"""trade_outcome_learner — the feedback loop backend/routers/trading.py's
GET /api/trading/source-performance has referenced by name since it was
written, but that never actually existed: neither this module nor its two
tables (source_performance, trading_order_outcomes) had a CREATE TABLE
anywhere in the codebase. The endpoint would 500 on any real call ("no such
table"). Built 2026-08-29 while wiring Pine Script indicators into the
learning loop, since that wiring needs a real, working per-source PnL
tracker to attach to -- not a stub.

What it does: periodically marks every filled BUY order's real pnl_pct at
+1h and +24h after entry (against a live quote, not a fabricated historical
price -- same honest "the forward tracking IS the mark" methodology
Mycelium's signal_fusion/backtest.py already documents for the same
problem: there's no synthetic historical-price lookup here, only real
current quotes recorded once real wall-clock time has actually elapsed),
then aggregates those marks per SOURCE (trigger_reason -- 'manual_ui',
'social_telegram', 'pine:<indicator_id>:<name>', or 'strategy:<id>' when a
strategy is set) into source_performance for the API to read.

Source tagging convention (no schema change to trading_orders needed --
trigger_reason is already a free-text column):
    strategy_id set    -> "strategy:<id>"
    else trigger_reason -> used as-is (already how manual_ui/social_telegram
                            trades are tagged; Pine-triggered orders use
                            "pine:<indicator_id>:<name>", set by
                            routers/pine.py's evaluate-and-fill endpoint)
    else                -> "manual_ui" fallback
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

# (window label, hours after executed_at before this mark is eligible)
WINDOWS: tuple[tuple[str, float], ...] = (("1h", 1.0), ("24h", 24.0))

LOOP_INTERVAL_S = 600  # 10 minutes -- marks don't need to be real-time


async def init_outcome_tables() -> None:
    async with get_db() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS trading_order_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES trading_orders(id),
            source TEXT NOT NULL,
            window TEXT NOT NULL,
            entry_price REAL NOT NULL,
            mark_price REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            computed_at TEXT DEFAULT (datetime('now')),
            UNIQUE(order_id, window))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS source_performance (
            source TEXT NOT NULL,
            window TEXT NOT NULL,
            n_trades INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            avg_pnl_pct REAL NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (source, window))""")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_outcomes_source ON trading_order_outcomes(source)"
        )
        await db.commit()


def _source_for_order(order: dict) -> str:
    if order.get("strategy_id") is not None:
        return f"strategy:{order['strategy_id']}"
    reason = (order.get("trigger_reason") or "").strip()
    return reason or "manual_ui"


async def record_outcomes_once() -> dict[str, int]:
    """Mark every eligible filled BUY order's real pnl_pct for each window
    that has come due. Returns {window: count marked} for observability."""
    from .routers.trading import _fetch_quote  # local import: avoid a top-level
    # circular import (routers.trading imports nothing from this module, but
    # main.py imports both at startup in an order this keeps independent of).

    marked: dict[str, int] = {label: 0 for label, _ in WINDOWS}
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        for window_label, hours in WINDOWS:
            rows = await (await db.execute(
                """SELECT o.id, o.symbol, o.avg_fill_price, o.trigger_reason, o.strategy_id
                   FROM trading_orders o
                   WHERE o.side='BUY' AND o.status='filled'
                     AND o.avg_fill_price IS NOT NULL AND o.avg_fill_price > 0
                     AND o.executed_at IS NOT NULL
                     AND datetime(o.executed_at, ? || ' hours') <= datetime('now')
                     AND NOT EXISTS (
                         SELECT 1 FROM trading_order_outcomes t
                         WHERE t.order_id = o.id AND t.window = ?
                     )
                   LIMIT 200""",
                (hours, window_label),
            )).fetchall()

            for row in rows:
                order = dict(row)
                mark_price = await _fetch_quote(order["symbol"])
                if not mark_price:
                    continue  # try again next cycle rather than mark a fake price
                entry = float(order["avg_fill_price"])
                pnl_pct = ((mark_price - entry) / entry) * 100.0 if entry else 0.0
                source = _source_for_order(order)
                try:
                    await db.execute(
                        """INSERT INTO trading_order_outcomes
                           (order_id, source, window, entry_price, mark_price, pnl_pct)
                           VALUES (?,?,?,?,?,?)""",
                        (order["id"], source, window_label, entry, mark_price, round(pnl_pct, 4)),
                    )
                    marked[window_label] += 1
                except aiosqlite.IntegrityError:
                    pass  # UNIQUE(order_id, window) -- another cycle beat us to it
        await db.commit()
    return marked


async def refresh_source_performance() -> int:
    """Recompute source_performance from trading_order_outcomes. Full
    recompute (not incremental) -- the table is small (bounded by real
    order volume) and this keeps the aggregation logic in one obviously
    correct place rather than an error-prone running-average update."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT source, window, COUNT(*) AS n_trades,
                      SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                      AVG(pnl_pct) AS avg_pnl_pct
               FROM trading_order_outcomes
               GROUP BY source, window"""
        )).fetchall()
        for r in rows:
            await db.execute(
                """INSERT INTO source_performance (source, window, n_trades, wins, avg_pnl_pct, updated_at)
                   VALUES (?,?,?,?,?, datetime('now'))
                   ON CONFLICT(source, window) DO UPDATE SET
                       n_trades=excluded.n_trades, wins=excluded.wins,
                       avg_pnl_pct=excluded.avg_pnl_pct, updated_at=excluded.updated_at""",
                (r["source"], r["window"], r["n_trades"], r["wins"], round(float(r["avg_pnl_pct"]), 4)),
            )
        await db.commit()
        return len(rows)


async def run_once() -> dict:
    marked = await record_outcomes_once()
    sources_updated = await refresh_source_performance()
    return {"marked": marked, "sources_updated": sources_updated}


async def outcome_learner_loop() -> None:
    """Background task, same shape as main.py's other periodic loops."""
    await init_outcome_tables()
    while True:
        try:
            result = await run_once()
            if any(result["marked"].values()) or result["sources_updated"]:
                logger.info("trade_outcome_learner: %s", result)
        except Exception:
            logger.exception("trade_outcome_learner cycle failed")
        await asyncio.sleep(LOOP_INTERVAL_S)
