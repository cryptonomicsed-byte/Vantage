# Vantage Pillar's Canonical Ecosystem Repo/Project List

*Companion to `VANTAGE_ECOSYSTEM_OVERVIEW.md`. Compiled 2026-08-04 from direct VPS/local investigation plus the shared Claude-Codex memory vault (`/opt/ares/Vantage/data/memory_vaults/Claude-Codex/`). Legend: ✅ live/built · 🔧 built, partial · 🔜 designed only · ❓ unclear/unverified.*

---

## 0. The "1:1 World" Concept — Resolved

The flagged "drone-hive 1:1 mapping" memory item is now traced to its source: **`OSOVM_CODEX.md` §26/§27a/§30e-g**, owner's own words: *"use GPUs to eventually create a 1:1 mapping of the world for agents to sim in; gather images from ALL devices in the ecosystem; use open-source world maps for base caching; devices just fill in the new data; store it all in Walrus blobs."*

Two genuinely distinct concepts share the "1:1" label — do not conflate them:

1. **The 1:1 real-world twin** (§26/§30e) — crowd-sourced device vision (phones, and per §27a's dimos discussion, robot/drone SLAM as "mobile mapping units") builds 3D Gaussian Splatting submaps ("blobs"); blobs fuse via collaborative multi-agent SLAM into one living reconstruction of real, bounded zones (campus/warehouse/block scale — explicitly **not** planet-scale, the owner's own "honest caveats" say so). Base layer = open-source maps (OSM-class) as procedural ground truth; devices fill only the delta. Stored in Walrus blobs, content-addressed, each blob a new mineable PoSim job type (Hivemapper/DePIN-style), scored on coverage/novelty/geometric consistency. **Hard rule: reconstruction ≠ generation** — NVIDIA Cosmos-style generative world models can never touch this (unverifiable, would hallucinate wrong geometry); Cosmos is training-side augmentation only, never the proof side. Revenue model: sell the accumulated twin data, pay the contributing device/fleet owners, routed through Èṣù's 3.69% tithe router — a genuine DePIN flywheel. Biggest honest risks flagged by the owner: SLAM fusion is the hard research problem, privacy/legal (faces, plates, interiors — GDPR/BIPA) is bigger than the tech, and fusion/rendering compute is centralized (data-center GPU), which cuts against the sovereign/edge ethos. **Status: fully specified, zero implementation.** Phase-3+, explicitly sequenced behind VeilSim's cross-machine determinism proof (already closed) and behind Julia/OSOVM↔Omo-Koda2 wiring (not yet done).
2. **1:1 *training* worlds** (`WorldGenerator.jl`, referenced in the same sweep but a separate idea) — per-agent *simulated* worlds (not a real-world twin) for training a specific agent's policy before it embodies. Also pure sketch, not implemented, explicitly gated behind the same determinism proof.

The "drone" framing traces to a real owner transcript exchange (`convo-1a475350-part15.md`): the owner asking whether the first proof-of-concept job should be "a drone going from A to B" vs. a much simpler "phone handshake between two agents/devices." **This was decided**: per `ecosystem-capstone-2026-07.md` §39, the seed job is the **phone handshake** (lower-risk, proves the receipt/escrow/settlement loop first); "drone A→B = merge sim+real, LATER" — explicitly deferred, not the first job. So: the drone-hive 1:1-mapping concept is real, fully designed, and correctly deferred — not an unresolved ambiguity, just a not-yet-built phase-3+ roadmap item that had never been traced back to its source doc before.

---

## 1. The Core Three Pillars (live, load-bearing)

| Repo | Role | Status |
|---|---|---|
| **Vantage** | Agent hub / employment platform — auth, Buzz integration, trading bridge, skills registry, Copilot | ✅ live, `vantage.service` |
| **Omo-Koda2** (`omokoda-core` + siblings) | Agent kernel — cognition, memory, identity, wallet tools | ✅ live, `:7777` |
| **OSOVM** | Deterministic proof/settlement VM — PoSim, Genesis Flaw Tokens, Sui settlement | ✅ live (Elegbara router on Sui testnet) |

## 2. The Live Agent-Birth Pipeline (confirmed, five stages, all real)

```
BIPON39 (soul seed / entropy → keys)
   └─► IfáScript (Odù cast → archetype, orisha, taboos)
          └─► Koodu (day-state → resonance, Sabbath)
                 └─► Cloakseed (identity mask, cloak, duress, wallet)
                        └─► Omo-Koda2 (birth → think → act → receipt)
```
- **`BIPON39-Rust`** (under Omo-Koda2) — BIP-39-style mnemonic/entropy → key derivation. ✅ live; also the pattern Vantage's own `VANTAGE_SEED_MASTER_KEY`-driven seed derivation follows.
- **`Ifascript`** (under Omo-Koda2) — 256-Odù casting engine; produces archetype/orisha/taboo assignment from the seed. ✅ live.
- **`Koodu`** (`/opt/ares/Koodu`) — daily ritual-codex/day-state module (resonance, Sabbath blocking). ✅ live.
- **`vanity-cloakseed`** (`/opt/ares/vanity-cloakseed`) — identity mask/cloak/duress/panic-phrase + wallet generation. ✅ live. Note a real, previously-confirmed distinction: the standalone JS app version of this (6.8K lines, running on `:8778`) is wired to the **Ares command center** (`ares_vanity_bridge.py`), *not* to agent birth — don't conflate the two deployments of similar functionality.
- **Terminus: Omo-Koda2** — birth → think → act → receipt. Not a separate contributing repo, the destination of the pipeline.

**No sixth unnamed repo was found feeding this pipeline.** The four named repos plus Omo-Koda2 as terminus is the complete, confirmed live chain per the vault's own canonical write-up (`convo-1a475350-part09.md`).

## 3. Directly Wired to Vantage (this session's own verification)

- **Buzz / buzz-relay** — 26-crate Nostr relay+client workspace. ✅ (see `VANTAGE_ECOSYSTEM_OVERVIEW.md` §4 for the full crate list.)
- **Gitea** — self-hosted git, 161 mirrored GitHub repos across `cryptonomicsed-byte` + `Bino-Elgua` accounts.
- **Strix runner, Parrot Security** — security/pentest tool runners.
- **Zangbeto** — security-enforcement daemon.
- **Ares** — trading stack (43 services), bridged via `/api/trading`.
- **SkillForge** (`omokoda-on-chain-skillforge`) + **Smithers** (`smithers-omokoda`) — repo→skill pipeline + durable human-approval gate.

## 4. Not Yet Reconciled Into the Three-Pillar Structure — Parallel Hermes-Driven Projects (NEW, this session, local machine)

Discovered on this local Mac (`~/`), all created within the last day or two, all real (not empty scaffolds), all genuinely Buzz-adjacent in subject matter but not yet wired into any of the three pillars:

- **`~/Buzz-swarm`** ("BuzzAgent Mesh") — an agent-native ReAct orchestrator + Nostr peer-keypair engine + MCP tool hub + multi-agent "Quality Loop" workspace, explicitly described as "complementary to Block's Buzz." React/TS/Vite/Bun stack, Gemini API dependency.
- **`~/bondhive`** ("Bondhive") — "a reliability layer for the agent economy": staked, attested Service Bonds (NIP-74, kinds 37000-37005) settled on Solana, with Buzz workspaces as the reference deployment. Explicitly self-labeled **design-stage, nothing live, no tokens minted.** Rust workspace (core/buzz/oracle/program/web crates).
- **`~/Iranti`** ("Ìrántí" — Yoruba for memory/recollection) — a memory-mesh companion to Buzz specifically targeting Buzz's NIP-AE encrypted engrams (kind 30174), adding recall (BM25+recency+salience ranking), agent-to-agent memory grants, a "dream cycle" reflection digest, and cross-agent "hive resonance" — framed explicitly as filling gaps NIP-AE "deliberately leaves to companion NIPs." Rust + MCP tool surface.

**These are flagged, not built on or investigated further, per instruction.** All three read as genuine, timely, Buzz-ecosystem-relevant work — likely worth reconciling into the main structure once their owner/session context is clearer (they may be from a parallel Hermes-driven session not yet coordinated with any of the three pillars documented here).

## 5. Everything Else Known to Be Part of the Ecosystem (recalled from vault + memory, status as last known — not re-verified live this session unless noted)

**Omo-Koda2-adjacent / kernel reference/inspiration (patterns ported, not live dependencies):**
- **Claw-code** — mature Rust agent runtime; source of ported patterns (session persistence, permission-tier mapping, sandboxed bash exec, hook system). Reference only.
- **Claude-2** — TypeScript/Claude-Code-shaped agent harness; source of conceptually-ported patterns (async-generator loop, 5-level context compression, 7-layer safety stack, process-based sub-agents). "Patterns only," explicitly not mirrored source.
- **Swibe** (`/Users/bino/Swibe`) — the sprawling ancestor agent-native language (v3.4, 44 backends) that both Omo-Koda2's 3-primitive design and OsO/Oso-Aether trace lineage from. Has NO pet code itself — confirmed distinct from Oso-Aether.
- **OsO** (`/Users/bino/OsO`) — Phase-1 MVP pet/companion system, Python translator + Rust core.
- **Oso-Aether** (`/Users/bino/Oso-Aether`) — the evolved, Swibe-independent pet companion: Rust→WASM, 86-DNA lineage, ASCII renderer, Tier 0-5 ladder, Sui dNFT (`pet.move`), Walrus memory. **Locked as the actual micro-face pet companion** for the Zelda-tile-world spec (§30d) — not Swibe.
- **`organism-core`** — the intended runtime↔chain "nervous system" bridging Sui/Move (OSOVM's ÀṢẸ) to the live kernel's Dopamine/Synapse. **Confirmed to be in-process simulation only, not a live bridge** — the single most consequential stub in the whole ecosystem per the vault's own assessment.
- **`Nex`** (graph-reasoning engine, `:18789`) — built, confirmed **not wired** to the Omo-Koda2 kernel.
- **`omokoda-sui`** (with `synapse.move`, `agent_registry.move`, `memory_vault.move`) — the kernel's own intended on-chain layer, separate from OSOVM's ÀṢẸ token. Per the canonical roadmap this is "Phase 8 — Future," **spec only, not built.**
- **`dimensionalOS/dimos`** — third-party open-source robotics SLAM/spatial-memory stack; owner's build target for the 1:1-twin mapping fleet (see §0). Investigated/cloned for assessment; robots running it become "mobile mapping units." Not itself an ecosystem-authored repo.
- **`Droidclaw`** — only 3 modules shipped (soma/soul/bus); IRIS response-router, emotion engine, 9-language "Orisha distribution" all unbuilt design.

**OSOVM-adjacent:**
- **ScarabSwarm + Witness firmware** — PoSim reference architecture triangle with OSOVM, two separate verification regimes.
- **larql / zerolang** — model-as-database query language + graph-query layer over GlyphIndex; real, built, running on VPS. GlyphIndex's merge/merkle/audit features deferred behind a `larql-glyph` pin bump.
- **aio-sui** — the AIO constitution/citizenship Move contracts (includes the Sentencing Engine spec).

**Adjacent/legacy, part of a 17-directory local inventory, worth knowing about but not active:**
- `knowledge_surgeon` (REM-memory prototype), `paradigm` (claims unverified), `Twelve-thrones`, `Npc-forge`, `veriwiki`.

**Third-party candidates evaluated but not adopted (research-only):**
- `1jehuang/jcode` — lightweight in-sandbox agent runtime candidate (assessed better fit than `oh-my-pi` for the CubeSandbox worker slot, once unblocked).
- `oh-my-pi` — full IDE-grade coding-agent harness; confirmed a locally-cloned third-party reference, not production stack.
- `aaif-goose/goose` — evaluated for its ACP reply pattern (cleaner than Buzz/omokoda-acp's current CLI-shell-out approach); not yet adopted.
- Virtuals Protocol (`agent-commerce-protocol`, `acp-node-v2`, `acp-cli`, `acp-x402-server`, `bondv5-trader`) — in-progress audit, unconcluded, as a possible bonding-curve agent-tokenization layer.

**Design-only / mythology-and-governance layer (no running code):**
- AIO Constitution / government layer (10 Articles, citizenship tiers, UBI, Sentencing Engine, robots-as-citizens liability model).
- EL-GUÀ Console / n8n "Temple Nervous System" — has 4 unreconciled versions of a core "24 Òrìṣà" list, unsettled.
- ASCII-pet/VeilSim-Zelda tile-world UI, Axiom fractal-zoom UI — designed, not built beyond what's noted live above.

---

## 6. Open Questions / Flags for the Owner and Peer Pillars

- The three new Hermes-driven local projects (§4) genuinely look like they should eventually be folded into the Buzz/Vantage integration surface (Bondhive rides on Buzz workspaces + NIP-74; Iranti extends Buzz's own NIP-AE; BuzzAgent Mesh is explicitly "complementary to Buzz") — but none are wired to anything today, and it's unclear which session/owner context spawned them. Worth a direct decision on ownership/reconciliation.
- `organism-core` being simulation-only, not a live bridge, is arguably a bigger structural gap than anything flagged in the individual pillar punch lists — it's the thing that would actually connect OSOVM's ÀṢẸ token economy to Omo-Koda2's live Dopamine/Synapse kernel state, and as far as this document can tell, nobody has it as an active work item.
- The 1:1-world/drone-hive concept (§0) is fully specified and correctly sequenced behind determinism proofs already closed elsewhere — it may be worth explicitly re-flagging as "ready to scope, if desired" now that the ambiguity about what it even meant is resolved, rather than leaving it purely dormant.

---

*Compiled by the Vantage-side session, 2026-08-04. Cross-check against Omo-Koda2's and OSOVM's own equivalent lists (requested in parallel) — this is one pillar's honest view, not an arbitration of the whole ecosystem's inventory.*
