#!/usr/bin/env python3
"""Polymarket Trading Daemon — Authenticated trading via Polymarket US API → Vantage.

Reads prediction markets, places/cancels orders, syncs positions + balances
to Vantage portfolio, and ingests high-conviction markets as trading signals.

Requires:
  pip install polymarket-us
  Env vars: PM_KEY_ID, PM_SECRET_KEY, VANTAGE_KEY
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
VANTAGE_KEY  = os.environ.get("VANTAGE_KEY", "")
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
def _headers():
    return {"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}

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
            _post("/api/trading/signals/ingest", {
                "symbol": "PM:ACCOUNT",
                "source": "polymarket_trader",
                "conviction": 3.0,
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
            _post("/api/trading/signals/ingest", {
                "symbol": slug[:10],
                "source": "polymarket_trader",
                "conviction": min(abs(size) * 5, 7.0),
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
            conviction = min(abs(top - 0.5) * 15, 8.0)  # 0–8 scale

            # Auto-place small bets on high-conviction markets
            if conviction >= MIN_CONVICTION * 10 and top > 0.60:
                tag = (m.get("tags", [{}]) or [{}])[0].get("label", "prediction")
                _post("/api/trading/signals/ingest", {
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
