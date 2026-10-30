#!/usr/bin/env python3
"""Polymarket prediction market read-only collector daemon."""
import asyncio, time, sys, os, uuid, json
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

POLYMARKET_BASE = "https://clob.polymarket.com"
POLL_INTERVAL = 900  # 15 minutes

async def fetch_polymarket_markets() -> list[dict]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{POLYMARKET_BASE}/markets", params={"limit": 50, "active": "true"})
            if r.status_code == 200:
                data = r.json()
                return data.get("data", data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"[polymarket] fetch error: {e}", flush=True)
    return []

async def ingest_markets(markets: list[dict]):
    from backend.db import get_db
    async with get_db() as db:
        for m in markets:
            sig_id = str(uuid.uuid4())
            tokens = m.get("tokens", [{}])
            yes_token = next((t for t in tokens if t.get("outcome", "").lower() == "yes"), tokens[0] if tokens else {})
            no_token = next((t for t in tokens if t.get("outcome", "").lower() == "no"), {})
            await db.execute(
                """INSERT OR IGNORE INTO prediction_signals
                   (id, source, market_id, question, yes_price, no_price, volume_24h, end_date, category, raw_json, ingested_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sig_id, "polymarket", m.get("condition_id", m.get("id", "")),
                 m.get("question", ""), float(yes_token.get("price", 0.5)),
                 float(no_token.get("price", 0.5)), float(m.get("volume24hr", 0.0)),
                 m.get("end_date_iso", ""), m.get("category", ""), json.dumps(m), int(time.time()))
            )
        await db.commit()

async def main():
    print("[polymarket] Starting Polymarket collector daemon", flush=True)
    while True:
        try:
            markets = await fetch_polymarket_markets()
            if markets:
                await ingest_markets(markets)
                print(f"[polymarket] Ingested {len(markets)} markets", flush=True)
        except Exception as e:
            print(f"[polymarket] Error: {e}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
