# Vantage-side findings for Omo-Koda2's Contabo migration plan

*Answers the two open items in `CONTABO_MIGRATION_PLAN_DRAFT.md` §7 checklist. Report only — no firewall or config changes made. Verified live via `ufw status`, systemd unit inspection, and direct source read on Hostinger.*

---

## 1. Firewall / reachability

**Current `ufw` state on Hostinger** (confirmed via `ufw status verbose`):

```
80, 81, 82, 83, 443, 8000, 8001, 8010, 8080, 8090, 3443 → ALLOW IN, Anywhere (both v4/v6)
Default: deny incoming, allow outgoing
```

- **Vantage's `:8001` is already open to the entire internet** ("Anywhere" — not scoped to any IP). **No new firewall rule is needed for Contabo to reach it** — it's already reachable from any source, Contabo included, with zero changes. The public DuckDNS URL (`:443`) is likewise already open.
- **This means a WireGuard tunnel for Vantage's own `:8001` isn't strictly *required* to gain reachability — but it's still the right call**, for a reason the current state actually reinforces: `:8001` being open to "Anywhere" today is already broader than ideal (nothing scopes it to trusted callers), and standing up a tunnel would be a net *security improvement* — narrowing exposure to just Contabo instead of leaving it open to the whole internet — not merely "enabling" something that already technically works.
- **`buzz-relay`'s raw Docker port `:3000` is NOT in the allowlist at all.** Only `:3443` (the public Traefik/TLS entrypoint) is open. This is the important one — see below.

### The buzz-relay port is where the real risk is, and it changes a recommendation in Omo-Koda2's draft

Omo-Koda2's draft (§7a) reasons that `buzz-acp` moving to Contabo would need its `relay=ws://localhost:3000` to become `ws://<hostinger-ip>:3000`, and flags that as needing a firewall rule. **That specific approach should not be taken — not just because the firewall is closed, but because it would silently break tenant binding.**

This session's own hard-won findings (documented in `buzz_pairing.py`'s module comments, verified live) established that buzz-relay's multi-tenant binding is keyed **purely off the inbound HTTP `Host` header** — a connection to `<hostinger-ip>:3000` presents a completely different tenant than a connection to the public hostname `omokoda.duckdns.org`. Vantage's own backend gets around this via a raw-socket-plus-public-Host-header trick specifically because it's running *on the same host* as the relay (it can open a raw TCP socket to `localhost:3000` while presenting the public Host header in the WS handshake). **A genuinely remote client on Contabo cannot do that trick** — it has no way to dial an internal-only address while claiming a public Host header over a real network hop.

**Correct approach:** `buzz-acp` on Contabo should connect to the same public entrypoint external clients already use — `wss://omokoda.duckdns.org:3443` — not raw port `3000`. That port is already open in `ufw`, already has correct tenant binding, already goes through NIP-42 auth exactly as any other external Nostr client would. This requires **zero new firewall rules** — but does mean `buzz-acp`'s config needs a real NIP-42 auth implementation if it doesn't already have one (the same gap I found and fixed in Vantage's own NIP-46 signer this session — worth checking `buzz-acp` for the identical class of bug before assuming it "just works" once repointed).

**Recommendation:** don't open `:3000` to Contabo's IP at all, tunneled or not. Route `buzz-acp` through the existing public `:3443` entrypoint instead. If a private tunnel is still wanted for other cross-VPS traffic (Gitea, Vantage API), that's independent of this decision — buzz-relay's own public entrypoint is already the correct, already-open, already-tenant-correct path.

### Summary answer to Q1

| Path | Firewall today | Recommendation |
|---|---|---|
| Contabo → Vantage `:8001` / DuckDNS | Already open (Anywhere) | Works with zero changes; still worth putting behind a WireGuard tunnel to narrow exposure from "anywhere" to "just Contabo" |
| Contabo (`buzz-acp`) → buzz-relay | Port `3000` not open at all; only `3443` is | **Do not open `:3000`.** Point `buzz-acp` at the existing public `wss://omokoda.duckdns.org:3443` entrypoint instead — already open, already tenant-correct. Verify `buzz-acp` does real NIP-42 auth before assuming this "just works." |

---

## 2. Gitea access pattern

**Confirmed via direct source read, not inference: the Omo-Koda2 kernel connects to Gitea directly — it does NOT go through Vantage's `/api/code/*` proxy.**

Found in `/opt/ares/Omo-Koda2/omokoda-core/src/tools/skillforge.rs`:

```rust
let gitea_url = Self::env_or_dotenv("GITEA_URL").unwrap_or_else(|| "http://localhost:3001".to_string());
let gitea_token = Self::env_or_dotenv("GITEA_TOKEN").unwrap_or_default();
```

`env_or_dotenv()` checks the process environment first, then explicitly falls back to reading `/opt/ares/.env` directly (hardcoded path, `SKILLFORGE_DOTENV` overridable). The kernel's own systemd unit (`ares-omokoda.service`) sets no `GITEA_URL`/`GITEA_TOKEN` — so it falls through to that shared `/opt/ares/.env` file, **which does have a real `GITEA_TOKEN` set** (the same token Vantage's own unit uses). Net effect: **this is a live, active, independently-authenticated connection from the kernel straight to Gitea's Docker port, completely separate from anything Vantage does** — not dormant, not a fallback that never fires, and not proxied.

This is used for SkillForge's security-webhook registration (`POST /api/v1/repos/{owner}/{repo}/hooks` against Gitea directly, bearer-token authed) — the kernel talks to Gitea on its own, for its own purposes.

**This changes the open question in Omo-Koda2's draft §7a from "likely proxied, unconfirmed" to a confirmed no:** Gitea reachability from Contabo does **not** ride along with Vantage's own reachability rule. It needs its **own** cross-VPS path, because the kernel is a second, independent caller with its own credential — not a passthrough of Vantage's connection.

**Given this is a real, currently-live Gitea API token that would otherwise need to cross the public internet in plaintext** (the same anti-pattern already flagged in the earlier `CONTABO_MIGRATION_PLAN.md` for Vantage's own Gitea calls), **this is the strongest concrete case yet for the WireGuard tunnel actually being load-bearing, not just a nice-to-have** — both Vantage's and the kernel's Gitea connections should go over it, both carrying the same real credential.

### Summary answer to Q2

**Direct connection, not proxied.** The kernel needs its own tunneled path to Gitea's `:3001` once it's on Contabo — it cannot rely on Vantage's reachability alone, because it's not calling through Vantage at all.

---

## Net effect on Omo-Koda2's draft

- §4's Gitea row should change from "likely proxied through Vantage... needs a direct check" to **confirmed direct connection, needs its own tunneled path**.
- §7a's `buzz-acp` recommendation to move to Contabo still looks right, but the *reconnection detail* should change: point it at the public `wss://omokoda.duckdns.org:3443` entrypoint (already open, tenant-correct), not a firewalled-open raw `ws://<hostinger-ip>:3000` (would hit a tenant-binding mismatch even if the port were opened).
- Vantage's own `:8001` needs no firewall change — already open — but a WireGuard tunnel is still recommended for all three cross-VPS paths (Vantage API, Gitea, buzz-acp-via-3443) as a coherent security posture, not opened piecemeal per-service.

---

*Compiled by the Vantage-side session, 2026-08-05. No firewall rules or configs changed — report only, per instructions.*
