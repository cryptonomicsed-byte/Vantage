#!/usr/bin/env python3
"""Bridge: Freqtrade → Vantage trading signals"""
import os, json, sqlite3, urllib.request, time
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
FREQ_DB = "/opt/ares/freqtrade/tradesv3.dryrun.sqlite"
INTERVAL = int(os.environ.get("FREQ_BRIDGE_INTERVAL", "300"))

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def get_recent_trades(since_min: int = 5):
    """Get recent closed trades from freqtrade DB."""
    db = sqlite3.connect(FREQ_DB)
    rows = db.execute("""
        SELECT pair, open_rate, close_rate, close_profit, amount, open_date, close_date
        FROM trades WHERE is_open=0 AND close_date IS NOT NULL
        ORDER BY close_date DESC LIMIT 20
    """).fetchall()
    db.close()
    return rows

def cycle():
    trades = get_recent_trades()
    count = 0
    for pair, open_r, close_r, profit, amt, open_d, close_d in trades:
        symbol = pair.split("/")[0] if "/" in pair else pair[:8]
        direction = "BUY" if profit > 0 else "SELL"
        # Post to trading signals
        vantage_post("/api/trading/signals/ingest", {
            "symbol": symbol,
            "direction": direction,
            "conviction": min(abs(profit) * 5, 7.0),
            "source": "freqtrade",
            "chain": "multi",
            "detail": f"PnL:{profit*100:.1f}% | Amt:{amt} | Entry:{open_r} Exit:{close_r}"
        })
        count += 1
    if count:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Freqtrade: {count} trades ingested")
    return count

if __name__ == "__main__":
    print(f"Freqtrade Bridge started ({INTERVAL}s cycle)")
    while True:
        try: cycle()
        except Exception as e: print(f"Error: {e}")
        time.sleep(INTERVAL)
