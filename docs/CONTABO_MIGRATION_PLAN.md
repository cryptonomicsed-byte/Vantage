# Vantage-vs-Contabo Split: Migration Plan

## Status quo (verified live, 2026-08-04)

- **No Contabo VPS is provisioned.** `omokoda.duckdns.org` resolves to `2.25.70.156` (the current Hostinger box). No memory record, config file, systemd unit, or env var anywhere in this environment references Contabo. This plan is a from-scratch design, not a migration-in-progress.
- The Hostinger box (`hostinger-vps`, 96G disk, currently running at 97-100% utilization — a real, recurring problem, see Risks) hosts everything: Vantage, Ares trading (43 services), buzz-relay (Docker), Omo-Koda2, OSOVM, Zangbeto, Gitea, and more, all as siblings under `/opt/ares` and systemd.
- **Vantage is NOT loosely coupled today.** `vantage.service`'s own unit file wires it to `localhost` for Gitea (3001), Strix runner (9877), Parrot Security (9878), Omo-Koda kernel (7777), and Zangbeto (8787) — all on the same host, via plain `http://localhost:<port>`, no auth beyond what those services expose over loopback. A naive "just move Vantage" lift would silently break all five integrations the moment they're no longer co-located.
- buzz-relay runs as Docker containers (`buzz-prod-relay-1` + postgres/redis/minio) under `/opt/ares/buzz-relay/deploy/compose`, fronted by a **dedicated Traefik entrypoint** bound to `omokoda.duckdns.org:3443` — this exists specifically because NIP-42 relay-URL matching and multi-tenant Host-header binding require the public hostname to be exact (see `buzz_pairing.py`'s own hard-won lessons this session). Moving the relay to a different host means either moving DNS/cert for that hostname too, or re-provisioning a second tenant binding for a new hostname — not a transparent proxy move.
- Vantage's DB is SQLite (`VANTAGE_DATA_DIR=/opt/ares/Vantage/data`) — single-file, not clustered. A split means either accepting a fresh empty DB on the new host, or a one-time file copy + downtime window (no live replication).

## Recommended split direction

Given the coupling above, splitting **Vantage's user-facing web/API tier alone** (frontend + FastAPI + its SQLite DB) onto Contabo, while **leaving buzz-relay and the localhost-only sibling services on Hostinger**, is the lowest-risk cut — it's the one boundary that doesn't require re-architecting five same-host integrations simultaneously. The alternative (moving buzz-relay too) is viable but roughly doubles the scope of this plan (Traefik/DNS/cert config, Docker volumes, Postgres data) for marginal benefit, since buzz-relay has no logic-level dependency on Vantage's own DB.

If the actual intent was different (e.g., moving Ares trading off, or a full-stack duplicate for redundancy rather than a functional split), say so — the above is inferred from what's technically separable, not confirmed against a stated goal.

## What has to move

1. **Vantage backend** — `/opt/ares/Vantage` (backend/, frontend/dist, data/) as a directory copy.
2. **SQLite DB file(s)** under `VANTAGE_DATA_DIR` — copied once at cutover, not before (to avoid divergence).
3. **Secrets/env vars** — `VANTAGE_ADMIN_KEY`, `VANTAGE_SEED_MASTER_KEY` (referenced elsewhere this session), `GITEA_TOKEN` — these must be duplicated (not regenerated) so existing agent keys/sealed seeds still derive identically.
4. **DNS** — `omokoda.duckdns.org` A/AAAA record repointed to the new Contabo IP (this is the single hard cutover moment — everything else can be prepared in advance).
5. **TLS cert** — whatever currently terminates HTTPS for the main domain (check for certbot/Let's Encrypt on Hostinger) needs reissuing on Contabo for the same hostname, or a Cloudflare-proxied setup that makes this moot.

## What must NOT move (stays on Hostinger, bridged over network instead of localhost)

- Gitea, Strix runner, Parrot Security, Omo-Koda kernel, Zangbeto — Vantage's five `http://localhost:*` integrations become `http://<hostinger-ip>:<port>` (or a Tailscale/WireGuard tunnel between the two boxes, preferable to raw public exposure of these internal ports). **This is the actual hard part of the split, not the DNS cutover.**
- buzz-relay + its Postgres/Redis/Minio stack (per recommended direction above).

## Step-by-step cutover sequence

1. Provision Contabo VPS, harden (ufw, ssh key auth) — mirror the `ufw allow` lessons already learned the hard way on Hostinger for buzz-relay's port 3443.
2. Set up a private network path (WireGuard/Tailscale) between Contabo and Hostinger for the five sibling-service calls, before touching DNS.
3. Copy `/opt/ares/Vantage` to Contabo, install matching Python venv, verify `systemctl start vantage` runs clean pointed at the tunnel IPs for its five dependencies (test with DNS still pointed at Hostinger — reachable only internally/by IP for now).
4. Freeze writes momentarily, copy the live SQLite DB file over (rsync), start the Contabo instance against that copy.
5. Flip DNS. Because of DNS TTL, expect a window (minutes to an hour) where some clients still hit Hostinger — this is the main source of split-brain risk if Hostinger's old Vantage instance is still running and accepting writes during that window. **Stop `vantage.service` on Hostinger before flipping DNS**, not after.
6. Reissue/attach TLS cert on Contabo for the domain.
7. Monitor error rates + the five sibling-service integrations for a real request cycle before decommissioning the Hostinger copy.

## Rollback plan

Keep Hostinger's `vantage.service` and its DB copy intact (stopped, not deleted) for at least one full day after cutover. Rollback = revert DNS + restart the Hostinger service. Because of step 5's stop-before-flip ordering, the Hostinger copy will be stale by however long the cutover took — accept a small window of lost writes on rollback, or reconcile manually from Contabo's DB if rollback happens same-day.

## Top risks

1. **The five localhost-only integrations are the real migration cost, not the web tier.** Any plan that treats this as "just move a FastAPI app" will silently break Gitea/Strix/Parrot/Omo-Koda/Zangbeto calls at cutover. Budget real time for the private-network bridge and to re-test each integration post-move.
2. **Disk pressure on Hostinger is already a live, recurring incident** (96G root at 97-100% full multiple times this session alone, once mid-build). If cleanup work happens *during* the migration window under time pressure, that's a bad time to also be firefighting disk space — resolve Hostinger's disk situation independently of this migration, not as part of it.
3. **SQLite has no live replication** — the DB-copy step is a hard synchronization point. Any write to Hostinger's Vantage after the copy but before Hostinger is stopped is lost unless manually reconciled. The stop-before-flip ordering in step 5 is the mitigation; skipping it is the most likely way this migration causes a real data-loss incident.
