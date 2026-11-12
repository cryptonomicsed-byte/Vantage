# Trading in Vantage: the map

Built from the code on `main` (4dd1942) and the live box on 2026-11-12. Every
claim names the file it came from. Where this doc and the code disagree, the
code wins; fix the doc.

## 1. The safety model (read this first)

Live execution has four independent layers. Paper trading always runs and
cannot be turned off.

| Layer | Control | Where it is read | State on live box |
|---|---|---|---|
| 0. Instance ceiling | env `VANTAGE_LIVE_TRADING` must be truthy | `backend/trading_mode.py` (`env_ceiling_open`, `global_live_enabled`) | **unset**, so closed |
| 1. Operator master switch | `live_mode` in `app_flags`/audit, set via `PUT /api/trading/live-mode` (needs a human signoff card) | `trading_mode.py`, `sentinel/live_controls.py` | off (`live_mode` value `0`) |
| 2. Account kill switch | `PUT /api/trading/live-killswitch` (Settings > Security) | `trading_mode.kill_switch_engaged` (fails closed) | not engaged (as far as the DB shows) |
| 3. Per-strategy opt-in | strategy must be `armed=1` AND `live=1`; `enable-live` refuses without Brier evidence | `routers/trading.py` `enable_live_strategy`, `brier_calibration_gate.py` | see section 6 |
| 4. Wallet binding | order's wallet must belong to the agent and hold a key; Solana only | `execute-live` | per order |

`execution_engine.process_order` re-checks layers 0, settlement, and 2 before
any key is decrypted. `execute_live_order` (the only real-money route) checks
them again and refuses non-Solana wallets. Manual orders with no strategy are
gated by layers 0 and 2 only; that is deliberate for `quick-trade`, but it means
a manual order skips the per-strategy opt-in and the Brier evidence check.

### Dead code, do not rely on it

`backend/trading_gate.py` (`VANTAGE_AUTOTRADE` + `app_flags['autotrade']`) is
**not imported by any production module**. Only two test files import it. The
live box does set `VANTAGE_AUTOTRADE=1`, and that flag gates nothing. Treat it
as a leftover; do not add a gate there.

## 2. The signal pipeline

```
producers (daemons, guild agents, webhooks, curve listener, Helius, DexScreener,
           Polymarket, Pump.fun, runner/early-buyer intel)
   -> signal_record.py     canonical record: directions long/short/hold/exit/alert,
                           NEW_SOURCE_CAP = 0.69 conviction ceiling for new sources
   -> signal_ledger.py     append-only ledger (63,893 rows), mirrored to Mycelium
   -> signal_pool (500)    in-memory/bounded pool; fused by signal_fusion.py
                           (enriches from Loom, context, strategy-lab, confluence)
   -> aggregate_score.py   WEIGHTS-based score over smart money, social,
                           narrative, whale presence, market snapshot, rug checks
   -> pipeline.promotable_signals(min_conviction=0.75, max_age_min=60)
   -> routers/pipeline.py  POST promote  (the only promotion door)
   -> trading_decision.jev_decide (threshold 0.65, env JEV_CONFIDENCE_THRESHOLD)
      / jev_decision.py    LLM decision via the free-pool LiteLLM on :8300
   -> trading_orders       status pending -> paper fill or live execution
```

Other doors into `trading_orders`:

- `POST /api/trading/signals/ingest` (`routers/trading.py` ~2704): with
  `SIGNAL_AUTO_EXECUTE_ENABLED=1` (**set on the live box**), a BUY/LONG/BULLISH
  or SELL/SHORT/BEARISH signal with conviction > 0.7 creates an order
  automatically. The order is idempotent on `signal_id`. It is still subject to
  layers 0 to 4 at execution time. Paper is the only thing that happens while
  the ceiling is closed.
- `POST /api/trading/quick-trade`: manual order, then `execute_live_order`.
- `POST /api/trading/copytrade` routes (`routers/copytrade.py`): subscribe to a
  leader, copy an order via `POST /copy/{order_id}`. Copies are ordinary orders.
- `daemons/strategy_bots.py` (**untracked**, another agent's WIP): paper-trades
  every strategy each cycle. Do not commit it from this map's branch.

Thresholds to know: promotion 0.75; auto-execute 0.7; JEV 0.65; new-source cap
0.69; Brier gate window 100, closes above 0.35 (`brier_calibration_gate.py`).

## 3. Execution and fills

- `execution_engine.py` (566 lines): `process_order` routes by chain. Solana via
  Jupiter (`_process_solana_order`, quote + liquidity check, `_confirm_solana_tx`);
  Hyperliquid (`_process_hyperliquid_order`). Daily SOL spend cap for buys
  (`TRADING_DAILY_SOL_CAP`), cooldown between live trades
  (`TRADING_COOLDOWN_SECONDS`), max concurrent pending
  (`VANTAGE_TRADING_MAX_CONCURRENT_PENDING=10` on the box).
- Kill switch read live from the env file (`_kill_switch_active`), so a
  `VANTAGE_TRADING_KILL_SWITCH` change takes effect without restart.
- Paper fills: `POST /api/trading/orders/{id}/paper-fill` (`routers/trading.py`
  ~1148). Tagged `tx_hash='paper:<uuid>'`. Every consumer (`proof_bridge.py`,
  `mycelium_bridge.py`) reads that prefix to know a fill is simulated.
- Position-level routes: `strategies/*` (create, template, patch, toggle, arm,
  disarm, enable-live, disable-live, delete), `orders` (create, patch, cancel,
  journal), `wallets` (create, generate, onramp, sync, patch, delete).

## 4. Learning and calibration (what decides whether a strategy is trusted)

- `trade_outcome_learner.py` (499 lines): snapshots submitted entries, records
  outcomes (filters probe/test prefixes, sanity-caps PnL at 1000%), refreshes
  `source_performance` every loop (600 s).
- `brier_calibration_gate.py` (363 lines): rolling Brier over the last 100
  scored predictions (Polymarket, Limitless, Kalshi). Above 0.35 the gate closes
  and `enable-live` returns 503 ("live execution stays off").
- `tradingos_challenger.py`: sends promotable signals to the TradingOS challenge
  endpoint; `VETO_AT = 3` high-severity findings vetoes. Enabled by
  `TRADINGOS_CHALLENGER_ENABLED`.
- `trade_recording.py`: per-account prefs for recording live trades and the
  leaderboard opt-in. Defaults: record on, leaderboard off.
- `pipeline/learning` and `shadow_rank.py`: a shadow ranking that is logged, not
  acted on. Deliberately not wired into `promotable_signals`.

## 5. Surfaces

Backend routers: `routers/trading.py` (3,356 lines, 60 routes, the core),
`routers/pipeline.py`, `routers/intel.py` (market, Sui, sources, whales, FX),
`routers/alpha.py`, `routers/alpha_hunter.py`, `routers/copytrade.py`,
`routers/trading_space.py` (guild trading spaces: pools, tasks, commitments),
`routers/intel_exchange.py` (imported signals, promotion), `routers/guild_*`.

Frontend (`frontend/src/components/trading/`):

- Terminal: `TradingTerminal.tsx`, `TradingDashboard.tsx`, `ExecutionPanel.tsx`
  (live toggle, kill switch), `StrategiesPanel.tsx`, `Portfolio.tsx`,
  `TradingViewChart.tsx`, `CandleChart.tsx`, `ChartWorkspace.tsx`,
  `PineEditor.tsx` / `PineScriptPanel.tsx`.
- Signals and intel: `IntelFeed.tsx`, `DailyIntel.tsx`, `AgentIntel.tsx`,
  `LearningPanel.tsx`, `EarlyRunners.tsx`, `EarlyRunnerIntel.tsx`,
  `DegenTrenches.tsx`, `Top5Degen.tsx`, `AggregateWinnerBanner.tsx`,
  `SignalMarkerLayer.tsx`.
- Market: `MarketIntel.tsx` with `analytics/` panels (Overview, Whales, FX,
  Arbitrage, Alpha, Yields, DEX, All Tokens, Trace, Sources, Quant backtest).
- Copy and prediction: `CopyTradingPanel.tsx`, `prediction/`,
  `PredictionMarkets.tsx`.
- Wallets: `GenerateWalletModal.tsx`, `OnrampModal.tsx`, `LaunchTokenPanel.tsx`.
- Spaces: `TradingSpaceShell.tsx`, `TradingSpacePool.tsx`, `TradingSpaceTasks.tsx`.
- State: `tradingStore.tsx`.

## 6. Live-box state (2026-11-12, read-only)

- Process env: `VANTAGE_AUTOTRADE=1`, `SIGNAL_AUTO_EXECUTE_ENABLED=1`,
  `SIGNAL_LEDGER_ENABLED=1`. `VANTAGE_LIVE_TRADING` absent, so the ceiling is
  closed and nothing can sign.
- `live_mode` value `0`; last change 2026-11-11 02:32 (brake applied, audit row 91).
- Startup audit rows: every `process_start` since 11-10 reads `paper_only` or
  `live_enabled` with `VANTAGE_LIVE_TRADING=<unset>`.
- **Strategy 9 "Moonbag Big-Hit" is `armed=1`, `live=1`, `enabled=1`, bound to
  wallet 13 (Solana), max position $0.50, stop -40%, take-profit +800%.** It is
  inert only because layer 0 is closed. Setting `VANTAGE_LIVE_TRADING` would
  make it trade real money immediately. Decide this before anyone opens layer 0.
- History: 23 `confirmed` and 7 `filled` orders. The `filled` ones (Nov 7-8)
  are paper. The `confirmed` ones (July 23) carry real Solana transaction hashes,
  so real money has moved through this system before.
- `trading_orders`: 7,594 `failed`, 23 `confirmed`, 11 `cancelled`, 7 `filled`.

## 7. Verification run for this map

`pytest` on the 38 trading-related test modules (filenames matching trad, order,
signal, execut, gate, live, paper, brier, strateg, jev, pipeline, copy, mode,
learn, outcome):

    351 passed, 8 skipped, 22 warnings in 270.01s

## 8. Known gaps and risks (not fixed in this change)

1. Strategy 9 is armed-live with a bound wallet, one env var from real trades.
2. `trading_gate.py` is dead code that still reads like a control. It should be
   deleted in a separate change once its two tests are retired.
3. Manual orders without a strategy skip the per-strategy opt-in and the Brier
   evidence check. Layers 0 and 2 still apply.
4. `SIGNAL_AUTO_EXECUTE_ENABLED=1` on the box means high-conviction signals
   create orders automatically. They execute only once layer 0 opens.
5. `daemons/strategy_bots.py` is untracked in this repo and writes paper orders.
6. The July confirmed orders are real on-chain trades; their PnL is not in
   `trading_pnl_snapshots` for review here.
