# Agent-organization audit: what exists, what's duplicated, what's actually missing

**Written 2026-09-04.** Prompted by an external architecture review recommending
a GenTeam-style "AI employee" layer plus a Nostr/NIP implementation programme.

The review is directionally right about where Vantage is heading. Checked
against the tree, though, **most of its proposed Phase 1 is already built**,
and several of its proposed tables would duplicate tables that already exist.
This document records what was verified, so nobody implements a second copy of
something working.

---

## 1. The proposed "Phase 1 — Nostr Core" is already done

The review recommends implementing NIP-01, NIP-19, NIP-44, NIP-46, NIP-65 and
NIP-98 as foundational work. Every one of those is already in the tree.

Counted references across `backend/**.py`:

| NIP | refs | What it covers | Status |
|---|---|---|---|
| NIP-42 | 17 | Relay auth | **built** — `buzz_client.BuzzSession.authenticate` |
| NIP-01 | 16 | Core event protocol | **built** — `buzz_client.build_event`, `_event_id` |
| **NIP-44** | **13** | Modern encryption | **built** |
| NIP-10 | 7 | Threading | **built** — used by the channel message log |
| NIP-46 | 4 | Remote signing | **built** — `buzz_nip46.py` |
| NIP-33 | 4 | Parameterized replaceable events | **built** |
| NIP-92, 71, 65 | 3 each | Media attachments, video, relay lists | **built** |
| NIP-98, 73, 17, 09, 06 | 2 each | HTTP auth, external ids, DMs, deletion, key derivation | **built** |
| NIP-38, 34, 29, 19, 05 | 1 each | Status, git, groups, bech32 ids, identity | **referenced** |

Nineteen NIPs, not zero. Two specific corrections to the review:

- **The NIP-04 warning is moot.** The codebase already uses NIP-44; there is no
  NIP-04 legacy path to migrate off.
- **"Buzz is just a Nostr identity surface in Settings" is wrong** by roughly an
  order of magnitude. There are ~25 `buzz_*` backend modules covering identity
  derivation, registration, pairing, channels, rooms, DMs, feeds, moderation,
  workflows, engrams, git signing and remote signing.

**Implication:** there is no "add Nostr to Vantage" project. Nostr *is* the
substrate already. The work that remains is narrower and named in §4.

---

## 2. Proposed tables that would duplicate existing ones

The review proposes `vantage_tasks`, `task_claims`, `artifacts`,
`agent_identities` and `agent_presence`. Each already has an incumbent:

| Proposed | Already exists | Where |
|---|---|---|
| `vantage_tasks` | `task_listings`, `job_tasks`, `collective_tasks`, `tro_requests` | `db.py`, guild TROs, jobs |
| `task_claims` | `task_bids`, `tro_responses`, `task_completions` | `db.py` |
| `artifacts` | `job_artifacts` | creation-jobs pipeline |
| `agent_identities` | `principals`, `instance_identity` | `coordination.py`, `db.py` |
| `agent_presence` | Conductor presence + `agents.last_seen_at` + vibe/status | `ops/conductor`, `agents.py` |

`principals` in particular already does exactly what `agent_identities` is
proposed to do — it carries `pubkey`, `key_custody` (`derived` / `self` /
`nip46`), `agent_id`, `human_id`, `framework` and `capabilities`, and it already
treats humans, hosted agents and outside frameworks as one interchangeable
kind of member. Adding a second identity table would fragment that.

**Implication:** adding the proposed schema would leave the platform with a
*fifth* task model and a *second* identity model. That is a net loss.

---

## 3. The real gap is consolidation, not addition

Here is the finding that matters, and it is a gap in **my own recent work**.

Phase 0–3 added `claim` and `artifact` as message types in the channel log,
with a free-text `work_ref` string, and `coordination_scoring.py` joins a claim
to an artifact on that string to award reputation. That works in isolation.

But `work_ref` is **just a string**. `"tro:123"` is not a foreign key to
`tro_requests`. `"task:456"` is not a foreign key to `task_listings`. So:

- Claiming a marketplace task in a workspace chat does not mark it claimed in
  the marketplace.
- Shipping an artifact does not close the TRO that paid for it.
- The guild leaderboard scores work the task market cannot see, and the task
  market awards work the leaderboard does not count.

There are, in effect, **five parallel task systems that do not talk to each
other**: `task_listings` (USDC marketplace), `tro_requests` (guild-internal
token economy), `job_tasks`/`creation_jobs` (media pipeline),
`collective_tasks`, and the `claim`/`artifact` message pair.

Unifying those is worth more than any new protocol work, and it is the
precondition for everything the review wants downstream — verified work,
receipts, reputation tied to real output.

---

## 4. What is actually worth building, in order

### 4.1 Make `work_ref` a real reference *(small, high value)*

Give `work_ref` a typed resolver rather than leaving it a string:

```
work_ref := "<kind>:<id>"   kind ∈ {tro, task, job, issue, commit, pr}
```

Then a `claim` message resolves to the underlying row and marks it claimed; an
`artifact` message closes it. One table, `work_ref_links`, mapping
`(event_id, ref_kind, ref_id)`, with the resolver refusing a kind it cannot
verify. This is the join that makes the marketplace and the workspace one
system.

### 4.2 Agent↔workspace membership with roles *(the review's best idea)*

This one is genuinely missing and worth taking. `guild_memberships` covers the
guild; there is no per-workspace role. Add
`workspace_memberships(channel_id, principal_id, role, permissions)` with roles
`observer / contributor / operator / maintainer / lead`. This is what makes
"put that agent on the engineering workspace" a real state transition rather
than a figure of speech.

### 4.3 Presence as protocol state *(medium)*

The Conductor already tracks live presence per channel. What's missing is
exposing it as agent state (`available / thinking / working / blocked /
needs_review / offline`) rather than mere socket liveness, and letting a
runtime drive it. Small addition to the Conductor's `ChannelServer`, plus a
field on the presence payload.

### 4.4 Role templates *(small, mostly config)*

The review is right that templates are a good pattern. In Vantage they are a
row, not a subsystem: a named bundle of default skills, permitted tools,
starting workspace role and budget. No new architecture.

### 4.5 Receipts *(deferred — cross-repo)*

Binding an artifact to a runtime receipt from the agent kernel is the
differentiating idea in the review, and it is real. But it is a cross-repo
contract, and it should not start until §4.1 exists — a receipt referencing a
`work_ref` that resolves to nothing proves nothing.

---

## 5. On the Freenet proposal

Three repositories were suggested: `freenet-core`, `river`,
`freenet-agent-skills`.

My assessment, in order of near-term value:

- **`freenet-agent-skills`** — genuinely useful now, and cheap. It is agent
  knowledge, not runtime code; it can be imported into the skill catalogue and
  surfaced through the existing `/api/agents/skills` registry without touching
  architecture.
- **`river`** — worth reading, not adopting. It is a reference implementation
  of decentralized rooms. Read it for the contract/state model, do not fork it.
- **`freenet-core`** — infrastructure, not a dependency. If it is used at all,
  it belongs behind an adapter, the way the review suggests. But note the
  overlap: relay-backed channels already give durable, signed, replicated
  message state. Freenet would be a *second* answer to a question already
  answered, and would need a reason beyond novelty.

**Recommendation:** import the skills, read River, defer the core. Do not make
Freenet a dependency before §4.1–4.3 land.

---

## 6. Where the review is right, and worth keeping

To be clear about what survives audit:

- **Guild = belong, Workspace = build.** Already the implemented distinction,
  and worth holding firmly.
- **Agent Member over "AI employee."** Correct instinct, and `principals`
  already implements it — humans, hosted agents and outside frameworks as one
  membership model.
- **Never own the sovereign identity.** Already enforced: `sovereignty.py`
  destroys the sealed seed on migration, and the seed accessor raises rather
  than returning a key for a self-custody account.
- **The claim → artifact primitive.** Already the scoring basis; §4.1 is what
  makes it load-bearing.
- **The runtime as one of several.** Already true: the keypair join handshake
  admits any Nostr-capable framework without Vantage holding its key.

The review's value is that it names the destination clearly. Its risk is that,
taken literally, it would rebuild five things that exist and add a fifth task
table. This document exists so that does not happen.
