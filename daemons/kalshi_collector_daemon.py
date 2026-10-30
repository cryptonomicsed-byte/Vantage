#!/usr/bin/env python3
"""Kalshi prediction market read-only collector daemon."""
import asyncio, time, sys, os, uuid, json
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
POLL_INTERVAL = 900  # 15 minutes

async def fetch_kalshi_markets(api_key: str = "") -> list[dict]:
    import httpx
    headers = {"accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{KALSHI_BASE}/markets", headers=headers, params={"limit": 50, "status": "open"})
            if r.status_code == 200:
                return r.json().get("markets", [])
    except Exception as e:
        print(f"[kalshi] fetch error: {e}", flush=True)
    return []

async def ingest_markets(markets: list[dict]):
    from backend.db import get_db
    async with get_db() as db:
        for m in markets:
            sig_id = str(uuid.uuid4())
            await db.execute(
                """INSERT OR IGNORE INTO prediction_signals
                   (id, source, market_id, question, yes_price, no_price, volume_24h, end_date, category, raw_json, ingested_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sig_id, "kalshi", m.get("ticker", ""), m.get("title", ""),
                 m.get("yes_bid", 0.0), m.get("no_bid", 0.0),
                 m.get("volume_24h", 0.0), m.get("close_time", ""),
                 m.get("category", ""), json.dumps(m), int(time.time()))
            )
        await db.commit()

async def main():
    api_key = os.environ.get("KALSHI_API_KEY", "")
    print("[kalshi] Starting Kalshi collector daemon", flush=True)
    while True:
        try:
            markets = await fetch_kalshi_markets(api_key)
            if markets:
                await ingest_markets(markets)
                print(f"[kalshi] Ingested {len(markets)} markets", flush=True)
        except Exception as e:
            print(f"[kalshi] Error: {e}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
