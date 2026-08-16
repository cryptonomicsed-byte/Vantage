#!/usr/bin/env python3
"""Polymarket account daemon — authenticated read of Polymarket US → Vantage.

Reads balances, open positions and top prediction markets from an
authenticated Polymarket account and pushes them into Vantage's intel signal
pool.

It does NOT place or cancel orders. The original version of this file said it
did, and carried a comment about "auto-placing small bets", but no order was
ever submitted -- the only outbound calls are reads plus Vantage ingestion.
The docstring is corrected rather than the behaviour: a daemon that silently
trades is not something to introduce by fixing a comment.

Everything here is account state and market observation, so it posts to
/api/intel/signals/ingest (the scored, non-executing pool) rather than
/api/trading/signals/ingest (which auto-creates a real order above 0.7
conviction). A balance reading is not a directional trade signal, and routing
it to the executing endpoint is how "sync my portfolio" becomes "buy
something". Same reasoning as the note at the top of routers/telegram_webhook.py.

Requires:
  pip install polymarket-us
  Env vars: PM_KEY_ID, PM_SECRET_KEY, VANTAGE_TOOL_INTEL_KEY
"""

import os, json, time, sys
from datetime import datetime, timezone

try:
    from polymarket_us import PolymarketUS
except ImportError:
    print("ERROR: pip install polymarket-us")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────
VANTAGE_URL  = os.environ.get("VANTAGE_URL", "http://localhost:8001")
# The ingest endpoints require system-tool auth (get_system_tool), not an agent
# key -- posting X-Agent-Key gets a 401, which is how this daemon originally
# shipped. See ares_alpha_hunter.py for the same pattern done correctly.
TOOL_INTEL_KEY = os.environ.get("VANTAGE_TOOL_INTEL_KEY", os.environ.get("VANTAGE_TOOL_INTEL", ""))
PM_KEY_ID    = os.environ.get("PM_KEY_ID", "")
PM_SECRET_KEY = os.environ.get("PM_SECRET_KEY", "")
INTERVAL     = int(os.environ.get("PM_TRADER_INTERVAL", "120"))  # 2 min cycle
MIN_VOLUME   = float(os.environ.get("PM_MIN_VOLUME", "25000"))
MIN_CONVICTION = float(os.environ.get("PM_MIN_CONVICTION", "0.65"))

if not PM_KEY_ID or not PM_SECRET_KEY:
    print("ERROR: PM_KEY_ID and PM_SECRET_KEY required in env")
    sys.exit(1)

pm = PolymarketUS(key_id=PM_KEY_ID, secret_key=PM_SECRET_KEY)


# ── Vantage HTTP helpers ────────────────────────────────────
INTEL_INGEST = "/api/intel/signals/ingest"

# Conviction is a 0–1 confidence platform-wide. The original file used a 0–8
# scale, which the ingest endpoint now rejects outright -- and would have
# cleared the 0.7 auto-execution threshold on every single signal had it been
# pointed at the trading endpoint.
def _conviction(value: float) -> float:
    return max(0.0, min(1.0, value))


def _headers():
    return {
        "Content-Type": "application/json",
        "X-Vantage-Tool": "intel",
        "X-Vantage-Tool-Key": TOOL_INTEL_KEY,
    }

def _post(endpoint, data):
    import urllib.request
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(), headers=_headers())
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def _get(endpoint):
    import urllib.request
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}", headers=_headers())
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


# ── Cycle ───────────────────────────────────────────────────
def cycle():
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # 1. Get account balances → push to Vantage
    try:
        balances = pm.account.balances()
        for b in balances if isinstance(balances, list) else []:
            _post(INTEL_INGEST, {
                "symbol": "PM:ACCOUNT",
                "source": "polymarket_trader",
                "conviction": _conviction(0.30),
                "type": "portfolio",
                "chain": "polygon",
                "detail": f"Balance: {b.get('asset','?')} = {b.get('balance',0)}"
            })
    except Exception as e:
        print(f"  [{now}] ⚠️ Balances: {e}")

    # 2. Get positions → sync to Vantage
    try:
        positions = pm.portfolio.positions()
        for p in positions if isinstance(positions, list) else []:
            market = p.get("market", {}) or {}
            slug = market.get("slug", p.get("marketSlug", "?"))
            size = float(str(p.get("size", 0) or 0))
            if abs(size) < 0.01:
                continue
            _post(INTEL_INGEST, {
                "symbol": slug[:10],
                "source": "polymarket_trader",
                "conviction": _conviction(abs(size) / 20.0),
                "type": "position",
                "chain": "polygon",
                "detail": f"Pos: {slug} | size={size:.2f} | pnl={p.get('pnl',0)}"
            })
    except Exception as e:
        print(f"  [{now}] ⚠️ Positions: {e}")

    # 3. Top markets → ingest as signals
    count = 0
    try:
        markets = pm.markets.list({"limit": 30})
        for m in markets if isinstance(markets, list) else markets.get("data", []):
            slug   = m.get("slug", "?")
            title  = m.get("question", m.get("title", ""))[:100]
            volume = float(str(m.get("volume24hr", m.get("volume", 0)) or 0))
            prices = m.get("outcomePrices", [])

            if volume < MIN_VOLUME:
                continue

            top = max(float(o) for o in prices) if prices else 0.5
            conviction = _conviction(abs(top - 0.5) * 2)  # 0-1: 0.5 = coin flip, 1.0 = certain

            # Record the market as an intel signal. Despite the original
            # comment here, no bet is placed -- see the module docstring.
            if conviction >= MIN_CONVICTION and top > 0.60:
                tag = (m.get("tags", [{}]) or [{}])[0].get("label", "prediction")
                _post(INTEL_INGEST, {
                    "symbol": slug[:10],
                    "source": "polymarket_trader",
                    "conviction": conviction,
                    "type": "prediction_market",
                    "chain": "polygon",
                    "detail": f"'{title}' | Vol:${volume:,.0f} | Top:{top:.1%} | {tag}"
                })
                count += 1

    except Exception as e:
        print(f"  [{now}] ⚠️ Markets: {e}")

    print(f"[{now}] Polymarket Trader: {count} signals ingested")


if __name__ == "__main__":
    print(f"Polymarket Trader ({INTERVAL}s cycle)")
    print(f"  Min volume: ${MIN_VOLUME:,.0f}  Min conviction: {MIN_CONVICTION}")

    # Test connection on startup
    try:
        bal = pm.account.balances()
        print(f"  Connected — {len(bal) if isinstance(bal, list) else '?'} balance entries")
    except Exception as e:
        print(f"  ⚠️ Auth check failed: {e}")
        print("  Daemon will keep retrying...")

    while True:
        try:
            cycle()
        except Exception as e:
            print(f"  ⚠️ Cycle error: {e}")
        time.sleep(INTERVAL)
