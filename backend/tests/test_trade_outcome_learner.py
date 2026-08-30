"""Tests for backend/trade_outcome_learner.py — the per-source PnL feedback
loop that GET /api/trading/source-performance has referenced by name since
it was written, but never actually existed in the committed codebase
(found 2026-08-29 while wiring Pine Script indicators into it). Schema
matches the REAL production trading_order_outcomes table discovered live
on deploy (one row per order, pnl_pct_1h/pnl_pct_24h columns) rather than
the two-rows-per-order design first tried and rejected on first deploy
attempt ("no such column: t.window") -- see the module docstring.
"""
import datetime

import aiosqlite
import pytest

from backend.trade_outcome_learner import (
    _source_for_order,
    init_outcome_tables,
    record_outcomes_once,
    refresh_source_performance,
    run_once,
    snapshot_submitted_entries,
)


async def _seed_agent_and_orders(db_mod, orders):
    """orders: list of dicts with id, side, symbol, avg_fill_price,
    status, trigger_reason, strategy_id, hours_ago (None = not executed)."""
    await db_mod.init_agents_db()
    async with db_mod.get_db() as db:
        await db.execute("INSERT INTO agents (id, name, api_key) VALUES (1, 'test-agent', 'k')")
        for o in orders:
            executed_at = None
            if o.get("hours_ago") is not None:
                executed_at = (
                    datetime.datetime.utcnow() - datetime.timedelta(hours=o["hours_ago"])
                ).isoformat()
            await db.execute(
                "INSERT INTO trading_orders (id,agent_id,side,symbol,chain,quantity,"
                "filled_quantity,avg_fill_price,status,trigger_reason,strategy_id,"
                "created_at,executed_at) VALUES (?,1,?,?,'solana',1,1,?,?,?,?,?,?)",
                (o["id"], o["side"], o["symbol"], o.get("avg_fill_price"), o.get("status", "filled"),
                 o.get("trigger_reason", "manual_ui"), o.get("strategy_id"), executed_at, executed_at),
            )
        await db.commit()


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    from backend.config import settings
    import backend.db as db_mod

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "vantage.db")
    return db_mod


class TestSourceTagging:
    def test_strategy_id_wins_over_trigger_reason(self):
        assert _source_for_order({"strategy_id": 7, "trigger_reason": "manual_ui"}) == "strategy:7"

    def test_trigger_reason_used_when_no_strategy(self):
        assert _source_for_order({"strategy_id": None, "trigger_reason": "pine:3:RSI Div"}) == "pine:3:RSI Div"

    def test_falls_back_to_manual_ui_when_both_empty(self):
        assert _source_for_order({"strategy_id": None, "trigger_reason": ""}) == "manual_ui"
        assert _source_for_order({}) == "manual_ui"


@pytest.mark.asyncio
async def test_marks_eligible_order_and_skips_ineligible(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30,
         "trigger_reason": "pine:1:MACD Custom"},
        # Too recent for either window -- must not be marked yet.
        {"id": 2, "side": "BUY", "symbol": "ETH", "avg_fill_price": 3000, "hours_ago": 0.1,
         "trigger_reason": "manual_ui"},
        # SELL side is never marked (docstring: "every buy order").
        {"id": 3, "side": "SELL", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30,
         "trigger_reason": "manual_ui"},
    ])

    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return {"BTC": 55000.0, "ETH": 3100.0}.get(symbol)
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    marked = await record_outcomes_once()
    assert marked["1h"] == 1
    assert marked["24h"] == 1

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM trading_order_outcomes")).fetchall()
    rows = [dict(r) for r in rows]
    assert len(rows) == 1  # one row PER ORDER (real schema), order 1 only
    row = rows[0]
    assert row["order_id"] == 1
    assert row["source"] == "pine:1:MACD Custom"
    assert row["pnl_pct_1h"] == pytest.approx(10.0)  # (55000-50000)/50000 * 100
    assert row["pnl_pct_24h"] == pytest.approx(10.0)
    assert row["evaluated_1h_at"] is not None
    assert row["evaluated_24h_at"] is not None


@pytest.mark.asyncio
async def test_idempotent_second_run_does_not_duplicate(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return 55000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    first = await record_outcomes_once()
    second = await record_outcomes_once()
    assert sum(first.values()) == 2  # 1h + 24h both marked once
    assert sum(second.values()) == 0  # already marked -- no duplicates

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT COUNT(*) AS c FROM trading_order_outcomes")).fetchall()
    assert dict(rows[0])["c"] == 1  # one row per order, not one per window


@pytest.mark.asyncio
async def test_missing_quote_skips_without_marking(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "UNKNOWNCOIN", "avg_fill_price": 50000, "hours_ago": 30},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return None  # no live quote available
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    marked = await record_outcomes_once()
    assert marked["1h"] == 0
    assert marked["24h"] == 0


@pytest.mark.asyncio
async def test_refresh_source_performance_aggregates_correctly(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30,
         "trigger_reason": "pine:1:MACD Custom"},
        {"id": 2, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30,
         "trigger_reason": "pine:1:MACD Custom"},
    ])
    import backend.routers.trading as trading_mod

    # Alternate quotes so order 1 wins (+10%) and order 2 loses (-10%).
    calls = {"n": 0}
    async def alternating_quote(symbol):
        calls["n"] += 1
        return 55000.0 if calls["n"] % 2 == 1 else 45000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", alternating_quote)

    await init_outcome_tables()
    result = await run_once()
    assert result["sources_updated"] == 2  # (source, window) pairs: pine:1... x {1h, 24h}

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM source_performance WHERE source='pine:1:MACD Custom' AND window='1h'"
        )).fetchall()
    row = dict(rows[0])
    assert row["n_trades"] == 2
    assert row["wins"] == 1
    # (10% + -10%) / 2 == 0
    assert row["avg_pnl_pct"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_bootstraps_cleanly_on_a_database_with_no_prior_table(db_env, monkeypatch):
    """A fresh/dev DB (no pre-existing trading_order_outcomes at all) must
    bootstrap correctly via CREATE TABLE IF NOT EXISTS, not just work
    against the real production table's pre-existing shape."""
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30},
    ])
    async with db_env.get_db() as db:
        tables = await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trading_order_outcomes'"
        )).fetchall()
    assert len(tables) == 0  # confirms this fixture starts truly fresh

    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return 55000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    # run_once() itself doesn't bootstrap the schema -- only
    # outcome_learner_loop() does, on startup. Mirror that here.
    await init_outcome_tables()
    result = await run_once()
    assert sum(result["marked"].values()) == 2


# ── Consolidation (2026-08-30): status='submitted' real on-chain orders ──
# execute_live_order never sets avg_fill_price -- only tx_hash/executed_at
# -- so these orders need the two-phase snapshot-then-evaluate path ported
# from the now-retired standalone /opt/ares/trade_outcome_learner.py daemon.

@pytest.mark.asyncio
async def test_snapshot_submitted_entries_creates_a_real_row(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "status": "submitted", "hours_ago": 0.01,
         "trigger_reason": "strategy_terminal"},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return 60000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        snapshotted = await snapshot_submitted_entries(db)
        await db.commit()
    assert snapshotted == 1

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM trading_order_outcomes")).fetchall()
    row = dict(rows[0])
    assert row["order_id"] == 1
    assert row["entry_price_usd"] == pytest.approx(60000.0)
    assert row["entry_recorded_at"] is not None
    assert row["source"] == "strategy_terminal"
    assert row["pnl_pct_1h"] is None  # not yet evaluated -- only snapshotted


@pytest.mark.asyncio
async def test_snapshot_submitted_entries_no_quote_is_retried_not_faked(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "UNKNOWNCOIN", "status": "submitted", "hours_ago": 0.01},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return None
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        snapshotted = await snapshot_submitted_entries(db)
    assert snapshotted == 0

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT COUNT(*) AS c FROM trading_order_outcomes")).fetchall()
    assert dict(rows[0])["c"] == 0


@pytest.mark.asyncio
async def test_submitted_order_evaluated_once_entry_recorded_at_ages_past_window(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "status": "submitted", "hours_ago": 30,
         "trigger_reason": "strategy_terminal"},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return 60000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    # Snapshot first (as record_outcomes_once itself would do), then
    # backdate entry_recorded_at to simulate real elapsed time -- the
    # snapshot always uses datetime('now'), so this is the only way to
    # test window-eligibility without literally waiting.
    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        await snapshot_submitted_entries(db)
        await db.execute(
            "UPDATE trading_order_outcomes SET entry_recorded_at = datetime('now', '-30 hours') WHERE order_id = 1"
        )
        await db.commit()

    async def rising_quote(symbol):
        return 66000.0  # +10% vs the 60000 entry snapshot
    monkeypatch.setattr(trading_mod, "_fetch_quote", rising_quote)

    marked = await record_outcomes_once()
    assert marked["1h"] == 1
    assert marked["24h"] == 1

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM trading_order_outcomes WHERE order_id=1")).fetchall()
    row = dict(rows[0])
    assert row["pnl_pct_1h"] == pytest.approx(10.0)
    assert row["pnl_pct_24h"] == pytest.approx(10.0)
    # Entry price stays the real snapshot, never overwritten by a later mark.
    assert row["entry_price_usd"] == pytest.approx(60000.0)


@pytest.mark.asyncio
async def test_submitted_order_too_recent_is_not_evaluated_yet(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "status": "submitted", "hours_ago": 0.01},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return 60000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    marked = await record_outcomes_once()  # snapshot happens now, entry_recorded_at = right now
    assert marked["1h"] == 0
    assert marked["24h"] == 0

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM trading_order_outcomes WHERE order_id=1")).fetchall()
    row = dict(rows[0])
    assert row["entry_price_usd"] == pytest.approx(60000.0)  # snapshotted
    assert row["pnl_pct_1h"] is None  # but not yet evaluated -- too recent


@pytest.mark.asyncio
async def test_filled_and_submitted_orders_both_marked_in_one_cycle_no_cross_contamination(db_env, monkeypatch):
    """The real consolidation scenario: one paper-filled order and one real
    on-chain submitted order, same cycle, must each get their own correct
    independent entry price and pnl -- never mixed up."""
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "status": "filled", "hours_ago": 30},
        {"id": 2, "side": "BUY", "symbol": "ETH", "status": "submitted", "hours_ago": 30},
    ])
    import backend.routers.trading as trading_mod
    await init_outcome_tables()

    # Order 2 (submitted) needs its snapshot to land BEFORE the 30h-ago
    # backdate below, matching real usage (record_outcomes_once snapshots
    # first, then evaluates) -- but since this test needs both entry AND
    # eval to reflect the SAME 30h-ago window, snapshot then backdate here
    # too, same technique as the dedicated snapshot test above.
    async def snapshot_quote(symbol):
        return {"ETH": 3000.0}.get(symbol)
    monkeypatch.setattr(trading_mod, "_fetch_quote", snapshot_quote)
    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        await snapshot_submitted_entries(db)
        await db.execute(
            "UPDATE trading_order_outcomes SET entry_recorded_at = datetime('now', '-30 hours') WHERE order_id = 2"
        )
        await db.commit()

    async def eval_quote(symbol):
        return {"BTC": 55000.0, "ETH": 3300.0}.get(symbol)
    monkeypatch.setattr(trading_mod, "_fetch_quote", eval_quote)

    marked = await record_outcomes_once()
    assert marked["1h"] == 2
    assert marked["24h"] == 2

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = {r["order_id"]: dict(r) for r in await (await db.execute("SELECT * FROM trading_order_outcomes")).fetchall()}
    assert rows[1]["entry_price_usd"] == pytest.approx(50000.0)
    assert rows[1]["pnl_pct_1h"] == pytest.approx(10.0)  # (55000-50000)/50000
    assert rows[2]["entry_price_usd"] == pytest.approx(3000.0)
    assert rows[2]["pnl_pct_1h"] == pytest.approx(10.0)  # (3300-3000)/3000


@pytest.mark.asyncio
async def test_snapshot_is_idempotent_no_duplicate_row_on_second_cycle(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "status": "submitted", "hours_ago": 0.01},
    ])
    import backend.routers.trading as trading_mod
    async def fake_quote(symbol):
        return 60000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    await init_outcome_tables()
    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        first = await snapshot_submitted_entries(db)
        await db.commit()
        second = await snapshot_submitted_entries(db)
        await db.commit()
    assert first == 1
    assert second == 0  # already has a row -- NOT EXISTS guard, no re-snapshot

    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT COUNT(*) AS c FROM trading_order_outcomes")).fetchall()
    assert dict(rows[0])["c"] == 1
