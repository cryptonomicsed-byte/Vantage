#!/usr/bin/env python3
"""Kalshi prediction market read-only collector daemon — Ed25519 signed auth."""
import asyncio, time, sys, os, uuid, json, base64
sys.path.insert(0, "/opt/ares/Vantage")
# data/vantage.db, matching every other process (the old default pointed at a
# file nothing writes; the unit overrides it, but a hand-run used the dead path).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/data/vantage.db")

# The collector and the settlement resolver must speak to the same host.
# Verified 2026-11-05: trading-api.kalshi.com returns 401 unauthenticated (it now
# needs the key), api.elections.kalshi.com returns 200. Collecting from one host
# and settling against another is how a market "never settles".
KALSHI_BASE = os.environ.get("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")
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


def _prob(value):
    """Kalshi quotes bids/asks in cents (0-100); the tab expects a probability.

    A value above 1.5 cannot be a probability, so it is divided by 100. The
    dollars fields (yes_bid_dollars etc.) are already 0-1 and pass through.
    Missing values stay None rather than 0.0, which the UI renders as "—" and
    the API nulls, instead of pretending a market is priced at zero.
    """
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 1.5 else v


async def ingest_markets(markets: list):
    """Upsert one row per market. `processed` is left alone on purpose."""
    from backend.db import get_db
    written = 0
    async with get_db() as db:
        for m in markets:
            market_id = m.get("ticker") or m.get("market_id") or ""
            if not market_id:
                continue
            yes_price = _prob(m.get("yes_bid_dollars", m.get("yes_bid")))
            no_price = _prob(m.get("no_bid_dollars", m.get("no_bid")))
            existing = await (await db.execute(
                "SELECT id FROM prediction_signals WHERE source=? AND market_id=?",
                ("kalshi", market_id),
            )).fetchone()
            if existing:
                await db.execute(
                    """UPDATE prediction_signals
                          SET question=?, yes_price=?, no_price=?, volume_24h=?,
                              end_date=?, category=?, raw_json=?, ingested_ts=?
                        WHERE source=? AND market_id=?""",
                    (m.get("title", ""), yes_price, no_price,
                     float(m.get("volume_24h") or 0.0), m.get("close_time", ""),
                     m.get("category", ""), json.dumps(m), int(time.time()),
                     "kalshi", market_id),
                )
            else:
                await db.execute(
                    """INSERT INTO prediction_signals
                       (id, source, market_id, question, yes_price, no_price, volume_24h, end_date, category, raw_json, ingested_ts)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), "kalshi", market_id, m.get("title", ""),
                     yes_price, no_price, float(m.get("volume_24h") or 0.0),
                     m.get("close_time", ""), m.get("category", ""),
                     json.dumps(m), int(time.time())),
                )
            written += 1
            if written % 40 == 0:
                # Same reason as the Polymarket collector: a long single
                # transaction starves the API's own writes. 100 rows is small
                # enough to be harmless, but the pattern is what caused the
                # outage, so it is not left in place for the next venue.
                await db.commit()
                await asyncio.sleep(0)
        await db.commit()
    return written


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
