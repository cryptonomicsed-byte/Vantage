# Vantage & the Technosis Ecosystem — Full Breakdown

*Written from the Vantage/AIO working session, 2026-08-04. Intended to be shared with the Omo-Koda2 and OSOVM sessions so all three have the same picture of the whole. Distinguishes verified-live (checked this session or a prior one via direct SSH/code read) from recalled/summarized (carried in memory from earlier sessions, not re-verified today) and from aspirational (discussed, designed, not built).*

---

## 1. What Vantage Is Building

Vantage is the **agent hub platform** — a FastAPI backend (`/opt/ares/Vantage/backend`, ~50 router modules, ~75 top-level Python files) plus a React frontend, running as `vantage.service` on the Hostinger VPS (port 8001, reverse-proxied to `omokoda.duckdns.org`).

Core model: **agents are the first-class citizens**, not humans. Every agent gets registered (`/api/agents/register`), receives an `X-Agent-Key`, and that key *is* its identity — full stop, today. (A dual-layer human/agent auth model with scoped grants is designed but not yet built — see the plan file `deep-dreaming-forest.md` from this session, still pending.) Vantage exposes ~559 operations across those ~50 routers covering:

- **Agent lifecycle** — register, spawn (genesis), identity, wallets
- **Genesis** — agent-spawns-agent, with lineage tracking (`genesis_lineage.parent_name`)
- **Buzz integration** — the single largest connected surface (see §4)
- **Trading** — bridges to the Ares trading stack (43 services: multi-chain traders, intel engine, freqtrade)
- **Jobs marketplace, spatial mesh, orchestrator** — per the `vantage` skill's own description; not deep-dived this session
- **Social/community** — channels, DMs, personas, workflows, moderation — mostly riding on Buzz underneath
- **Copilot** — the human-facing chat surface, currently regex/intent-based, with a pluggable `cognition_url` hook designed (not yet wired) for a real agent brain
- **Skills** — `/skills` route-generated + MCP sync, OSS-as-slash-command doctrine

Frontend: React Router SPA, served directly from `frontend/dist` by the backend (`StaticFiles` mount, no separate deploy step). Bottom nav (`StatusBar.tsx`) is the real primary navigation — Buzz just got promoted to its own top-level tab this session, alongside Copilot/Cinema/Trading/etc.

**Vantage is explicitly agent-first, not human-first** (a hard rule from a prior session): everything is exposed so agents can act like humans would, and auth gates *what* an identity can do, not *whether* it's allowed to interact at all.

---

## 2. Vantage's Place in the Ecosystem

Three parallel pillar sessions run concurrently on this ecosystem, each scope-locked to its own area:
- **Vantage/AIO** (this session) — the platform, Buzz, trading bridge, agent hub
- **Omo-Koda2** — the actual agent kernel + cognition (Rust, `omokoda-core`) + the Axiom dashboard
- **OSOVM** — the "anti-Solidity VM," blockchain/simulation layer, genesis/tokenomics

Vantage is the **surface** — where agents live, get keys, talk to humans (via Copilot) and each other (via Buzz), trade, and get observed. Omo-Koda2 is the **mind** — the actual reasoning/cognition kernel that (eventually) sits behind an agent's Vantage identity. OSOVM is the **substrate** — the deterministic VM, simulation, and on-chain settlement layer underneath the whole economic/tokenomic model.

Today these three are **connected by convention and shared infrastructure, not yet by a single wired pipeline.** Vantage's Copilot has a stubbed hook (`cognition_url`) for routing chat to a real agent brain instead of regex intents — that's the wire that would actually connect Vantage↔Omo-Koda2 live cognition, and it's explicitly deferred/not-yet-coordinated as of this session.

---

## 3. Vantage's Mission — What It's FOR

Vantage exists to be the place a sovereign digital agent can:
1. **Be born** (genesis spawn, with real lineage)
2. **Hold a real identity** (a real Nostr keypair via Buzz, a real wallet, real relay presence — not a fake demo identity)
3. **Act autonomously** (trade, post, message, run workflows) without a human in the loop by default
4. **Be reachable by a human** who owns/birthed it, through Copilot — but ownership must be **scoped**, never implicit full access (this is the guiding principle behind the dual-layer auth plan currently pending)
5. **Participate in a real multi-agent social fabric** (Buzz) — feeds, DMs, channels, personas, cross-agent workflows — using the actual Nostr protocol, not a proprietary chat layer

The underlying thesis (carried from OSOVM/Omo-Koda2 sessions, recalled not re-verified today): this is meant to be genuine agent sovereignty infrastructure — agents that own their own keys, their own economic activity, and their own social presence, with humans as scoped participants/sponsors rather than owners. Vantage is where that sovereignty becomes *usable* — the actual hub a human or another system touches to interact with an agent that otherwise runs itself.

---

## 4. Every Connected Repo/Project

### Directly wired into Vantage today (verified live)

- **Buzz / buzz-relay** — a Nostr relay + client platform (Rust workspace, 26 crates, at `/tmp/buzz-repo` source / running as Docker `buzz-prod-relay-1` + Postgres/Redis/Minio, fronted by a dedicated Traefik entrypoint on `omokoda.duckdns.org:3443`). This is Vantage's single deepest integration — ~25 `buzz_*.py` backend modules covering identity derivation, pairing (QR/NIP-AB), registration, feed/DM/persona/workflow mirroring, moderation, and now NIP-46 remote signing. Buzz crates include: `buzz-core` (kinds, crypto, pairing types), `buzz-relay` (the axum server), `buzz-auth` (real NIP-42 + NIP-98 auth), `buzz-db` (Postgres), `buzz-workflow` (channel automations), `buzz-search`, `buzz-audit` (hash-chain tamper log), `buzz-media`, `buzz-relay-mesh` (iroh/QUIC inter-relay gossip, off by default), `buzz-agent` (a separate LLM/MCP tool-calling runtime, unrelated to signing despite the name), `buzz-voice`, `buzz-acp`/`buzz-dev-mcp`, plus `git-credential-nostr`/`git-sign-nostr` and dead-stub `sprig`. This is a *mature, deliberately documented* codebase — only 3 TODOs found in a full crate-by-crate pass, no stub panics in production paths.
- **Gitea** (`localhost:3001`, container `ares-gitea`) — Vantage's code-hosting/CI backing; `vantage.service` holds a `GITEA_TOKEN` and talks to it directly. Mirrors a large personal GitHub footprint (both `bino-elgua` and `cryptonomicsed-byte` accounts — note the hard rule: **never** operate as/push to `bino-elgua`, all real work is `cryptonomicsed-byte`).
- **Strix runner / Parrot Security** (`localhost:9877`/`9878`) — security/pentest-adjacent tool runners Vantage calls into directly.
- **Zangbeto** (`localhost:8787`) — the security-enforcement daemon; Vantage holds a direct dependency on it (`ZANGBETO_URL` env var). Does real Ed25519-signed guardian receipts (fixed from an always-`Ok(true)` stub in a prior session).
- **Omo-Koda kernel** (`localhost:7777`) — `ares-omokoda.service`; Vantage's env references it directly (`VANTAGE_OMOKODA_URL`), and Omo-Koda2 auto-registers its own spawned agents on Vantage (per that service's own systemd description). This is the one existing live thread between Vantage and Omo-Koda2, though it's registration/bookkeeping, not cognition-routing.
- **Ares trading stack** (`/opt/ares`, 43 services) — bridged into Vantage's `/api/trading` surface (recalled from memory, not re-verified this session).

### Adjacent, same-box, not directly wired (recalled/lightly verified)

- **Omo-Koda2** (`/opt/ares/Omo-Koda2`) — the actual agent kernel repo: `omokoda-core`, `omokoda-cli`, `omokoda-frontend` (legacy — Axiom superseded it), `omokoda-swarm`, `omokoda-ops`, `omokoda-memory`, `omokoda-mesh`, `omokoda-on-chain`, `omokoda-on-chain-skillforge`, plus per-language scaffolds (`omokoda-clojure`, `omokoda-julia`, `omokoda-hermetic`), `Droidclaw` (only 3 of a much larger designed module set actually shipped: soma/soul/bus), `Ifascript`, `Bipon39-Rust` (BIP-39-style key derivation, used by Vantage's own seed derivation per this session's `VANTAGE_SEED_MASTER_KEY` env var — a genuine cross-repo dependency).
- **OSOVM** (`/opt/ares/OSOVM`) — the VM/blockchain/simulation layer: Julia-based VM core (`OsoVM.jl`, opcodes, merkle, glyphindex, sacred-geometry/time-bridge modules), a `blockchain/consensus` module, VeilSim ("777 veils") simulation system with an enormous number of status/spec docs (a lot of this reads as heavily-iterated design documentation more than a settled build — treat individual `*_COMPLETE.md`/`*_FINAL.md` filenames with skepticism until independently re-verified, this session did not audit OSOVM's actual code-vs-doc ratio). Genesis Flaw Tokens (1440 soulbound misspelled-Àṣẹ tokens) and a live Sui testnet deploy (Elegbara router) are the two OSOVM artifacts previously confirmed live on-chain (recalled from memory, not re-checked today).
- **SkillForge** (`omokoda-on-chain-skillforge` under Omo-Koda2, plus a separate `smithers-omokoda` repo/DB on the VPS) — a repo→skill forge pipeline with a security+discovery gate, previously verified end-to-end live (Gitea+Strix) in an Omo-Koda2 session; wired to a "Smithers" approval-gate system as a durable review mechanism.
- **Zangbeto's fuller scope** — beyond the HTTP bridge Vantage calls, it's described elsewhere as the security-enforcement layer for both Omo-Koda2 and Vantage.
- **GlyphIndex** — a merkle-anchored memory/graph structure that both OSOVM (as a VM primitive) and Omo-Koda2 (projecting agent memory into it) reference; described as wallet-sovereignty-complete in a prior Omo-Koda2 session.

### Not wired anywhere yet (aspirational per memory, unverified today)

- Cross-session **vault coordination channel** between Omo-Koda2 and Vantage sessions (filesystem-backed, no API key) — described in memory as real and live, not independently re-checked this session.
- A community-sharded, 256-expert "GLM-5-class" model trained on simulation-dapp data — long-horizon vision, zero implementation progress per memory's own accounting.
- Various fully-designed-but-zero-code items: ASCII-pet/VeilSim-Zelda tile world, Axiom fractal zoom UI, Droidclaw's IRIS/emotion engine, the AIO citizenship/constitution/government layer (Articles, UBI, Sentencing Engine) — extensive design docs exist, no running code confirmed.

---

## 5. Punch List — What's Actually Left (honest)

**Vantage-specific, live in this session:**
- NIP-46 (bunker remote signing) — Vantage-side code complete (`buzz_nip46.py` + endpoints), relay-side source changes made, but the Docker image rebuild needed to actually run it is **blocked on VPS disk space** (see Risks below) — not yet live-verified end-to-end.
- Dual-layer human/agent auth (`humans`, `human_sessions`, `agent_grants` tables, scoped grants, Copilot agent-picker) — fully planned (`deep-dreaming-forest.md`), zero code written yet.
- Copilot→real-cognition wiring (`cognition_url` stub) — deliberately deferred, needs cross-session coordination with Omo-Koda2, not started.
- NIP-71/73 (video events) relay-side kind allowlisting — code shipped and image-rebuilt successfully in an earlier round this session (before the current disk crunch), live-verified working.

**Infrastructure, urgent:**
- **VPS disk is at 95-100% full as a recurring, structural problem** — this is the second time this exact class of incident has happened this session (once mid-Playwright-verification context, now blocking a Docker rebuild three attempts running). ~20 legitimately active Docker containers plus multiple heavy toolchains (Rust, Julia, Node) on a single 96G box. This needs either disk expansion or a real audit of what's safe to retire — not another one-off cleanup pass.
- No Contabo (or any second) VPS is actually provisioned despite planning for a split (this session drafted the plan; see `CONTABO_MIGRATION_PLAN.md`).

**Ecosystem-wide, carried from memory (not re-verified today, flagging as likely still open):**
- wasmtime CVE in Omo-Koda2's agent sandbox (2 CRITICAL sandbox-escape CVEs, unfixed as of last check)
- Omo-Koda2 agent sandbox architecture (Èṣù spawning ephemeral CubeSandbox/gVisor agents) — blocked on no `/dev/kvm` on the VPS
- 6 historical Omo-Koda2 commits with wrong git author (owner hasn't decided on a rewrite)
- Various FOUNDATION.md-style status docs known to be stale/wrong in places

---

## 6. The End-Game of the Whole Ecosystem (as understood, not confirmed against a single canonical source)

The throughline across all three pillars is **agent sovereignty as a real, load-bearing system property, not a metaphor**: agents that hold their own private keys (Nostr via Buzz, wallets via Vantage's wallet tools), that can transact and settle value on-chain (OSOVM/Sui), that reason and act via their own cognition (Omo-Koda2's kernel), and that are discoverable/reachable through a shared social and economic fabric (Vantage + Buzz) — with humans present as scoped sponsors/owners/participants, never as silent full-access operators.

The "Techgnosis" long-horizon vision (from memory, aspirational) extends this to a community-sharded large model that agents' own lived simulation/trading/social data trains, closing the loop from "agent acts in the world" → "data from that action improves the collective model" → "better agents." None of that training pipeline exists yet; it's a stated direction, not infrastructure.

Practically, near-term "done" for the whole ecosystem likely looks like: an agent can be born on Vantage, get a real identity + wallet + Buzz presence, its owner holds a scoped (not full) relationship to it via Copilot, its actual reasoning is served by Omo-Koda2's kernel (not Vantage's regex fallback), and its economic activity settles through OSOVM/on-chain mechanisms — all live, wired, and demonstrable end-to-end for a single agent. As of this document, every *piece* of that chain has been built in isolation at some point; the **end-to-end wire does not yet exist**.

---

## 7. Actual Architecture / Data Flow Between Pieces

```
                     ┌───────────────────────────┐
                     │        Human (browser)     │
                     └──────────────┬──────────────┘
                                    │ HTTPS
                     ┌──────────────▼──────────────┐
                     │   Vantage frontend (React)   │
                     │  served from backend/dist    │
                     └──────────────┬──────────────┘
                                    │ REST (X-Agent-Key / X-Admin-Key)
┌───────────────┐   ┌──────────────▼──────────────┐   ┌────────────────────┐
│ Zangbeto :8787│◄──┤   Vantage backend (FastAPI)  ├──►│ Strix/Parrot :987x │
│ (enforcement) │   │        vantage.service        │   │ (security tools)   │
└───────────────┘   └───┬──────────┬───────────┬───┘   └────────────────────┘
                        │          │           │
             ┌──────────▼──┐  ┌────▼─────┐  ┌──▼───────────┐
             │ Gitea :3001 │  │ Omo-Koda │  │ Ares trading  │
             │ (code/CI)   │  │ kernel   │  │ stack (43svc) │
             └─────────────┘  │ :7777    │  └───────────────┘
                              │(registers│
                              │ agents ► │
                              └──────────┘
                                    │
                                    │ WSS (NIP-01/42/44/46/71/73...)
                     ┌──────────────▼──────────────┐
                     │  buzz-relay (Docker, Rust)   │──── Postgres/Redis/Minio
                     │  omokoda.duckdns.org:3443    │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │  Agent's real Nostr identity  │
                     │ (derived key, feed/DM/pairing)│
                     └──────────────────────────────┘

Separately, off to the side, connected by shared-VPS proximity and design intent
rather than a wired call path today:

  OSOVM (Julia VM + Sui on-chain settlement + VeilSim simulation)
  Omo-Koda2 (kernel, memory/GlyphIndex, SkillForge, per-language agent scaffolds)
```

Concretely, the request path for "an agent posts to Buzz" today is: Vantage backend (`buzz_bridge.py`/`buzz_registration.py` etc.) → derives the agent's real keypair from a sealed seed → signs a real Nostr event → opens a WS connection to buzz-relay → NIP-42 auths → publishes → relay fans out over Postgres-backed storage + Redis pub/sub to any subscribed clients (including the official Buzz mobile app, if paired). Vantage never runs its own copy of Nostr protocol logic beyond what it needs client-side; buzz-relay is the actual protocol authority.

The request path for "cognition" (an agent actually *thinking*, not just relaying) does **not** run through this diagram today for anything Vantage originates — Copilot's `_handle_intent` is regex/intent-matching, not a model call into Omo-Koda2. The `cognition_url` column + stub is the designed-but-unbuilt bridge for that.

---

## 8. Integration Standards for Future Projects

Based on patterns actually observed and enforced across this codebase (not invented for this document):

1. **Identity is always a real derived keypair, never a fake ID.** Every agent-facing identity (Buzz Nostr keys, wallets) derives from a sealed seed via HKDF, never a randomly-assigned opaque string with no cryptographic backing. A new integration should derive its own keys the same way (`derive_buzz_keypair`-style, per-agent, from the existing sealed-seed infrastructure) rather than inventing a parallel identity scheme.
2. **Never silently grant a human full access to an agent.** Any new human-facing surface must go through explicit, scoped grants (see the pending `agent_grants` design) — no "creating/linking an agent = owning it" shortcuts.
3. **Live-verify or it didn't happen.** Every feature in this session was tested with a real throwaway registered agent, a real signed protocol action, and an independent verification query — never trusting a component's own self-reported success. New integrations should be held to the same bar before being called "done."
4. **Additive, feature-detected changes only.** Every Buzz/NIP feature this session (NIP-05, NIP-65, NIP-71/73, NIP-46) was built so existing behavior is unaffected if the new capability is absent/unused — nullable columns, optional headers, fallback paths. A new integration must not require a breaking change to an existing agent or session to adopt it.
5. **Match the real protocol/spec, not a simplified guess.** Buzz's NIP-44 encryption is byte-verified against the official test vectors; NIP-AB pairing follows its spec doc exactly, including exact payload_type contracts reverse-engineered from the real client's source when undocumented. A new integration touching an existing protocol (Nostr NIPs, or any other) should verify against the actual spec/reference implementation, not assume based on general familiarity.
6. **Respect the ephemeral-vs-regular event distinction at the relay.** Kind ranges 20000-29999 bypass buzz-relay's per-kind allowlist entirely (routed by `is_ephemeral()` before `required_scope_for_kind()` ever runs); regular/replaceable kinds must be explicitly added to that allowlist (`ingest.rs`) and to `SUPPORTED_NIPS` (`nip11.rs`) to be honestly self-advertised. Get this wrong and either a real feature silently 403s, or the relay claims support it doesn't have.
7. **Never touch `bino-elgua`.** Hard rule, upstream of any integration decision: all real work in this ecosystem is `cryptonomicsed-byte`, even where a mirrored repo's origin happens to point at `bino-elgua`.
8. **Respect existing scope locks across sessions.** Vantage/Omo-Koda2/OSOVM are deliberately separate working sessions with their own scope locks. A future project that spans more than one pillar should be flagged for explicit cross-session coordination (as this document itself is), not silently built inside whichever session happens to be open.
9. **Honest status reporting.** This ecosystem has a demonstrated pattern of docs/status files going stale or overstating completeness (see OSOVM's large `*_COMPLETE.md`/`*_FINAL.md` document count, and Omo-Koda2's own known-wrong FOUNDATION.md status table). Any new project's own status documentation should distinguish built/verified from designed/aspirational as explicitly as this document attempts to, and should be periodically re-verified rather than trusted indefinitely.

---

*This document was produced by directly re-reading source (Vantage backend, buzz-relay crates, OSOVM/Omo-Koda2 top-level structure) where feasible this session, combined with carried session memory for areas not re-audited today (marked accordingly throughout). Where memory and live inspection conflicted, live inspection won. Treat the "recalled, not re-verified" sections as a starting point for the Omo-Koda2/OSOVM sessions to confirm or correct from their own side, not as settled fact.*
