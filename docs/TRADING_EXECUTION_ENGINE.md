# Trading Execution Engine

The execution engine turns Vantage into a single-codebase trader: signals become
pending orders, and a background loop settles them on-chain through per-chain
adapters. It replaces the standalone `/opt/ares/ares_*` scripts that read the DB
directly and drifted out of sync on every schema change.

```
pumpfun_scan_loop ──▶ trading_signals ──▶ trading_orders (pending)
                                                │
       execution_loop polls every 5s ◀──────────┘
            │
            ├─ solana       → Jupiter quote+swap, solders sign, Helius submit
            ├─ hyperliquid  → HL SDK (needs ETH on Arbitrum)
            └─ other        → marked "ready" (adapter pending)
```

Everything lives in `backend/` — one schema, one wallet system
(`crypto_utils.py`, per-agent AES-256-GCM), no external daemons.

## Two independent gates

Both default **off**. Nothing trades until you opt in.

| Setting | Effect |
|---|---|
| `VANTAGE_TRADING_ENGINE_ENABLED` | Run the execution loop at all. |
| `VANTAGE_TRADING_LIVE_ENABLED` | Actually sign + submit on-chain. When off, the Solana adapter builds and routes the real Jupiter swap but stops before submission and marks the order `ready` (dry-run). |
| `VANTAGE_PUMPFUN_SCAN_ENABLED` | Run the pump.fun scan → signal → order loop. |

This lets you deploy the wiring, watch dry-run orders reach `ready`, then flip
`TRADING_LIVE_ENABLED` only when you're ready to move funds.

## Configuration

```bash
# Engine
VANTAGE_TRADING_ENGINE_ENABLED=true
VANTAGE_TRADING_LIVE_ENABLED=false        # flip to true for real execution
VANTAGE_TRADING_ENGINE_INTERVAL=5         # seconds between polls
VANTAGE_HELIUS_API_KEY=<key>              # quotes, submit, confirmation
VANTAGE_JUPITER_BASE_URL=https://api.jup.ag/swap/v1

# Safety caps (SOL) — deliberately conservative defaults
VANTAGE_TRADING_MAX_SOL_PER_ORDER=0.01
VANTAGE_TRADING_DAILY_SOL_CAP=0.1
VANTAGE_TRADING_MAX_CONCURRENT_PENDING=5
VANTAGE_TRADING_DEFAULT_SLIPPAGE_BPS=300
VANTAGE_TRADING_COOLDOWN_SECONDS=30

# Pump.fun scanner
VANTAGE_PUMPFUN_SCAN_ENABLED=true
VANTAGE_PUMPFUN_SCAN_AGENT_ID=<agent id that owns the trading wallet>
VANTAGE_PUMPFUN_SCAN_INTERVAL=60
VANTAGE_PUMPFUN_SCAN_CONVICTION=0.72      # >0.7 auto-creates an order
VANTAGE_PUMPFUN_MIN_VOLUME_USD=5000
```

For live signing install the optional extra (dry-run needs nothing extra):

```bash
/opt/ares/venv/bin/pip install 'vantage[trading]'   # solders + base58
```

## Safety guards (Solana adapter)

- **Per-order cap** — rejects any BUY above `TRADING_MAX_SOL_PER_ORDER`.
- **Rolling 24h cap** — sums submitted/filled BUY volume; rejects when the new
  order would exceed `TRADING_DAILY_SOL_CAP`.
- **Concurrency cap** — pauses intake above `TRADING_MAX_CONCURRENT_PENDING`.
- **Rug check** — a BUY whose output token still has an active mint authority is
  rejected (fails closed if the Helius check can't complete).
- **Cooldown** — enforces `TRADING_COOLDOWN_SECONDS` between live submissions;
  an order inside the window stays `pending` for the next poll.

## Order lifecycle

`pending` → adapter runs → `ready` (dry-run) · `failed` (guard/quote/route) ·
`submitted` (tx sent) → `confirmed` (finalized on Helius) / stays `submitted`
if confirmation is still pending.

## Verification (devnet/mainnet cutover)

1. **Dry-run.** Engine on, live off. Create a test order:
   ```bash
   sqlite3 /opt/ares/Vantage/data/vantage.db \
     "INSERT INTO trading_orders (agent_id, wallet_id, order_type, side, symbol, chain, quantity, status)
      VALUES (1, <wallet_id>, 'market', 'BUY', 'BONK/SOL', 'solana', 0.005, 'pending');"
   ```
   Within ~5s the order should move to `ready` with the routed quote in `error`.
2. **Fund** the trading wallet (e.g. `85SFCu…`) with a small amount of SOL.
3. **Go live.** Set `TRADING_LIVE_ENABLED=true`, install `vantage[trading]`,
   restart. Repeat step 1 with a tiny quantity; watch it reach `submitted` then
   `confirmed`, with `tx_hash` populated.
4. **Retire the standalone scripts.** Once the engine is confirmed:
   - stop the `ares_all_traders.py` daemon,
   - remove/disable the `solana` and `pumpfun` entries in its config so
     `ares_pumpfun_trader.py` / `ares_jupiter_signer.py` no longer run,
   - keep them archived until a full week of engine operation looks healthy.

## Tests

`backend/tests/test_execution_engine.py` (routing, every safety guard,
dry-run Jupiter path with mocked HTTP) and
`backend/tests/test_pumpfun_pipeline.py` (safety filter, conviction, in-process
signal/order creation, end-to-end scan with mocked GeckoTerminal). No network or
funds are touched; live submission is never exercised in tests.
