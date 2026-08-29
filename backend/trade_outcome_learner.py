"""trade_outcome_learner — the feedback loop backend/routers/trading.py's
GET /api/trading/source-performance has referenced by name since it was
written, but that never actually existed in the committed codebase: no
CREATE TABLE for it anywhere in Python source, and no writer. The endpoint
would 500 on any real call ("no such table"). Built 2026-08-29 while wiring
Pine Script indicators into the learning loop, since that wiring needs a
real, working per-source PnL tracker to attach to -- not a stub.

Real discovery on first deploy attempt: `trading_order_outcomes` and
`source_performance` already existed LIVE on production (data/vantage.db)
with real rows dated 2026-07-23/24 -- a genuine prior writer ran at some
point and was lost (never committed, or its source removed) rather than
never existing. This module's schema was rewritten to match that real,
already-live `trading_order_outcomes` shape (one row PER ORDER, pnl_pct_1h/
pnl_pct_24h as columns) instead of the two-rows-per-order design first
tried, which failed loudly on deploy ("no such column: t.window") against
the real table. source_performance's shape already matched by coincidence
(source/window/n_trades/wins/avg_pnl_pct/updated_at, PK(source,window)).
This resumes writing into that real history rather than forking it.

What it does: periodically marks every filled BUY order's real pnl_pct at
+1h and +24h after entry (against a live quote, not a fabricated historical
price -- same honest "the forward tracking IS the mark" methodology
Mycelium's signal_fusion/backtest.py already documents for the identical
problem: no synthetic historical-price lookup, only real current quotes
recorded once real wall-clock time has actually elapsed), then aggregates
those marks per SOURCE into source_performance for the API to read.

Source tagging convention (no schema change to trading_orders needed --
trigger_reason is already a free-text column, and the live data already
uses this exact convention: 'manual', 'strategy:9', 'moonshot snipe —
score=65', ...):
    strategy_id set    -> "strategy:<id>"
    else trigger_reason -> used as-is (Pine-triggered orders use
                            "pine:<indicator_id>:<name>", set by
                            routers/pine.py's evaluate-and-fill endpoint)
    else                -> "manual_ui" fallback
"""
from __future__ import annotations

import asyncio
import logging

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

# (window label, column suffix, hours after executed_at before eligible)
WINDOWS: tuple[tuple[str, str, float], ...] = (("1h", "1h", 1.0), ("24h", "24h", 24.0))

LOOP_INTERVAL_S = 600  # 10 minutes -- marks don't need to be real-time


async def init_outcome_tables() -> None:
    """CREATE TABLE IF NOT EXISTS -- a no-op against the real production
    table (already exists with this exact shape), and a real bootstrap for
    any fresh/dev database that doesn't have it yet."""
    async with get_db() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS trading_order_outcomes (
            order_id INTEGER PRIMARY KEY REFERENCES trading_orders(id),
            agent_id INTEGER,
            mint TEXT,
            side TEXT,
            source TEXT,
            strategy_id INTEGER,
            signal_id INTEGER,
            entry_price_usd REAL,
            entry_recorded_at TEXT DEFAULT (datetime('now')),
            price_1h REAL,
            pnl_pct_1h REAL,
            evaluated_1h_at TEXT,
            price_24h REAL,
            pnl_pct_24h REAL,
            evaluated_24h_at TEXT)""")
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
    that has come due. One row per order (matches the real production
    schema): first eligible window INSERTs the row, the second UPDATEs the
    same row's other window columns. Returns {window: count marked}."""
    from .routers.trading import _fetch_quote  # local import: avoid a top-level
    # circular import (routers.trading imports nothing from this module, but
    # main.py imports both at startup in an order this keeps independent of).

    marked: dict[str, int] = {label: 0 for label, _, _ in WINDOWS}
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        for window_label, col_suffix, hours in WINDOWS:
            rows = await (await db.execute(
                f"""SELECT o.id, o.symbol, o.avg_fill_price, o.trigger_reason,
                           o.strategy_id, o.agent_id, o.side
                    FROM trading_orders o
                    WHERE o.side='BUY' AND o.status='filled'
                      AND o.avg_fill_price IS NOT NULL AND o.avg_fill_price > 0
                      AND o.executed_at IS NOT NULL
                      AND datetime(o.executed_at, ? || ' hours') <= datetime('now')
                      AND NOT EXISTS (
                          SELECT 1 FROM trading_order_outcomes t
                          WHERE t.order_id = o.id AND t.evaluated_{col_suffix}_at IS NOT NULL
                      )
                    LIMIT 200""",
                (hours,),
            )).fetchall()

            for row in rows:
                order = dict(row)
                mark_price = await _fetch_quote(order["symbol"])
                if not mark_price:
                    continue  # try again next cycle rather than mark a fake price
                source = _source_for_order(order)

                existing = await (await db.execute(
                    "SELECT order_id, entry_price_usd FROM trading_order_outcomes WHERE order_id=?",
                    (order["id"],),
                )).fetchone()
                entry = float(existing["entry_price_usd"]) if existing and existing["entry_price_usd"] else float(order["avg_fill_price"])
                pnl_pct = ((mark_price - entry) / entry) * 100.0 if entry else 0.0

                if existing:
                    await db.execute(
                        f"""UPDATE trading_order_outcomes
                            SET price_{col_suffix}=?, pnl_pct_{col_suffix}=?,
                                evaluated_{col_suffix}_at=datetime('now')
                            WHERE order_id=?""",
                        (mark_price, round(pnl_pct, 4), order["id"]),
                    )
                else:
                    await db.execute(
                        f"""INSERT INTO trading_order_outcomes
                            (order_id, agent_id, side, source, strategy_id, entry_price_usd,
                             price_{col_suffix}, pnl_pct_{col_suffix}, evaluated_{col_suffix}_at)
                            VALUES (?,?,?,?,?,?,?,?, datetime('now'))""",
                        (order["id"], order["agent_id"], order["side"], source,
                         order["strategy_id"], entry, mark_price, round(pnl_pct, 4)),
                    )
                marked[window_label] += 1
        await db.commit()
    return marked


async def refresh_source_performance() -> int:
    """Recompute source_performance from trading_order_outcomes. Full
    recompute (not incremental) -- the table is small (bounded by real
    order volume) and this keeps the aggregation logic in one obviously
    correct place rather than an error-prone running-average update.
    Each real window's non-null marks are aggregated independently (a row
    only marked at +1h so far still contributes to the '1h' aggregate even
    before its +24h mark lands)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        updated = 0
        for window_label, col_suffix, _ in WINDOWS:
            rows = await (await db.execute(
                f"""SELECT source, COUNT(*) AS n_trades,
                           SUM(CASE WHEN pnl_pct_{col_suffix} > 0 THEN 1 ELSE 0 END) AS wins,
                           AVG(pnl_pct_{col_suffix}) AS avg_pnl_pct
                    FROM trading_order_outcomes
                    WHERE pnl_pct_{col_suffix} IS NOT NULL
                    GROUP BY source"""
            )).fetchall()
            for r in rows:
                await db.execute(
                    """INSERT INTO source_performance (source, window, n_trades, wins, avg_pnl_pct, updated_at)
                       VALUES (?,?,?,?,?, datetime('now'))
                       ON CONFLICT(source, window) DO UPDATE SET
                           n_trades=excluded.n_trades, wins=excluded.wins,
                           avg_pnl_pct=excluded.avg_pnl_pct, updated_at=excluded.updated_at""",
                    (r["source"], window_label, r["n_trades"], r["wins"], round(float(r["avg_pnl_pct"]), 4)),
                )
                updated += 1
        await db.commit()
        return updated


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
