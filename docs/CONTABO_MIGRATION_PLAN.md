# Hostinger → Contabo Split: The Real Migration Plan (v2, supersedes v1)

**Correction notice:** the original `CONTABO_MIGRATION_PLAN.md` (drafted earlier this session) assumed the opposite split — Vantage's own web/API tier moving off Hostinger. That was wrong. **Confirmed direction:** Vantage and its direct dependencies stay on Hostinger, untouched, zero migration. What moves to the new Contabo VPS (`89.117.74.224`, 6vCPU/12GB/200GB, Ubuntu, SSH alias `contabo-vps`, confirmed reachable and near-empty — 6.6G/193G used) is: **the Omo-Koda2 kernel ecosystem** (frontend, Julia memory service, Obatala/Clojure, Oya, Elixir swarm), **the Ares trading stack** (43 services), and **Gitea**.

This document is Vantage's side of a three-way convergence — Omo-Koda2 is drafting the kernel-ecosystem side, OSOVM is assessing `organism-core`'s bridge impact. Nothing executes until all three agree.

---

## 1. What actually changes for Vantage

Vantage's own code, database, and systemd service (`vantage.service`) do not move and are not touched by this migration. What changes is that three of its five `localhost`-only dependencies become cross-VPS calls:

| Dependency | Today | After migration | Verdict |
|---|---|---|---|
| **Gitea** (`GITEA_URL=http://localhost:3001`) | same-host HTTP | cross-VPS HTTP | **Becomes cross-VPS — needs tunnel** |
| **Omo-Koda kernel** (`VANTAGE_OMOKODA_URL=http://localhost:7777`) | same-host HTTP | cross-VPS HTTP | **Becomes cross-VPS — needs tunnel** |
| **Ares trading stack** (`ARES_RPC_PROXY=http://localhost:9861`, wallet service `:8778`) | same-host HTTP | cross-VPS HTTP | **Becomes cross-VPS — needs tunnel, highest stakes (real capital)** |
| Strix runner (`:9877`), Parrot Security (`:9878`) | same-host HTTP | **unchanged** | Not part of the announced move — stays on Hostinger |
| Zangbeto (`:8787`) | same-host HTTP | **unchanged** | Not part of the announced move — stays on Hostinger |

**Confirmed via direct code read, not assumption:**
- Gitea: used by `routers/code.py`, `routers/intel.py`, `routers/production.py`, `config.py`. Notably, `code.py` already has a fallback `GITEA_URL = settings.GITEA_URL or "http://2.25.70.156:3001"` — i.e., a hardcoded plaintext fallback to Hostinger's *public* IP already exists in the code. This is exactly the anti-pattern to avoid repeating for Contabo: the real `GITEA_TOKEN` is sent as a bearer header on every request. If `GITEA_URL` is simply repointed to `http://89.117.74.224:3001` with no tunnel, that's a real API token crossing the public internet in plaintext on every Gitea call Vantage makes. **This alone is sufficient justification for a private tunnel, independent of the trading-capital argument below.**
- Omo-Koda kernel: used by `agents.py` (auto-registration bookkeeping), `mind_link.py`, and **`routers/omokoda_cognition_proxy.py`** — a real, already-built HTTPS proxy (added 2026-07-29, found during this audit, not previously known to this session) that lets Buzz's workflow engine reach the kernel's `/v1/cognition` webhook. It exists *specifically* because Buzz's workflow engine has a hardcoded SSRF guard (`buzz_core::network::is_private_ip`) that rejects loopback/private IPs — so Vantage's own public HTTPS front door proxies the call through instead. **This proxy's forwarding target is currently `http://localhost:7777` and must be repointed to wherever the kernel lands on Contabo** — this is a concrete, mechanical one-line-ish change, but a real one, and the single most important line to get right in this whole migration, since it's the one path external systems (via Buzz) actually depend on.
- Ares trading: `trading.py` makes direct HTTP calls to `http://127.0.0.1:9861` (Ares RPC proxy — Solana RPC, CoinGecko) and shells out to `http://127.0.0.1:8778` (a wallet-creation service, confirmed elsewhere to be the standalone `vanity-cloakseed` JS app wired to the Ares command center). **This is real trading capital's execution path** — not a bookkeeping/read-only integration like the other two.

## 2. Two Hostinger-hosted directories that are Vantage's own — explicit call, not left ambiguous

Per your flag: `worldmonitor/` (2.5G) and `video-engine/` (1.6G) on Hostinger are confirmed mine. Investigated both against Vantage's own code before deciding:

- **`video-engine` stays on Hostinger with Vantage.** `routers/video_studio.py` has a **hard filesystem dependency** — it shells out to `/opt/ares/video-engine/node_modules/.bin/hyperframes` and reads `/opt/ares/video-engine/remotion-templates` by absolute local path. This is not a network integration; it cannot be pointed at a remote host without a real rewrite (turning it into a service with its own API), which is out of scope here. Moving it would break Vantage's video-studio feature outright.
- **`worldmonitor` stays on Hostinger too, but for a different, weaker reason.** It's only loosely coupled — it *pushes* signals into Vantage via `POST /api/intel/signals/ingest` and `POST /api/trading/signals/ingest` (confirmed in `intel.py`/`trading.py` docstrings: "system-only signal ingestion"). This push model works identically regardless of where `worldmonitor` runs — it could move to Contabo without breaking anything. There's simply no reason to: it's already here, already stable, and moving it would only add migration risk for zero functional benefit. Keeping it on Hostinger is a choice, not a requirement.

## 3. Is a private tunnel (WireGuard/Tailscale) warranted?

**Yes — for all three moved dependencies, not just trading.** Three independent reasons, each sufficient on its own:

1. **Real trading capital.** The Ares RPC proxy and wallet-creation service becoming reachable cross-VPS means Vantage's trading execution path now crosses a public network boundary. Even with app-level auth, putting a live trading control surface on the open internet (vs. a tunnel) is a materially larger attack surface for a system that moves real money — this is the strongest of the three reasons and would justify a tunnel on its own.
2. **Gitea's token, as shown above, would otherwise cross the public internet in plaintext** on every code-hosting call (repo search, clone URLs with embedded tokens, git push/pull). This is a concrete, provable risk, not a theoretical one — the plaintext fallback already exists in the code today.
3. **The kernel proxy path matters for correctness, not just security** — `omokoda_cognition_proxy.py`'s whole reason for existing is to get *around* an SSRF guard that blocks private/loopback IPs. A WireGuard tunnel's IP range (typically `10.x.x.x`) is itself a private range — meaning **Vantage's own direct calls to the kernel over the tunnel are fine** (Vantage isn't subject to Buzz's SSRF guard, only Buzz's own workflow engine is), but this needs to be understood explicitly so nobody "fixes" the tunnel setup by accidentally routing the *proxy's own* Buzz-facing side through the tunnel IP and re-triggering the exact SSRF rejection the proxy was built to avoid. **The proxy's public HTTPS front door stays exactly as-is; only its internal forwarding target changes, from `localhost:7777` to `<tunnel-ip>:7777`.**

Recommendation: **WireGuard** over Tailscale for this pairing — a single dedicated two-node tunnel with no third-party coordination service in the loop, appropriate for a link that's carrying trading execution calls and a real code-hosting token. Tailscale's operational simplicity is a better fit for many-node meshes; this is a two-node link where that advantage doesn't matter as much and a self-hosted WireGuard tunnel keeps one fewer external dependency in a security-sensitive path.

## 4. Safe cutover sequence for Gitea specifically (SkillForge depends on it)

Gitea is the trickiest of the three moves because it has **two dependents that must both keep working**: Vantage (code hosting, SkillForge's deploy target) and its own CI runner (`gitea-runner` / `act_runner` container, currently on Hostinger alongside Gitea itself).

**Key fact worth confirming with Omo-Koda2 before scheduling anything:** Gitea Actions runners connect *outbound* to the Gitea server — they do not need to be co-located with it. This means **the runner does not have to move just because Gitea does.** It can stay on Hostinger and simply be reconfigured to dial the new Contabo address over the tunnel. This meaningfully lowers cutover risk (one fewer thing that has to move in lockstep).

Sequence:
1. Stand up the WireGuard tunnel first, independent of any service move — verify basic reachability (ping, a raw `curl` between the two boxes over tunnel IPs) before touching Gitea at all.
2. Provision Gitea on Contabo (fresh install or a full data directory copy — coordinate with whichever pillar owns the actual Gitea data/DB migration mechanics; this plan only covers the Vantage-facing contract, not Gitea's own migration internals).
3. **Do not cut over Vantage's `GITEA_URL` until the new Gitea instance is confirmed to have all 161 mirrored repos and is reachable over the tunnel** — verify with a raw `curl <tunnel-ip>:3001/api/v1/repos/search` before touching Vantage's env.
4. Re-point the Gitea Actions runner at the new tunnel address; verify at least one real CI run succeeds against the new instance before relying on it.
5. Update `vantage.service`'s `GITEA_URL` to the tunnel IP, restart, and immediately re-run a real functional check (repo search, one real clone operation) — the same live-verify standard used throughout this session, not just a health-check ping.
6. **Only after Gitea is confirmed working end-to-end from Vantage's side**, decommission or repurpose the old Hostinger Gitea instance. Keep it stopped-not-deleted for a rollback window, same pattern as the (superseded) v1 plan's rollback approach.
7. SkillForge specifically should get one real end-to-end test (a real repo → skill build) against the new Gitea location before this is called done — it's the one consumer with the most steps between it and Gitea, and the most likely to have a hardcoded assumption somewhere.

## 5. What could break Vantage's own operation if these three move out from under it

- **Anything that assumes same-host latency/reliability.** A `localhost` call and a call over a WireGuard tunnel to a different physical VPS are not equivalent in failure modes — network partitions, tunnel drops, and added latency are all new failure classes Vantage's code wasn't written against. Specifically worth auditing (not yet done, flagged for the actual migration work): do any of the three integrations assume synchronous, near-zero-latency responses in a way that could cause timeouts or degraded UX once real network hops are involved?
- **The `omokoda_cognition_proxy.py` forwarding target** — if this isn't updated in lockstep with the kernel's actual move, Buzz workflow webhook calls silently fail (or worse, silently succeed against a stale/wrong target) the moment the kernel leaves Hostinger. This is the single highest-priority line item to get right, and to explicitly test post-move (not just assume it works because the code "looks" repointed).
- **Gitea's plaintext-fallback anti-pattern being repeated.** If `GITEA_URL` env var is ever unset in a deploy and the code falls back to a hardcoded value (as it does today for Hostinger's IP), someone needs to make sure that fallback doesn't get updated to a plaintext Contabo public IP as a "quick fix" — it should either be removed, or updated to point at the tunnel IP, never the public IP.
- **Wallet/trading calls timing out or partially completing across a network boundary during actual trade execution** — the RPC proxy and wallet-creation calls in `trading.py` were written assuming a local, fast, reliable call. A network blip mid-trade-execution is a new risk category this migration introduces that didn't exist before. Worth an explicit review of `trading.py`'s error handling/retry behavior around these two calls specifically before cutover, given it's real capital.
- **Vantage's own auto-registration bookkeeping of Omo-Koda2-spawned agents** (`agents.py`'s reference to the kernel) — lower stakes than the above (it's bookkeeping, not execution), but should still be verified post-move so agent registration doesn't silently break.

## 6. Cross-pillar convergence — what Vantage needs from the other two sessions before this can be scheduled

- **From Omo-Koda2:** the kernel ecosystem's own migration plan (Julia memory service, Obatala/Clojure, Oya, Elixir swarm) — specifically, the final bind address/port layout on Contabo, so Vantage's `VANTAGE_OMOKODA_URL` and the cognition proxy's forwarding target can be set correctly on the first try, not iteratively discovered.
- **From OSOVM:** confirmation of how `organism-core`'s bridge to the kernel is affected. Per this session's own ecosystem audit, `organism-core` is already confirmed to be **simulation-only, not a live bridge today** — so this migration doesn't break a working connection, but it does mean whoever eventually builds that real bridge needs to design it against the *post-migration* address layout, not the current same-host assumption. Worth flagging now rather than have it silently baked in as a same-host assumption later.
- **A single agreed cutover order across all three services** (Gitea, kernel, Ares) — this plan recommends Gitea first (lowest stakes, clearest dependents, SkillForge as a strong end-to-end test), kernel second, Ares/trading last and most carefully (highest stakes, real capital, least tolerance for a botched cutover).

---

*Drafted by the Vantage-side session, 2026-08-05. Supersedes the earlier `CONTABO_MIGRATION_PLAN.md` in this same repo, which assumed the wrong migration direction. Nothing in this plan has been executed — no service moved, no DNS changed, no tunnel stood up.*
