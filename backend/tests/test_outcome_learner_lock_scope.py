"""trade_outcome_learner must never hold the database write lock across a
network quote.

record_outcomes_once and snapshot_submitted_entries used to INSERT/UPDATE a
row and then await the next order's live quote inside the same transaction,
committing only at the end -- so SQLite's write lock was held across every
remaining quote (up to 200 per window). On a slow price API that is minutes;
every other writer waits out busy_timeout (90 s) and fails with 'database is
locked'. That is what broke the voice-relay tests on CI, and what the
learner did to production every cycle.

The probe: while each quote is "in flight", a second connection must be able
to take the write lock immediately (BEGIN IMMEDIATE with no busy wait).
"""
import sqlite3

import aiosqlite
import pytest

from backend.db import DB_PATH


@pytest.fixture
def lock_probe(monkeypatch):
    import backend.routers.trading as trading
    seen = []

    async def quote(symbol, mint=None):
        c = sqlite3.connect(DB_PATH, timeout=0, isolation_level=None)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("ROLLBACK")
            seen.append(True)
        except sqlite3.OperationalError:
            seen.append(False)  # someone holds the write lock while we "wait on the network"
        finally:
            c.close()
        return 2.0

    monkeypatch.setattr(trading, "_fetch_quote", quote)
    return seen


async def _orders(status, n, hours_ago, prefix):
    async with aiosqlite.connect(DB_PATH) as db:
        ids = []
        for i in range(n):
            cur = await db.execute(
                """INSERT INTO trading_orders (agent_id, wallet_id, order_type, side, symbol, chain, quantity,
                       trigger_reason, status, avg_fill_price, executed_at)
                   VALUES (1, NULL, 'market', 'BUY', ?, 'solana', 1, ?, ?, ?, datetime('now', ?))""",
                (f"{prefix}{i}", f"{prefix}_producer", status,
                 1.0 if status == "filled" else None, f"-{hours_ago} hours"))
            ids.append(cur.lastrowid)
        await db.commit()
    return ids


async def _outcomes(ids):
    async with aiosqlite.connect(DB_PATH) as db:
        q = ",".join("?" * len(ids))
        return (await (await db.execute(
            f"SELECT COUNT(*) FROM trading_order_outcomes WHERE order_id IN ({q})", ids)).fetchone())[0]


async def test_marking_never_holds_the_write_lock_across_a_quote(client, lock_probe):
    from backend import trade_outcome_learner as tol
    await tol.init_outcome_tables()
    ids = await _orders("filled", 4, 30, "LOCKMARK")
    await tol.record_outcomes_once()
    assert len(lock_probe) >= 4, "the quotes were not reached"
    assert all(lock_probe), f"write lock held during a quote: {lock_probe}"
    assert await _outcomes(ids) == 4  # and the marks still land


async def test_snapshotting_never_holds_the_write_lock_across_a_quote(client, lock_probe):
    from backend import trade_outcome_learner as tol
    from backend.db import get_db
    await tol.init_outcome_tables()
    ids = await _orders("submitted", 4, 2, "LOCKSNAP")
    async with get_db() as db:
        db.row_factory = aiosqlite.Row  # as record_outcomes_once sets it
        await tol.snapshot_submitted_entries(db)
        await db.commit()
    assert len(lock_probe) >= 4 and all(lock_probe), f"write lock held during a quote: {lock_probe}"
    assert await _outcomes(ids) == 4
