#!/usr/bin/env python3
"""Polymarket prediction-market collector — read-only, open markets only.

The previous version read `clob.polymarket.com/markets?active=true&limit=50`.
Measured against the live API: it returns 1,000 rows per call regardless of
`limit`, **941 of them closed**, and adding `closed=false` changes nothing --
the CLOB ignores the parameter. Because the collector also minted a fresh uuid
for every row, the same 947 markets were re-inserted every 15 minutes: 92,000
signals, 89,700 with an end_date in the past, 0 in the future. The Prediction
tab correctly flagged that as STALE FEED; this is the fix.

Three changes of substance:

1. **gamma-api**, not the CLOB. Same API the settlement resolver already speaks,
   so collection and settlement agree on one market vocabulary, and it honours
   `closed=false` / `end_date_min`.
2. **Client-side filtering anyway.** A server-side filter that is documented but
   not honoured is worse than no filter, because it looks like a guarantee.
   `is_open()` is applied to every row regardless of what the query asked for.
3. **Upsert by (source, market_id).** One row per market, refreshed in place.
   `processed` is left alone so a market is decided once, not every fifteen
   minutes -- the Brier record is per market, not per poll.
"""
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, "/opt/ares/Vantage")
# data/vantage.db, matching every other process. The old default pointed at
# /opt/ares/Vantage/vantage.db, which no process writes; the unit overrides it,
# so nothing was broken, but a hand-run would have used a dead file.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/data/vantage.db")

GAMMA_BASE = os.environ.get("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com")
POLL_INTERVAL = int(os.environ.get("POLYMARKET_POLL_SECONDS", "900"))
PAGE_LIMIT = 100
MAX_PAGES = int(os.environ.get("POLYMARKET_MAX_PAGES", "6"))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _json_list(value: Any) -> list:
    """gamma returns outcomes/outcomePrices as JSON *strings*."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def is_open(market: dict, now: Optional[datetime] = None) -> bool:
    """Open = still trading and still in the future.

    Deliberately independent of the query parameters. `closed` and `archived`
    are checked explicitly because `closed` is False-by-omission on some rows,
    and an end date in the past means the outcome is already knowable.
    """
    now = now or datetime.now(timezone.utc)
    if market.get("closed") is True or market.get("archived") is True:
        return False
    if market.get("active") is False:
        return False
    end = _parse_dt(market.get("endDate") or market.get("end_date_iso"))
    return end is not None and end > now


def parse_market(market: dict) -> Optional[dict]:
    """gamma market -> the shape prediction_signals stores, or None.

    Prices come from `outcomePrices` aligned to `outcomes`, because the gamma
    schema has no fixed yes/no keys: a market can be ordered either way.
    """
    condition_id = market.get("conditionId") or market.get("condition_id") or market.get("id")
    question = market.get("question") or ""
    if not condition_id or not question:
        return None

    outcomes = [str(o).strip().lower() for o in _json_list(market.get("outcomes"))]
    prices = _json_list(market.get("outcomePrices"))
    yes_price = no_price = None
    for name, price in zip(outcomes, prices):
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        if name in ("yes", "true"):
            yes_price = value
        elif name in ("no", "false"):
            no_price = value

    # Polymarket's binary markets are not always Yes/No: "Up or Down", "Real
    # Madrid vs PSG", "Ospreys vs Seminoles". The venue lists the two sides in
    # outcome order, so the first outcome is the side the quoted price is for.
    # Without this every one of those markets rendered as "—" — 600 open markets
    # with no visible price, which reads as a broken feed rather than a different
    # question shape. The UI labels them from `outcomes`, so nothing is called
    # "yes" that is really a football team.
    if yes_price is None and no_price is None and len(prices) == 2 and len(outcomes) == 2:
        try:
            yes_price, no_price = float(prices[0]), float(prices[1])
        except (TypeError, ValueError):
            pass

    return {
        "market_id": str(condition_id),
        "question": question,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume_24h": float(market.get("volume24hr") or 0.0),
        "end_date": market.get("endDate") or market.get("end_date_iso") or None,
        "category": market.get("category") or "",
        "slug": market.get("slug") or "",
        "raw": market,
    }


async def fetch_open_markets() -> list[dict]:
    """Page through gamma, keeping only markets that are genuinely open."""
    import httpx

    now = datetime.now(timezone.utc)
    out: list[dict] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(MAX_PAGES):
                r = await client.get(
                    f"{GAMMA_BASE}/markets",
                    params={
                        "closed": "false",
                        "active": "true",
                        "limit": PAGE_LIMIT,
                        "offset": page * PAGE_LIMIT,
                        "order": "endDate",
                        "ascending": "true",
                        "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                )
                if r.status_code != 200:
                    print(f"[polymarket] HTTP {r.status_code}: {r.text[:200]}", flush=True)
                    break
                payload = r.json()
                rows = payload if isinstance(payload, list) else payload.get("data", [])
                if not rows:
                    break
                fresh = 0
                for row in rows:
                    if not is_open(row, now):
                        continue
                    parsed = parse_market(row)
                    if not parsed or parsed["market_id"] in seen:
                        continue
                    seen.add(parsed["market_id"])
                    out.append(parsed)
                    fresh += 1
                # A full page with nothing usable means we are reading an
                # archived window: stop rather than paging through history.
                if fresh == 0:
                    break
    except Exception as e:
        print(f"[polymarket] fetch error: {e}", flush=True)
    return out


async def ingest_markets(markets: list[dict]) -> int:
    """Upsert one row per market. `processed` is left alone on purpose.

    Commits every BATCH rows rather than once at the end. A 600-market poll in a
    single transaction held the SQLite write lock for long enough that the API's
    startup migrations timed out and the service came back "active" but never
    bound a port -- a collector starving the app it feeds.
    """
    from backend.db import get_db

    BATCH = 40
    written = 0
    async with get_db() as db:
        for m in markets:
            existing = await (await db.execute(
                "SELECT id FROM prediction_signals WHERE source=? AND market_id=?",
                ("polymarket", m["market_id"]),
            )).fetchone()
            if existing:
                await db.execute(
                    """UPDATE prediction_signals
                          SET question=?, yes_price=?, no_price=?, volume_24h=?,
                              end_date=?, category=?, raw_json=?, ingested_ts=?
                        WHERE source=? AND market_id=?""",
                    (
                        m["question"], m["yes_price"], m["no_price"], m["volume_24h"],
                        m["end_date"], m["category"], json.dumps(m["raw"], default=str),
                        int(time.time()), "polymarket", m["market_id"],
                    ),
                )
            else:
                await db.execute(
                    """INSERT INTO prediction_signals
                       (id, source, market_id, question, yes_price, no_price,
                        volume_24h, end_date, category, raw_json, ingested_ts)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), "polymarket", m["market_id"], m["question"],
                        m["yes_price"], m["no_price"], m["volume_24h"], m["end_date"],
                        m["category"], json.dumps(m["raw"], default=str), int(time.time()),
                    ),
                )
            written += 1
            if written % BATCH == 0:
                await db.commit()
                await asyncio.sleep(0)  # let the API's writers in between batches
        await db.commit()
    return written


async def main():
    print("[polymarket] Starting Polymarket collector (gamma-api, open only)", flush=True)
    while True:
        try:
            markets = await fetch_open_markets()
            if markets:
                n = await ingest_markets(markets)
                print(f"[polymarket] Ingested {n} open markets", flush=True)
            else:
                # Loud, because the failure mode this replaces was silence.
                print("[polymarket] WARNING: 0 open markets returned", flush=True)
        except Exception as e:
            print(f"[polymarket] Error: {e}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
