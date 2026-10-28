# Vantage Master TODO

> Canonical reference for all pending features and infrastructure work.
> Last updated: 2026-10-28

---

## Part 1 — VPS Signal Restoration

### Hard Rules
- Do NOT enable any trading or auto-execution. Do not place, sign, or simulate-then-send any order. Do not touch wallet private keys or any reveal-key endpoint.
- Keep these OFF: `ares-wallet-learner`, `ares-pumpfun-trader`, `ares_trader_solana.py`, any sniper, `pumpfun_daemon` and old pump.fun trading scripts (pre-v2 instruction layouts), `refresh_gmgn_proxies.py` cron (IP banned 1,000+ times).
- Read and report before changing. Ask before deleting data, rotating keys, or changing firewall/DNS.
- Make small commits with clear messages. Don't push to remote until approved.

### STEP 0 — Safety Check
1. `/api/trading/signals/ingest` auto-creates a REAL order when conviction > 0.7 and the agent has a wallet. `daemons/vantage_predictor.py` posts there with `execute=True` at conviction 1.0. Find the operator switch, confirm it is OFF in the new server env/config. If missing, default must be OFF.
2. Confirm none of `pumpfun_launch_radar`, `ogun_degen`, `alpha`, or `grokbot_paper` signals can reach `/api/trading/signals/ingest`. Intel path only.
3. List which agents have wallets attached in vantage.db.

### STEP 1 — Inventory
- Expected intel units: `ares-wallet-intel`, `ares-alpha-hunter`, `pumpfun-launch-radar`, `pumpfun-tier-scanner`, `ares-signal-fusion`, `signal-gate`, `loom`, `coordinator`, radar, alpha-feed, `social_tracker`/Telegram webhook, dex-new, dune/`advanced_analytics`, `signal_aggregator` (whale_monitor), `ares-grokbot-paper`.
- Check data arrived: `vantage.db` (~995 MB), `wallet_intel.db` (~257k wallets), `ares-signal-fusion/ares_picks.db` (~4,445 graded picks), Mycelium pick log.

### STEP 2 — Restore Missing Sources
1. **"radar"** — `intel.py` reads `~/ares_radar/latest.json`. Find producer, restore.
2. **"alpha_feed"** — `intel.py` reads `~/ares_alpha_feed/high_conviction.json`. Restore producer.
3. **"social_telegram"** — Re-register Telegram webhook against new host. Posts to intel ingest only.
4. **"dex_new"**, **"dune"**, **"whale_monitor"** — appeared after move, vanished after restart. Find why.
5. `~/ares_intelligence/intel_latest.json` — check being produced.
6. Grep for old server IP, fix env files.

### STEP 3 — Fix Degraded Pump.fun Tools
- `/api/intel/pumpfun/signals` returns 0 signals.
- `pumpfun_tier_scanner.py` (PumpPortal WebSocket) probably disconnected.
- `/api/intel/pumpfun/token_traders` returns 0 traders (Helius key missing?).
- `/api/intel/degen/fresh_deployers` times out.
- `/api/intel/pumpfun/risk/{mint}` returns `risk_score 0, "safe": true` — fail closed; use Helius for authority checks instead of GMGN.
- Verify Helius, Birdeye, PumpPortal, GeckoTerminal, Telegram keys exist (names only, never values).

### STEP 4 — Cleanups
- Source "test" appeared in live unified feed — find and remove.
- `pumpfun_launch_radar` inverted: higher conviction for MORE prior launches. Serial deployers = rug risk. Document and propose fix (don't change yet).
- Confirm new sources are intended: `ogun_degen`, `vantage-predictor`, `trading_agents`, `worldmonitor_geo`, `polymarket`, `kraken`, `coinpaprika`, `fear_greed`, `geckoterminal`.
- Agent "Trader Joe" (id 497): reissue API key server-side, write to file only owner can read. `read` + `intel` only, no `place_trade`.

### STEP 5 — Verify and Report
- `GET /api/intel/signals` with every source and newest timestamp.
- `systemctl status` for every intel unit.
- Table: source | before | after | fixed | still broken.
- List every file and env var changed.

---

## Part 2 — Galaxy Graph Extensions

### Context (reuse, do not rewrite)
- Money Flow endpoint merges `wallet_trades` (Helius) + `wallet_edges` (counterparties). Nodes: wallets/tokens/social. Edges: capital flows + mentions.
- Brightness = recent-activity share; size = capital weight (Mandelbrot fade).
- `social_signals` + `social_tracker.py` feed X/Telegram mentions.
- Mycelium: `cluster_detect.py`, `kol_map.json`, `watchlist.json`, `collect_decision_traces.py`.
- NostrGateway is the only relay abstraction. Never hardcode relay URLs.

### Features to Implement

**1. Social-to-chain linker**
- Input: X/Twitter handle or raw wallet address.
- Scrape recent posts for Solana mints/contract addresses.
- Resolve to funded wallets via Helius.
- Persist `handle → wallet` mappings.
- Inject into Money Flow as social nodes + "mentioned" edges + capital-flow edges.

**2. Token early-buyer backtracker**
- Input: token mint.
- Walk earliest transactions (bonding-curve / first N buyers) backward with Helius.
- Build buyer DAG: funder relationships, timestamps, amounts, entry rank.
- Inject wallets/flows into `wallet_edges` / `wallet_trades` with correct brightness/size.
- Tag early buyers in node metadata.

**3. Clustering & categorization**
- Port Mycelium `cluster_detect.py` logic.
- Group by shared funding sources, co-trading windows, behavioral fingerprints.
- Classify: active, dormant, dust, low-traction, high-conviction.
- Attach `cluster_id` + tags to graph nodes; support cluster filter query param.

**4. Self-hosted Smart Money labels**
- Seed from `kol_map.json`, `watchlist.json`, newly linked social wallets.
- Score: win rate, early-entry frequency, consistency, exit timing (Helius trade history).
- Weekly daemon re-ranks. Expose as Money Flow node labels and filter.

**5. Sniper pipeline handoff**
- Accepts scored token from Early-Entry / fomopulse / Smart Money.
- Checks `ares-control` `approve_execution` gate.
- Paper mode by default. Kill conditions: liquidity collapse, top-holder spike, time expiry, score drop.

**6. Money Flow API extensions**
- Preserve existing response shape.
- Add query params: `twitter_handle`, `mint`, `cluster_id`, `early_buyers_only`, `smart_money_only`.

---

## Part 3 — MCP Exposure Enforcement

### GitHub Action (save as `.github/workflows/mcp-exposure.yml`)

Fails build when new logic lands without a tagged route or with a tag outside the known set. Recognized tags: `identity`, `agents`, `mind`, `playlists`, `swarm`, `workspace`, `guild-forum`, `guilds`, `publish`, `feed`, `social`, `messages`, `notifications`, `analytics`, `cinema`, `audio`, `series`, `co-creation`, `pipeline`, `federation`, `mesh`, `copilot`, `composio`, `trading`, `code`, `platform`, `admin`, `telegram`, `alpha`, `intel`.

Logic: walk `backend/` rglob `*.py`; flag modules with 3+ functions and no FastAPI route; flag routes with unrecognized tags.

### MCP Audit Prompt for Claude Code

Full audit: inventory every `backend/` module with non-trivial logic; gap report for unexposed features (no route or wrong tag); remediation with correct router + recognized tag + agent-friendly models + dry-run mode; verification that skills registry picks up new tools; enforcement suggestions.

---

## Part 4 — Alpha Stack Completion

*Prerequisite: `social_linker.py`, `early_buyer.py`, `wallet_cluster_engine.py`, and 8 `/api/alpha/` endpoints already committed.*

**Smart Money scorer + daemon** — `smart_money_scorer.py` scoring from early_buyer, wallet_clusters, Helius trade history, `kol_map`/`watchlist`. Endpoints: `GET /api/alpha/smart-money`, `GET /api/alpha/smart-money/{wallet}`, `POST /api/alpha/smart-money/rebuild`. moneyflow param `smart_money_only=true`.

**Sniper pipeline** — Accepts trigger (token mint, `cluster_id`, or smart-money signal). Checks `approve_execution`. Paper mode first. Endpoints: `POST /api/alpha/sniper/spawn`, `GET /api/alpha/sniper/active`, `POST /api/alpha/sniper/{id}/kill`. Log every spawn/kill to action receipt path.

**Auto-refresh daemon** — Re-link known KOLs, re-backtrack recent high-volume mints, rebuild clusters on schedule.

**Health endpoint** — `GET /api/alpha/status`: last linker run, last cluster rebuild, smart-money label count, active snipers.

**Constraints** — All routes under `/api` with recognized tags. Paper mode by default. No hardcoded relay URLs. `dry_run=true` on every mutating POST. Cache Helius aggressively.

---

## Part 5 — Prediction Market Signal Layer

### Hard Safety Rules
1. **Intel endpoint only.** All output to `/api/intel/signals/ingest`. Never to `/api/trading/signals/ingest`.
2. **Live trading is a separate process and a separate key.** No config flag on the signal daemon enables live orders.
3. **Venue eligibility (Florida):** Kalshi and ForecastEx eligible. Polymarket US eligible when stable. Main Polymarket international = read-only only.

### Architecture (Five Layers)

**Venue** — Kalshi public REST + optional WebSocket. Polymarket Gamma + CLOB (read-only). PMXT optional and only after sidecar/auth review. Demo endpoints only during development.

**Research** — Collector: market metadata, rules text (`rules_primary` or Mostly Right registry), order-book mid/last, Vantage news/context. **Strip all market prices and odds from prompt.** Record market price separately.

**Decision** — Jev as pure function: state in, probability out. No I/O, no credentials, no memory.

**Edge + monitoring** — Edge = model probability vs (market price − fees). Every signal source writes `last_posted`. Silent source emits health/alert on intel bus.

**Calibration + shadow** — Extend Mycelium `signal_quality.py` to score signals against resolved contracts with Brier. Shadow-only until calibration gate met.

### Calibration Rules
- Score against **market price**, not only base rate.
- Minimum **30 resolved contracts**, spread across several event types.

### Data Sources
- Kalshi: `external-api.kalshi.com/trade-api/v2`
- Polymarket Gamma + CLOB (read-only)
- Mostly Right settlement rules/sources dataset (CC BY 4.0)
- Vantage intel ingest + Mycelium signal-quality / signal-fusion

### Build Order
1. Confirm intel ingest payload shape (`telegram_webhook.py` is the reference).
2. Research collector for Kalshi (market price stripped from prompt).
3. Wire Jev as pure probability function. Post to intel endpoint with `last_posted` and computed edge.
4. Add silent-source health signal.
5. Extend Mycelium: Brier against resolved contracts, measuring vs both base rate and market price, with 30-contract / multi-event-type gate.
6. Shadow-only for at least one full contract cycle. Review calibration.
7. Only after review: decide on a separate live-execution process (not part of this layer).

---

## Pending Credential Gaps

| Item | Where to set |
|------|-------------|
| Anthropic API key with balance | `/etc/ares-env/paperclip-claude-worker.env` → `ANTHROPIC_API_KEY` |
| xAI API key | `/etc/ares-env/paperclip-grok-worker.env` → `XAI_API_KEY` |
| Tailscale activation | `tailscale up --authkey=YOUR_KEY --hostname=omokoda-vps --accept-routes` |
