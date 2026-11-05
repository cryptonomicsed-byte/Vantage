#!/usr/bin/env python3
"""Kalshi prediction market read-only collector daemon — Ed25519 signed auth."""
import asyncio, time, sys, os, uuid, json, base64
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

KALSHI_BASE = os.environ.get("KALSHI_BASE", "https://trading-api.kalshi.com/trade-api/v2")
POLL_INTERVAL = 900  # 15 minutes


def _load_signing_key():
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    if not key_path or not os.path.exists(key_path):
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        pem = open(key_path, "rb").read()
        return load_pem_private_key(pem, password=None)
    except Exception as e:
        print(f"[kalshi] key load error: {e}", flush=True)
        return None


def _signed_headers(method: str, path: str, key_id: str, signing_key) -> dict:
    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + method.upper() + path).encode()
    sig = signing_key.sign(msg)
    return {
        "accept": "application/json",
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
    }


async def fetch_kalshi_markets() -> list:
    import httpx
    key_id = os.environ.get("KALSHI_KEY_ID", "")
    signing_key = _load_signing_key()
    path = "/trade-api/v2/markets"
    if signing_key and key_id:
        headers = _signed_headers("GET", path, key_id, signing_key)
    else:
        headers = {"accept": "application/json"}
        print("[kalshi] WARNING: no signing key, unauthenticated", flush=True)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{KALSHI_BASE}/markets",
                headers=headers,
                params={"limit": 100, "status": "open"},
            )
            if r.status_code == 200:
                return r.json().get("markets", [])
            print(f"[kalshi] HTTP {r.status_code}: {r.text[:200]}", flush=True)
    except Exception as e:
        print(f"[kalshi] fetch error: {e}", flush=True)
    return []


async def ingest_markets(markets: list):
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
    print("[kalshi] Starting Kalshi collector daemon (Ed25519 auth)", flush=True)
    while True:
        try:
            markets = await fetch_kalshi_markets()
            if markets:
                await ingest_markets(markets)
                print(f"[kalshi] Ingested {len(markets)} markets", flush=True)
            else:
                print("[kalshi] 0 markets returned", flush=True)
        except Exception as e:
            print(f"[kalshi] Error: {e}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
