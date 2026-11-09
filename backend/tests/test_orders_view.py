"""P2-04: source_performance is folded into a stable `orders` view.

Acceptance: the view exists, exposes source/window/n/wins/win_rate/
avg_pnl_pct, and a source with no trades reads win_rate NULL (never 0).
"""
import pytest

from backend import trade_outcome_learner as tol


@pytest.mark.asyncio
async def test_orders_view_folds_source_performance():
    from backend.db import get_db
    async with get_db() as db:
        await tol.init_outcome_tables()
        db.row_factory = None  # fetch tuples for clarity

        await db.execute(
            "INSERT OR REPLACE INTO source_performance "
            "(source, window, n_trades, wins, avg_pnl_pct, n_suspect, updated_at) "
            "VALUES ('src', '1h', 10, 6, 1.5, 0, datetime('now'))"
        )
        await db.execute(
            "INSERT OR REPLACE INTO source_performance "
            "(source, window, n_trades, wins, avg_pnl_pct, n_suspect, updated_at) "
            "VALUES ('empty', '1h', 0, 0, 0, 0, datetime('now'))"
        )
        await db.commit()

        cols = "source, window, n, wins, win_rate"
        src = await (await db.execute(
            f"SELECT {cols} FROM orders WHERE source='src'"
        )).fetchone()
        assert src[0] == "src" and src[2] == 10 and src[3] == 6
        assert abs(src[4] - 0.6) < 1e-9

        empty = await (await db.execute(
            "SELECT win_rate FROM orders WHERE source='empty'"
        )).fetchone()
        assert empty[0] is None  # no trades -> NULL, never 0


@pytest.mark.asyncio
async def test_view_exists_on_empty_table():
    from backend.db import get_db
    async with get_db() as db:
        await tol.init_outcome_tables()
        exists = await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name='orders'"
        )).fetchone()
        assert exists is not None
