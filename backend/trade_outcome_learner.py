"""trade_outcome_learner — the feedback loop backend/routers/trading.py's
GET /api/trading/source-performance has referenced by name since it was
written, but that never actually existed in the committed codebase: no
CREATE TABLE for it anywhere in Python source, and no writer. The endpoint
would 500 on any real call ("no such table"). Built 2026-08-29 while wiring
Pine Script indicators into the learning loop, since that wiring needs a
real, working per-source PnL tracker to attach to -- not a stub.

Real discovery on first deploy attempt: `trading_order_outcomes` and
`source_performance` already existed LIVE on production (data/vantage.db)
with real rows dated 2026-07-23/24. This module's schema was rewritten to
match that real, already-live `trading_order_outcomes` shape (one row PER
ORDER, pnl_pct_1h/pnl_pct_24h as columns).

--- Consolidation (2026-08-30) ---
That "already-live" data was NOT from a lost/deleted writer as first
concluded -- /opt/ares/trade_outcome_learner.py turned out to be a real,
still-running standalone daemon (ares-trade-outcome-learner.service)
writing into these exact same tables, targeting a DIFFERENT (at the time,
non-overlapping) order population: real on-chain BUY orders that reach
status='submitted' via routers/trading.py's execute_live_order, which sets
tx_hash/executed_at but deliberately never sets avg_fill_price -- there is
no real broadcast fill price tracked separately in this schema. This
module's own query only ever covered status='filled' (paper-fills, whose
avg_fill_price IS the real, exact fill price) -- so the two daemons were
two independent, differently-shaped implementations of "mark pnl_pct at
+1h/+24h" writing into one shared table, a real latent consistency risk
even though no actual double-write had occurred in practice (confirmed via
10h of live observation with zero overlap): different price sources, and
no code-enforced guarantee that would stay true if the order lifecycle
ever grew a submitted->filled transition later.

While implementing the fix, live production data surfaced a THIRD real
order-execution path with the identical gap, which the original
consolidation plan (and the standalone daemon it replaces) both missed
entirely: /opt/ares/vantage_execution_engine.py, a separate background
poller (backend/execution_engine.py's real companion, not the same code as
execute_live_order) that sets status='confirmed' -- also never sets
avg_fill_price. Confirmed live: production currently has ZERO orders in
'submitted'/'filled' but 23 real ones in 'confirmed' -- it is currently
the DOMINANT real order population, and neither pre-existing
implementation covered it at all. See NO_FILL_PRICE_STATUSES below.

Fixed by consolidation, not by adding a lock or a "documented boundary"
comment on top of two implementations: this module now ALSO covers every
status in NO_FILL_PRICE_STATUSES ('submitted' AND 'confirmed'), via
snapshot_submitted_entries() (ported from the standalone daemon's own
snapshot_new_entries(), same real reasoning -- no fill price exists to
read directly, so the live quote at first observation is recorded as an
honest proxy for entry) using this module's own richer price source
(routers/trading._fetch_quote's real multi-source fallback chain: Pyth ->
CoinGecko -> RPC proxy, vs the standalone daemon's
DexScreener-only call). The standalone daemon (ares-trade-outcome-
learner.service) is retired -- see ops/README or the deploy commit for the
disable step; its source file is kept on disk (renamed .retired) rather
than deleted, for rollback.

What it does: periodically marks every eligible BUY order's real pnl_pct
at +1h and +24h after entry (against a live quote, not a fabricated
historical price -- same honest "the forward tracking IS the mark"
methodology Mycelium's signal_fusion/backtest.py already documents for the
identical problem: no synthetic historical-price lookup, only real current
quotes recorded once real wall-clock time has actually elapsed), then
aggregates those marks per SOURCE into source_performance for the API to
read.

Source tagging convention (no schema change to trading_orders needed --
trigger_reason is already a free-text column, and the live data already
uses this exact convention: 'manual', 'strategy:9', 'moonshot snipe —
score=65', ...):
    strategy_id set    -> "strategy:<id>"
    else trigger_reason -> used as-is (Pine-triggered orders use
                            "pine:<indicator_id>:<name>", set by
                            routers/pine.py's evaluate-and-fill endpoint)
    else                -> "manual_ui" fallback

2026-08-29: after each cycle's real recompute, also emits real
per-source-performance observation traces into Mycelium's trace substrate
(backend/mycelium_bridge.py) -- so Mycelium's pattern miners have real,
structured source-quality signal to reason over alongside wallet_intel's
and signal_quality's own traces already in that same substrate, not just
the raw numbers this module already exposes via the API above. Fail-soft:
Mycelium being down never affects this module's own real job.
"""
from __future__ import annotations

import asyncio
import logging

import aiosqlite

from .db import get_db
from . import mycelium_bridge

logger = logging.getLogger(__name__)

# (window label, column suffix, hours after entry before eligible)
WINDOWS: tuple[tuple[str, str, float], ...] = (("1h", "1h", 1.0), ("24h", "24h", 24.0))

LOOP_INTERVAL_S = 600  # 10 minutes -- marks don't need to be real-time

# Real terminal BUY-order statuses that never get avg_fill_price populated,
# so need the snapshot-then-evaluate path (see snapshot_submitted_entries):
#   'submitted' -- routers/trading.py's execute_live_order (on-demand HTTP,
#     live-execution). tx_hash/executed_at set, avg_fill_price never is.
#   'confirmed' -- /opt/ares/vantage_execution_engine.py's background
#     execution-engine poller (a SEPARATE real execution path from the one
#     above -- confirmed live 2026-08-30: this is currently the DOMINANT
#     real order population in production, 23 real confirmed orders vs
#     zero currently in 'submitted'/'filled'). Same gap, same fix applies.
# 'filled' (strategy_bots.py's own fills, and paper_fill_order) DOES set
# avg_fill_price directly and is handled by record_outcomes_once()'s
# original, unchanged direct-entry path -- not in this set.
NO_FILL_PRICE_STATUSES: tuple[str, ...] = ("submitted", "confirmed")


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


async def snapshot_submitted_entries(db: aiosqlite.Connection) -> int:
    """Real BUY orders whose terminal status never gets avg_fill_price
    populated (see NO_FILL_PRICE_STATUSES -- both 'submitted' and
    'confirmed' real execution paths, no real broadcast fill price is
    tracked separately in this schema for either, only tx_hash +
    executed_at, the broadcast moment, not a priced fill). Consolidated in
    from the now-retired standalone /opt/ares/trade_outcome_learner.py
    daemon (2026-08-30): the first time we observe such an order with no
    existing outcomes row yet, snapshot a live quote NOW as an honest
    proxy for entry price -- same real limitation the retired daemon
    already carried (the live price at first OBSERVATION, not the exact
    broadcast fill price), now sourced from this module's own richer
    _fetch_quote fallback chain instead of a DexScreener-only call.

    Returns the real count of orders snapshotted this call. Never raises
    -- a symbol with no live quote right now is simply retried next cycle."""
    from .routers.trading import _fetch_quote

    snapshotted = 0
    placeholders = ",".join("?" * len(NO_FILL_PRICE_STATUSES))
    rows = await (await db.execute(
        f"""SELECT o.id, o.symbol, o.agent_id, o.side, o.trigger_reason, o.strategy_id
            FROM trading_orders o
            WHERE o.side='BUY' AND o.status IN ({placeholders}) AND o.executed_at IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM trading_order_outcomes t WHERE t.order_id = o.id)
            LIMIT 50""",
        NO_FILL_PRICE_STATUSES,
    )).fetchall()
    for row in rows:
        order = dict(row)
        price = await _fetch_quote(order["symbol"])
        if not price:
            continue  # no live quote yet -- retried next cycle, never a fake snapshot
        source = _source_for_order(order)
        try:
            await db.execute(
                """INSERT INTO trading_order_outcomes
                   (order_id, agent_id, side, source, strategy_id, entry_price_usd, entry_recorded_at)
                   VALUES (?,?,?,?,?,?, datetime('now'))""",
                (order["id"], order["agent_id"], order["side"], source, order["strategy_id"], price),
            )
            snapshotted += 1
        except aiosqlite.IntegrityError:
            pass  # another process already created this row this instant -- fine, not an error
    return snapshotted


async def record_outcomes_once() -> dict[str, int]:
    """Mark every eligible BUY order's real pnl_pct for each window that
    has come due, across BOTH real order populations this module now
    covers (see module docstring's Consolidation section):
      - status='filled' (paper-fills, strategy_bots.py's own fills): entry
        = avg_fill_price (the real, exact fill price), clock starts at
        executed_at (the real fill moment).
      - status IN NO_FILL_PRICE_STATUSES ('submitted'/'confirmed', real
        on-chain execution via either routers/trading.py's
        execute_live_order or vantage_execution_engine.py's background
        poller): entry = the snapshot snapshot_submitted_entries() already
        recorded, clock starts at that snapshot's own entry_recorded_at
        (the real limitation noted there -- no better real "entry moment"
        exists in this schema for either on-chain path).
    One row per order (matches the real production schema): first eligible
    window INSERTs the row (only ever reached by the filled-order branch --
    submitted/confirmed orders already have their row from the snapshot
    step above), the second UPDATEs the same row's other window columns.
    Returns {window: count marked}."""
    from .routers.trading import _fetch_quote  # local import: avoid a top-level
    # circular import (routers.trading imports nothing from this module, but
    # main.py imports both at startup in an order this keeps independent of).

    marked: dict[str, int] = {label: 0 for label, _, _ in WINDOWS}
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        snapshotted = await snapshot_submitted_entries(db)
        if snapshotted:
            await db.commit()

        no_fill_placeholders = ",".join("?" * len(NO_FILL_PRICE_STATUSES))
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

                    UNION

                    SELECT o.id, o.symbol, o.avg_fill_price, o.trigger_reason,
                           o.strategy_id, o.agent_id, o.side
                    FROM trading_orders o
                    JOIN trading_order_outcomes t ON t.order_id = o.id
                    WHERE o.side='BUY' AND o.status IN ({no_fill_placeholders})
                      AND t.entry_price_usd IS NOT NULL
                      AND datetime(t.entry_recorded_at, ? || ' hours') <= datetime('now')
                      AND t.evaluated_{col_suffix}_at IS NULL

                    LIMIT 200""",
                (hours, *NO_FILL_PRICE_STATUSES, hours),
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


async def _emit_mycelium_traces() -> int:
    """Real per-source-performance observation traces into Mycelium's
    trace substrate (backend/mycelium_bridge.py), tied to this exact cycle
    boundary -- after refresh_source_performance() has just recomputed the
    real table, not on a separate independent timer. Reads the same
    source_performance columns GET /api/trading/source-performance itself
    reads, no reshaping. asyncio.to_thread because mycelium_bridge's HTTP
    call is a blocking urllib request (same convention wallet_intel/
    collector.py already uses on the Mycelium side of this exact
    integration) -- must not block this loop's own event loop.
    Fail-soft: mycelium_bridge itself never raises, but this is wrapped in
    its own try/except anyway so a bug in trace emission can never take
    down real outcome-marking."""
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT source, window, n_trades, wins, avg_pnl_pct, updated_at FROM source_performance"
            )).fetchall()
        return await asyncio.to_thread(mycelium_bridge.emit_source_performance_traces, [dict(r) for r in rows])
    except Exception:
        logger.exception("trade_outcome_learner: mycelium trace emission failed")
        return 0


async def run_once() -> dict:
    marked = await record_outcomes_once()
    sources_updated = await refresh_source_performance()
    mycelium_traces_emitted = await _emit_mycelium_traces()
    return {"marked": marked, "sources_updated": sources_updated, "mycelium_traces_emitted": mycelium_traces_emitted}


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
