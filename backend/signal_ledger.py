"""signal_ledger -- every ingested signal, kept forever (handoff P1-03/P1-04).

signal_pool keeps the newest 500 rows and that does not change. The ledger
keeps every signal that reaches either ingest door -- legacy or
signal_record/1 -- with an entry price, so Mycelium can mark it later and
signal_promotions ids still resolve after the pool row is trimmed.

Flag: SIGNAL_LEDGER_ENABLED (default off; off = nothing here runs).

The write is scheduled off the request path: the entry-price quote can take up
to 5 s and the ingest response must not wait for it, and a ledger failure must
never fail an ingest (logged, never raised). drain() lets tests and shutdown
wait for pending writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

QUOTE_TIMEOUT_S = 5.0

DDL = (
    """CREATE TABLE IF NOT EXISTS signal_ledger (
  id TEXT PRIMARY KEY,
  pool_id INTEGER,
  door TEXT NOT NULL,
  schema TEXT,
  source TEXT NOT NULL, source_ref TEXT, type TEXT,
  symbol TEXT, chain TEXT, address TEXT,
  direction TEXT, conviction REAL, conviction_raw REAL,
  horizon_hours REAL DEFAULT 24, target_pct REAL,
  entry_price_usd REAL, entry_price_src TEXT,
  parents TEXT DEFAULT '[]', producer TEXT DEFAULT '{}',
  evidence TEXT DEFAULT '{}', tags TEXT DEFAULT '[]',
  created_at INTEGER, ingested_at INTEGER NOT NULL,
  mirrored_at INTEGER
)""",
    "CREATE INDEX IF NOT EXISTS idx_ledger_source ON signal_ledger(source, ingested_at)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_address ON signal_ledger(address, ingested_at)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_pool ON signal_ledger(pool_id)",
)

_pending: set[asyncio.Task] = set()


def enabled() -> bool:
    return os.environ.get("SIGNAL_LEDGER_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


async def ensure_tables(db) -> None:
    for ddl in DDL:
        await db.execute(ddl)


async def _entry_price(symbol: str, mint: str) -> tuple[Optional[float], str]:
    """The quote path the order flow uses (trading._fetch_quote, mint-exact
    since 816967d: a mint is never priced by its ticker)."""
    try:
        from backend.routers.trading import _fetch_quote
        price = await asyncio.wait_for(_fetch_quote(symbol, mint or None), QUOTE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return None, "none"
    except Exception as e:  # never fail a ledger write on a quote
        logger.debug("ledger quote failed for %s/%s: %s", symbol, mint, e)
        return None, "none"
    if price is None or not price > 0:
        return None, "none"
    return float(price), ("mint" if mint else "symbol")


def legacy_id(door: str, pool_id: Optional[int], signal: dict) -> str:
    if pool_id is not None:
        return f"pool:{pool_id}" if door == "intel" else f"{door}:pool:{pool_id}"
    h = hashlib.sha256(json.dumps(signal, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{door}:{signal.get('ts') or int(time.time())}:{h}"


def row_for(door: str, signal: dict, pool_id: Optional[int], record: Optional[dict]) -> dict:
    """signal: the legacy pool fields. record: the normalised signal_record/1
    (from signal_record.validate) with conviction already capped, or None."""
    now = int(time.time())
    r = record or {}
    asset = r.get("asset") or {}
    prod = r.get("producer")
    return {
        "id": r.get("id") or legacy_id(door, pool_id, signal),
        "pool_id": pool_id,
        "door": door,
        "schema": r.get("schema"),
        "source": str(signal.get("source") or r.get("source") or "unknown")[:64],
        "source_ref": r.get("source_ref"),
        "type": signal.get("type") or r.get("type"),
        "symbol": signal.get("symbol") or asset.get("symbol"),
        "chain": asset.get("chain") or signal.get("chain") or "",
        "address": asset.get("address") or signal.get("mint") or "",
        "direction": r.get("direction") or (signal.get("direction") or ""),
        "conviction": signal.get("conviction"),
        "conviction_raw": r.get("conviction_raw", signal.get("conviction")),
        "horizon_hours": r.get("horizon_hours") or 24.0,
        "target_pct": r.get("target_pct"),
        "parents": json.dumps(r.get("parents") or []),
        "producer": json.dumps(prod if isinstance(prod, dict) else {}),
        "evidence": json.dumps(r.get("evidence") or {}, default=str),
        "tags": json.dumps(r.get("tags") or []),
        "created_at": r.get("created_at") or signal.get("ts") or now,
        "ingested_at": now,
    }


async def write(row: dict) -> bool:
    """Insert one ledger row (with entry price). Idempotent on id. Never raises."""
    try:
        price, src = await _entry_price(row.get("symbol") or "", row.get("address") or "")
        row = {**row, "entry_price_usd": price, "entry_price_src": src}
        from backend.db import get_db
        async with get_db() as db:
            await ensure_tables(db)
            cols = list(row)
            await db.execute(
                f"INSERT OR IGNORE INTO signal_ledger ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", tuple(row[c] for c in cols))
            await db.commit()
        return True
    except Exception as e:
        logger.warning("signal_ledger write failed (ingest unaffected): %s", e)
        return False


def schedule(door: str, signal: dict, pool_id: Optional[int], record: Optional[dict] = None) -> None:
    """Fire-and-forget from an ingest door. No-op when the flag is off."""
    if not enabled():
        return
    try:
        row = row_for(door, signal, pool_id, record)
        t = asyncio.get_running_loop().create_task(write(row))
        _pending.add(t)
        t.add_done_callback(_pending.discard)
    except Exception as e:
        logger.warning("signal_ledger schedule failed (ingest unaffected): %s", e)


async def drain(timeout: float = 30.0) -> None:
    if _pending:
        await asyncio.wait(set(_pending), timeout=timeout)


async def query(source: str = "", address: str = "", since: int = 0, limit: int = 100) -> list[dict]:
    from backend.db import get_db
    import aiosqlite
    where, args = ["ingested_at >= ?"], [int(since or 0)]
    if source:
        where.append("source = ?"); args.append(source)
    if address:
        where.append("address = ?"); args.append(address)
    async with get_db() as db:
        await ensure_tables(db)
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"SELECT * FROM signal_ledger WHERE {' AND '.join(where)} "
            f"ORDER BY ingested_at DESC, rowid DESC LIMIT ?", (*args, int(limit)))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("parents", "producer", "evidence", "tags"):
            try:
                d[k] = json.loads(d[k]) if d[k] else d[k]
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out
