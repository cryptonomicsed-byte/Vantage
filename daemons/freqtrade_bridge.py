#!/usr/bin/env python3
"""Bridge: Freqtrade → Vantage signals.

Publishes recently *closed* freqtrade trades. Note what that means: this is a
record of what already happened, not a prediction. The original version
derived direction from realised PnL ("it made money, so BUY") and scored
conviction as `min(abs(profit) * 5, 7.0)` -- a 0-7 scale against a platform
contract of 0-1, posted to the order-creating endpoint with an agent key.

The agent key 401'd, which is the only reason this never traded: a 14% winning
trade scored 0.7 on the real scale and would have auto-created an order on
every cycle, in the direction of a position freqtrade had already closed.

Conviction is now a true proportion of the observed range, and the report goes
to the intel pool -- a finished trade is an observation, so it is never routed
to the executing endpoint at all.
"""
import os, sqlite3, time
from datetime import datetime, timezone

from vantage_signals import post_signal

FREQ_DB = os.environ.get("FREQ_DB", "/opt/ares/freqtrade/tradesv3.dryrun.sqlite")
INTERVAL = int(os.environ.get("FREQ_BRIDGE_INTERVAL", "300"))

# Freqtrade profit is a fraction (0.14 = +14%). Treating a 20% move as the top
# of the range keeps a typical win in the middle of the 0-1 band instead of
# pinning it at maximum confidence.
PROFIT_FULL_SCALE = float(os.environ.get("FREQ_PROFIT_FULL_SCALE", "0.20"))

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
        post_signal(
            symbol, "freqtrade",
            type_="closed_trade",
            conviction=abs(profit), scale=PROFIT_FULL_SCALE,
            direction=direction,
            chain="multi",
            detail=f"PnL:{profit*100:.1f}% | Amt:{amt} | Entry:{open_r} Exit:{close_r}",
        )
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
