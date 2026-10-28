# Vantage Execution TODO
> Last updated: 2026-10-28. Check this file before starting any session.

---

## PHASE 0 — Safety & Stability (do first, block everything else)

### P0-1 Find and lock the operator trading switch
- [ ] In `backend/routers/trading.py` ~line 2171, find the env var / config field that gates auto-execution above conviction 0.7
- [ ] Confirm it is set to OFF in `/etc/ares-env/*.env` and in the running process env
- [ ] If the var is missing entirely, add it with a safe default of `False` and restart `vantage.service`
- [ ] Document the var name in this file

### P0-2 Fix crashing systemd services
- [ ] **ares-radar** (exit 1) — check `/opt/ares/ares_radar.py` error log; likely API key or changed DexScreener URL
- [ ] **ares-ogun-orchestrator** (FileNotFoundError: 'sol') — `sol` binary missing; find where it was and either install or point to correct path in the unit's ExecStart/Environment
- [ ] **ares-deepseek-intel** (stdout: No such file or directory) — fix `StandardOutput=` or `StandardError=` in the unit file to point to an existing path or `journal`
- [ ] **ares-strategy-lab-hub** (17,143 restarts) — read the actual error, find root cause; do not just silence the restart loop
- [ ] For each: `journalctl -u <service> -n 30 --no-pager` → fix → `systemctl restart` → confirm active

### P0-3 Confirm intel-only for all paper/alpha signals
- [ ] Grep `api/trading/signals/ingest` across all daemon scripts in `/opt/ares/`; any match that isn't the trading daemon itself is a bug
- [ ] Expected offenders to check: `pumpfun_launch_radar.py`, `ogun_degen*.py`, `alpha_hunter*.py`, `grokbot_paper*.py`

---

## PHASE 1 — Alpha Stack Completion (main build work)

### P1-1 Smart Money scorer module
- [ ] Create `backend/smart_money_scorer.py`
  - Score sources: `early_buyer` table data, `wallet_clusters` table, Helius trade history (win rate, early-entry frequency, exit timing, consistency), `kol_map.json` + `watchlist.json` seeds
  - Thresholds configurable via env or config
  - Provisional label → promote after N refresh cycles (persist `refresh_count`, `promoted_at`)
  - DB table: `smart_money_wallets (address, score REAL, category TEXT, label TEXT, source TEXT, refresh_count INT, promoted_at REAL, last_updated REAL)`
- [ ] Wire into `backend/routers/alpha_hunter.py` (or new `alpha_smart.py`):
  - `GET /api/alpha/smart-money` — list all labeled wallets, supports `?category=` + `?min_score=` filters
  - `GET /api/alpha/smart-money/{wallet}` — score, history, label, sources
  - `POST /api/alpha/smart-money/rebuild` — force full re-score; supports `dry_run=true`
- [ ] Add `smart_money_only: bool = Query(False)` param to `GET /api/moneyflow` in `alpha.py`
- [ ] Decorate wallet nodes in moneyflow response with `smart_money_score` and `smart_money_label` fields

### P1-2 Smart Money refresh daemon
- [ ] Create `backend/daemons/smart_money_daemon.py` — polls every 7 days (configurable via `SMART_MONEY_REFRESH_DAYS` env var), calls `smart_money_scorer.rebuild()`
- [ ] Add systemd unit `/etc/systemd/system/ares-smart-money.service` with `EnvironmentFile=/etc/ares-env/ares-smart-money.env`
- [ ] Add `RestartSec=30` and `Restart=on-failure`

### P1-3 Sniper pipeline handoff module
- [ ] Create `backend/sniper_pipeline.py`
  - Accepts: `mint`, optional `cluster_id`, optional `smart_money_signal`
  - Checks `ares-control` `approve_execution` gate via HTTP before any action
  - Default mode: paper via `shadow_account.py`; live mode requires explicit `mode="live"` AND gate approval
  - Kill conditions struct: `liquidity_collapse_threshold`, `top_holder_spike_pct`, `ttl_seconds`, `score_floor`
  - Persist active snipers: `active_snipers (id, mint, mode, kill_conditions JSON, spawned_at, killed_at, kill_reason)`
  - Log every spawn/kill to existing action receipt path
- [ ] Wire into `alpha_hunter.py` (or `alpha_sniper.py`):
  - `POST /api/alpha/sniper/spawn` `{"mint": "...", "mode": "paper", "kill_conditions": {...}}` — `dry_run=true` supported
  - `GET /api/alpha/sniper/active`
  - `POST /api/alpha/sniper/{id}/kill`

### P1-4 Auto-refresh daemon
- [ ] Create `backend/daemons/alpha_refresh_daemon.py`
  - Schedule (configurable, defaults): re-link known KOLs from `kol_map.json` every 6h; re-backtrack recent high-volume mints (top 10 by trade cap last 24h) every 2h; rebuild wallet clusters every 12h
  - Cache-first: skip if last run < interval
  - Post one `source="alpha_refresh"` health signal to intel bus after each cycle
- [ ] Add systemd unit `/etc/systemd/system/ares-alpha-refresh.service`

### P1-5 Health endpoint
- [ ] Add `GET /api/alpha/status` to `alpha_hunter.py`:
  - `last_linker_run`, `last_cluster_rebuild`, `last_smart_money_rebuild`, `smart_money_wallet_count`, `active_sniper_count`, `alpha_refresh_last_run`
  - Pull from DB `SELECT MAX(last_updated) ...` for each

### P1-6 Cluster quality tags (HoldTech-style)
- [ ] In `wallet_cluster_engine.py`, add optional computation of:
  - `bundle_detected: bool` — wallets funded within same 60s window by same funder
  - `fresh_wallet: bool` — first tx < 7 days ago
  - `concentration_pct: float` — top-1 holder % of cluster's token exposure
- [ ] Gate behind `include_quality_tags: bool = False` query param on cluster endpoints

---

## PHASE 2 — MCP Exposure Enforcement

### P2-1 Add "alpha" + "intel" to recognized tag set
- [ ] Read `backend/skills_registry.py`
- [ ] Add `"alpha"` and `"intel"` to the recognized/allowed tags list
- [ ] Verify `build_skills_registry(app)` picks up the new `/api/alpha/*` routes
- [ ] Hit `GET /api/agents/skills` (or equivalent) and confirm all alpha_hunter + smart money + sniper endpoints appear

### P2-2 Wire MCP server
- [ ] `backend/mcp_server.py.bak-guildmcp-*` exists — check if `fastapi-mcp` is installed: `pip show fastapi-mcp`
- [ ] If installed, restore `mcp_server.py` from the `.bak` and mount it in `main.py`
- [ ] If not installed: `pip install fastapi-mcp` in venv, then restore and mount
- [ ] Test: agent callable path via MCP tool list

### P2-3 Add mcp-exposure.yml GitHub Action
- [ ] Create `.github/workflows/mcp-exposure.yml` with the AST scanner (see MASTER_TODO.md Part 3)
- [ ] Add `"alpha"` and `"intel"` to the `recognized_tags` set in that Action
- [ ] Commit to Vantage repo

---

## PHASE 3 — Prediction Market Signal Layer

### P3-1 Confirm intel ingest payload shape
- [ ] Read `backend/routers/telegram_webhook.py` — this is the reference POST payload to `/api/intel/signals/ingest`
- [ ] Document the exact JSON shape here: `{"source": str, "signal_type": str, "content": str, "conviction": float, "metadata": dict}`
- [ ] Confirm `prediction_markets.py` is the Crucible/IfáScript falsifier (different purpose) — do NOT reuse it for Kalshi

### P3-2 Kalshi research collector
- [ ] Create `backend/daemons/kalshi_collector.py`
  - Poll `https://external-api.kalshi.com/trade-api/v2/markets?status=open` (no auth required)
  - For each market: fetch `rules_primary` text + orderbook mid price + last candlestick
  - Enrich with Mostly Right settlement registry (CC BY 4.0) where `rules_primary` is ambiguous
  - Build typed state object: `{"ticker", "title", "rules_primary", "book_mid", "close_date", "vantage_news_context"}` — **strip `book_mid` from the model prompt**, keep it for edge computation only
  - Write `last_posted` timestamp after each cycle; emit health signal if silent > SLA

### P3-3 Jev decision layer (pure function)
- [ ] Create `backend/jev_engine.py` — pure function: `jev_score(state: dict) -> float`
  - Calls OpenRouter (or Anthropic) with structured prompt; returns probability 0.0–1.0
  - No DB, no I/O inside the function itself
  - Edge = `jev_probability - (book_mid - fees)` computed by the caller

### P3-4 Prediction market signal daemon
- [ ] Create `backend/daemons/prediction_market_daemon.py`
  - Thin wrapper: call `kalshi_collector`, pass state to `jev_engine`, post result to `/api/intel/signals/ingest` with `source="kalshi_jev"`, `conviction=edge`
  - **Never post to `/api/trading/signals/ingest`**
  - Writes `last_posted`; watchdog thread emits alert signal if silent > 30 min
- [ ] Add systemd unit `/etc/systemd/system/ares-prediction-market.service`

### P3-5 Brier calibration gate in Mycelium
- [ ] Extend `mycelium/miners/signal_quality.py` OR add `mycelium/calibration/brier_gate.py`
  - Track resolved Kalshi contracts vs posted predictions
  - Compute Brier score against **market price** (not just base rate)
  - Gate: minimum 30 resolved contracts, spread across ≥3 event types
  - Output: `calibration_status: "shadow" | "eligible" | "failing"` per signal source
- [ ] Wire gate status into `/api/intel/signals` metadata so it's visible

### P3-6 Polymarket read-only collector (Florida — international = data only)
- [ ] Create `backend/daemons/polymarket_collector.py`
  - Polymarket Gamma (`gamma-api.polymarket.com`) for discovery
  - CLOB (`clob.polymarket.com`) for books and midpoints
  - Read-only — no order placement on international exchange
  - Same state-building pattern as Kalshi collector; strip book price from Jev prompt

---

## PHASE 4 — VPS Restoration Remaining Items

### P4-1 Fix test signal in production feed
- [ ] Find which service is posting `source="test"` to `/api/intel/signals/ingest`
- [ ] Either gate it behind `if os.environ.get("ENVIRONMENT") != "production"` or remove it

### P4-2 Fix pumpfun_launch_radar conviction inversion
- [ ] In `pumpfun_launch_radar.py` find the scoring line that raises conviction for more prior launches
- [ ] Propose inversion: more launches → higher rug risk → lower conviction (or separate `serial_deployer` risk flag)
- [ ] Do not change yet — present the code and the fix for approval first

### P4-3 Reissue Trader Joe API key (agent id 497)
- [ ] Server-side: call internal key-issuance API or directly insert into `vantage.db` agents/keys tables
- [ ] Write key to `/root/trader_joe_key.txt` with `chmod 600`
- [ ] Capabilities: `read`, `intel` only — no `place_trade`

### P4-4 Confirm new signal sources are intentional
- [ ] For each: `ogun_degen`, `vantage-predictor`, `trading_agents`, `worldmonitor_geo`, `polymarket`, `kraken`, `coinpaprika`, `fear_greed`, `geckoterminal` — check the producing service name and what it posts
- [ ] Confirm none of them post to the trading ingest

---

## PENDING CREDENTIALS (blocks specific tasks)

| Credential | Blocks | Where to set |
|---|---|---|
| Anthropic API key (with balance) | Paperclip claude-worker | `/etc/ares-env/paperclip-claude-worker.env` → `ANTHROPIC_API_KEY` |
| `XAI_API_KEY=xai-...` | Paperclip grok-worker | `/etc/ares-env/paperclip-grok-worker.env` |
| Tailscale auth key | VPN mesh between devices | `tailscale up --authkey=YOUR_KEY --hostname=omokoda-vps --accept-routes` |
| OpenRouter API key (for Jev) | P3-3 decision layer | `/etc/ares-env/ares-prediction-market.env` → `OPENROUTER_API_KEY` |

---

## DONE (for reference)

- [x] NostrGateway relay abstraction
- [x] Galaxy graph: `social_linker.py`, `early_buyer.py`, `wallet_cluster_engine.py`
- [x] moneyflow params: `twitter_handle`, `mint_backtrack`, `cluster_id`, `early_buyers_only`, `show_clusters`
- [x] `alpha_hunter.py` endpoints: `/status`, `/wallets`, `/wallets/{address}`, `/clusters`, `/events`
- [x] pumpfun `/risk` endpoint uses Helius for authority checks
- [x] SQLite `cache_size` + `mmap` applied per-connection
- [x] `prediction_markets.py` Crucible/IfáScript falsifier (different from Kalshi signal layer)
- [x] Paperclip orchestration layer (grok worker + claude worker + invite flow)
- [x] MASTER_TODO.md committed to Vantage repo
