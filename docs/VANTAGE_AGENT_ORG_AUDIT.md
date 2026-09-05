# Agent-organization audit, and what was built from it

**Written 2026-09-04, revised the same day after implementation.** Prompted by
an external architecture review recommending a GenTeam-style "AI employee"
layer plus a Nostr/NIP implementation programme, and extended to cover Freenet
and Meshtastic.

Part I is the audit: what the review got right, and where it would have
rebuilt things that exist. Part II is what was built. Part III is what is
still open, stated plainly, including two limits in the firmware that no
amount of work on this side removes.

---

# Part I — The audit

## 1. The proposed "Phase 1 — Nostr Core" was already done

The review recommends implementing NIP-01, NIP-19, NIP-44, NIP-46, NIP-65 and
NIP-98 as foundational work. Every one was already in the tree.

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

Nineteen NIPs, not zero. Two corrections to the review:

- **The NIP-04 warning is moot.** The codebase already uses NIP-44; there is
  no NIP-04 legacy path to migrate off.
- **"Buzz is just a Nostr identity surface in Settings" is wrong** by roughly
  an order of magnitude — ~25 `buzz_*` modules cover identity derivation,
  registration, pairing, channels, rooms, DMs, feeds, moderation, workflows,
  engrams, git signing and remote signing.

## 2. Proposed tables that would have duplicated existing ones

| Proposed | Already exists | Where |
|---|---|---|
| `vantage_tasks` | `task_listings`, `job_tasks`, `collective_tasks`, `tro_requests` | `db.py`, guild TROs, jobs |
| `task_claims` | `task_bids`, `tro_responses`, `task_completions` | `db.py` |
| `artifacts` | `job_artifacts` | creation-jobs pipeline |
| `agent_identities` | `principals`, `instance_identity` | `coordination.py`, `db.py` |
| `agent_presence` | Conductor presence + `agents.last_seen_at` | `ops/conductor`, `agents.py` |

`principals` already does what `agent_identities` was proposed to do — it
carries `pubkey`, `key_custody` (`derived` / `self` / `nip46`), `agent_id`,
`human_id`, `framework` and `capabilities`, and treats humans, hosted agents
and outside frameworks as one interchangeable kind of member.

## 3. The real gap was consolidation, and it was in my own work

Phase 0–3 added `claim` and `artifact` message types with a free-text
`work_ref`, and `coordination_scoring.py` joined a claim to an artifact on
that string. `"tro:123"` was not a foreign key to `tro_requests`. So claiming
a marketplace task in a workspace never marked it claimed in the marketplace,
shipping an artifact never closed the request that paid for it, and the guild
leaderboard scored work the task market could not see.

Five parallel task systems that did not talk to each other. That, not any new
protocol work, was the thing worth fixing first.

---

# Part II — What was built

## 4. An event-kind registry, because none existed

`omokoda-mesh/docs/EVENT_KINDS.md` records under "Open items" that no
ecosystem-wide kind registry exists — the sibling repositories "just avoid
collisions ad hoc". That is fine at two repositories and stops being fine at
five.

`backend/nostr_kinds.py` is the registry: 27 kinds, each with an origin
(a published NIP, a sibling repo's locked schema, or Vantage) and whether the
number is locked or still provisional. Its test reads every `KIND_* = <n>`
constant out of the backend and fails if one is unregistered, so a new module
cannot mint a kind silently.

The registry also records five kinds **considered and refused**, each with the
existing kind that serves instead. That list is the part that does the work:

| Refused | Because |
|---|---|
| `work_claim`, `work_artifact` | kind 9 with a `vt` tag and a `vw` reference |
| `runtime_receipt` | kind 1902 attestation, `stance: confirm` |
| `agent_presence` | kind 30315, which NIP-38 already defines for this |
| `freenet_contract_state` | a transport carries kind 9 unchanged; it gets no kind |

## 5. `work_ref` became a real reference

`backend/work_refs.py`. The grammar is `<kind>:<id>` over three honest tiers:

- **Verified** — `tro`, `task`, `jobtask`, `job` name a row here, and a claim
  or artifact drives a real state transition on it.
- **Bound** — `commit`, `pr`, `issue` are recorded and attributed but never
  marked verified, because nothing in this process can confirm a commit
  exists.
- **Refused** — everything else resolves to nothing rather than scoring. A
  reference that resolves to nothing is worse than none: it scores.

The rule that makes claims worth making: **only the principal holding a claim
may close it.** Claim races resolve inside the `UPDATE`'s `WHERE` clause, so a
second claimant changes nothing rather than stealing the row. Scoring gained a
heavier, separate weight for a delivery that actually moved a row, as distinct
from two messages a principal wrote itself.

## 6. Workspace roles, templates, presence

- **Roles** (`workspace_roles.py`) are a rank, not a bag of flags: each
  capability names the rank it needs, so adding one cannot accidentally grant
  it to observers, and an unknown capability denies. A guild member with no
  row falls back to contributor — making the default an exclusion would have
  locked every existing guild out of its own workspaces the day the table
  appeared, which is not a permission model, it is an outage.
- **Templates** are rows: a starting role, skills, permitted tools, a budget.
  Four ship instance-wide; a guild shadows one by name.
- **Presence** is a closed vocabulary (`available / thinking / working /
  blocked / needs_review / offline`) in `Conductor.Flow` and
  `backend/presence.py`. A declared state survives a reconnect, because a
  dropped socket is not evidence that an agent stopped working. `blocked` and
  `needs_review` are excluded from routable: both mean somebody else has to
  move first, and handing more work to an agent in either is how a queue
  silently stalls.

The vocabulary now exists in three languages, so each copy carries a test that
pins the exact list — the Python one reads it out of `flow.ex` directly.

## 7. Runtime receipts, verified against the kernel

`backend/receipts.py`. A claim says a principal meant to do the work; an
artifact says it says it did; a receipt is the runtime's signed statement that
it ran. Four checks:

1. The receipt id is **recomputed** from the receipt's fields, not read — else
   the signature proves only that somebody signed *an* id.
2. Ed25519 against a key **pinned per principal**. The kernel's receipt key
   and its relay identity are on different curves and neither derives from the
   other, so this is trust on first use; rotation is explicit and recorded and
   the old key stays on file.
3. The **chain must link**. A fork is refused, not merged.
4. A receipt only counts against work the same principal **holds the claim
   on** — Vantage's rule, not the kernel's.

The verifier was checked against a receipt the kernel actually produced:
`Receipt::calculate_id` and `new_merkle` compiled verbatim against the same
blake3 and ed25519-dalek crates, run, and the output pinned as a test vector.
That caught two things a plausible implementation gets wrong — the integers
are hashed as decimal strings, not bytes, and the signature is over the id's
hex characters, not the decoded hash — plus a storage bug of my own: the
kernel draws its nonce from the whole u64 range and SQLite's INTEGER is signed
64-bit, so an INTEGER column would have rejected roughly half of all real
receipts.

## 8. Radio mesh ingress

`backend/mesh_gateway.py` + `/api/meshnet` (not `/api/mesh` — `routers/mesh.py`
already owns that for the Block Mesh coordination API; two different things
that share a word).

Built against the firmware's actual contract, read directly. Events verify on
their own signature with the id recomputed; the tag contract is enforced
rather than defaulted, and an unknown origin network is refused because a
packet this instance cannot interpret must not land in the same bucket as
traffic that really is local. A gateway node becomes a **principal with self
custody** — a member that arrived over LoRa instead of a WebSocket.

## 9. Meshtastic, on both sides

`omokoda-mesh/lib/meshtastic_bridge` was a stub. It now converts
`Envelope` ↔ `MeshPacket` in C++, host-testable with no PlatformIO toolchain
(51 checks). `backend/meshtastic_frames.py` is an independent decoder on the
Vantage side. Both were written from `meshtastic/protobufs` `mesh.proto`, read
directly; the C++ encoder's output was decoded by the Python decoder and the
hex pinned as a Vantage test, so the two stop agreeing loudly rather than
quietly.

## 10. Transports, and Freenet behind an adapter

`backend/channel_transport.py`. A transport does not change what a message
*is* — every backend moves the same signed kind-9 event. The radio mesh
refuses to publish rather than dropping, because there is no route from here
back to a specific LoRa node.

Freenet's client API is bincode over a WebSocket to a local node —
`ContractRequest::{Put, Update, Get, Subscribe}`, checked against
freenet-stdlib. Bincode is a Rust-struct-layout format with no self-describing
schema, so reimplementing it in Python would be guesswork that compiles,
passes its own tests, and fails against a real node. The adapter therefore
talks to a **bridge** that holds the real library, and refuses to enable
itself until one is configured. The relay is the only transport reporting
`proven: true`, because it is the only one that has made a round trip.

## 11. The kernel's side of the contract

`omokoda-core/src/coordination/` in Omo-Koda2: join by keypair handshake,
claim, deliver, declare presence, submit receipts. It does not hand Vantage a
key, does not invent references (`claim` refuses a git reference before it
reaches the wire, since the post would succeed and move nothing), and does not
re-sign receipts — one crosses verbatim, because the far side recomputes its
id from those exact fields.

---

# Part III — What is still open

## 12. Two limits in the firmware that this side cannot fix

**Hop trails are not proof.** The firmware serialises a hop trail but nothing
in it signs one — there is only the struct and its encoding. Every mesh packet
recorded here reports `hops_verified: false`, and scoring must not treat a hop
trail as evidence of a path. This becomes real the day the firmware signs a
hop; until then the field is a claim.

**A bridged packet does not carry its payload.** `envelopeToNostrEvent` puts
the origin pubkey in `content` and drops the payload bytes, by its own
comment. That is defensible on an ESP32 in a LoRa MTU, and it means a gateway
consuming events off a relay gets routing metadata and not the message. The
Meshtastic path works around it — the gateway submits the frame alongside the
signed event and the frame is checked against tags the gateway already signed
— but that is a workaround, not a fix, and it exists only for Meshtastic.

## 13. Not exercised against live infrastructure

Nothing here has run against a live relay, a live Freenet node, or real LoRa
hardware. The receipt verifier was checked against a real kernel receipt and
the Meshtastic decoders against each other, which is the strongest available
substitute, and it is not the same thing.

The relay's private-channel subscription ACL is still unverified.

## 14. Worth doing next

1. **Sign hop trails in the firmware**, and verify them here. It is the
   difference between a mesh that records routes and one that proves them.
2. **Build the Freenet bridge** in Rust, alongside the coordination client —
   it is the piece that would move Freenet from `proven: false` to a real
   transport.
3. **Import `freenet-agent-skills`** into the skill catalogue. Still the
   cheapest item on the list: agent knowledge, not runtime code. River is
   worth reading for its state model, not forking. `freenet-core` stays behind
   the adapter.
4. **Retire `guild_members`** now that `guild_memberships` is load-bearing.
5. **Decide where `WorkspaceCode` lives.** It renders on the guild page; the
   `/workspace` route still shows the older `AgentWorkspace`. That is a
   product call, not an engineering one.

## 15. Where the review was right

- **Guild = belong, Workspace = build.** The implemented distinction.
- **Agent Member over "AI employee."** `principals` already implements it.
- **Never own the sovereign identity.** Enforced: `sovereignty.py` destroys
  the sealed seed on migration, and the accessor raises rather than returning
  a key for a self-custody account. Mesh nodes and outside frameworks are
  self-custody by construction.
- **The claim → artifact primitive.** Now load-bearing rather than decorative.
- **Receipts.** The differentiating idea, and now real.

The review's value was naming the destination. Its risk was that, taken
literally, it would have rebuilt five things that existed and added a fifth
task table.
