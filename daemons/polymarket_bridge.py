#!/usr/bin/env python3
"""Bridge: Polymarket public markets → Vantage intel signals.

Unauthenticated read of trending prediction markets. (polymarket_trader.py is
the authenticated account-level sibling; neither places a bet.)

Conviction used to be `abs(top - 0.5) * 10` capped at 7.0 -- a 0-5 range with
a 0-7 cap, against a platform contract of 0-1. Any market priced past 57%
scored above 1.0, so the whole feed read as certainty. It is now the same
`* 2` proportion polymarket_trader.py uses: 0.5 is a coin flip, 1.0 is settled.
"""
import os, json, urllib.request, time
from datetime import datetime, timezone

from vantage_signals import post_signal

POLYMARKET_URL = "https://gamma-api.polymarket.com"
INTERVAL = int(os.environ.get("POLY_BRIDGE_INTERVAL", "600"))

HEADERS = {"User-Agent": "curl/8.0"}

def fetch_markets(limit=20):
    """Fetch trending prediction markets."""
    req = urllib.request.Request(
        f"{POLYMARKET_URL}/markets?limit={limit}&order=volume24hr&ascending=false",
        headers=HEADERS
    )
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    return data if isinstance(data, list) else data.get("data", [])

def cycle():
    markets = fetch_markets()
    count = 0
    for m in markets:
        title = m.get("question", m.get("title", ""))[:80]
        raw_prices = m.get("outcomePrices", []); outcomes = json.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices if isinstance(raw_prices, list) else [])
        volume = float(str(m.get("volume24hr", m.get("volume", 0)) or 0))
        if volume < 10000:
            continue
        top_outcome = max(float(o) for o in outcomes) if outcomes else 0
        # 0-1: 0.5 priced = coin flip = 0.0, 1.0 priced = settled = 1.0.
        conviction = abs(top_outcome - 0.5) * 2
        tag = m.get("tags", [{}])[0].get("label", "prediction") if m.get("tags") else "prediction"
        post_signal(
            tag.upper()[:10] if tag else "PREDICT", "polymarket",
            type_="prediction_market",
            conviction=conviction,
            chain="polygon",
            detail=f"'{title}' | Vol:${volume:,.0f} | Top outcome:{top_outcome:.1%}",
        )
        count += 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Polymarket: {count} markets ingested")

if __name__ == "__main__":
    print(f"Polymarket Bridge ({INTERVAL}s cycle)")
    while True:
        try: cycle()
        except Exception as e: print(f"Error: {e}")
        time.sleep(INTERVAL)
