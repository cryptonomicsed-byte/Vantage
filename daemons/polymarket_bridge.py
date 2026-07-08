#!/usr/bin/env python3
"""Bridge: Polymarket → Vantage intel signals"""
import os, json, urllib.request, time
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
POLYMARKET_URL = "https://gamma-api.polymarket.com"
INTERVAL = int(os.environ.get("POLY_BRIDGE_INTERVAL", "600"))

HEADERS = {"User-Agent": "curl/8.0"}

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

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
        conviction = abs(top_outcome - 0.5) * 10  # 0-5 scale based on certainty
        tag = m.get("tags", [{}])[0].get("label", "prediction") if m.get("tags") else "prediction"
        vantage_post("/api/intel/signals/ingest", {
            "symbol": tag.upper()[:10] if tag else "PREDICT",
            "source": "polymarket",
            "conviction": min(conviction, 7.0),
            "type": "prediction_market",
            "chain": "polygon",
            "detail": f"'{title}' | Vol:${volume:,.0f} | Top outcome:{top_outcome:.1%}"
        })
        count += 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Polymarket: {count} markets ingested")

if __name__ == "__main__":
    print(f"Polymarket Bridge ({INTERVAL}s cycle)")
    while True:
        try: cycle()
        except Exception as e: print(f"Error: {e}")
        time.sleep(INTERVAL)
