"""Tests for backend/trade_outcome_learner.py — the per-source PnL feedback
loop that GET /api/trading/source-performance has referenced by name since
it was written, but never actually existed anywhere (found 2026-08-29 while
wiring Pine Script indicators into it: neither trading_order_outcomes nor
source_performance had a CREATE TABLE, the endpoint 500'd on any real call).
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
    assert len(rows) == 2  # order 1 only, both windows
    assert all(r["order_id"] == 1 for r in rows)
    assert all(r["source"] == "pine:1:MACD Custom" for r in rows)
    pnl = next(r["pnl_pct"] for r in rows if r["window"] == "1h")
    assert pnl == pytest.approx(10.0)  # (55000-50000)/50000 * 100


@pytest.mark.asyncio
async def test_idempotent_second_run_does_not_duplicate(db_env, monkeypatch):
    await _seed_agent_and_orders(db_env, [
        {"id": 1, "side": "BUY", "symbol": "BTC", "avg_fill_price": 50000, "hours_ago": 30},
    ])
    import backend.routers.trading as trading_mod
    monkeypatch.setattr(trading_mod, "_fetch_quote", lambda symbol: 55000.0)

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
    assert dict(rows[0])["c"] == 2


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
    prices = {1: 55000.0, 2: 45000.0}  # one win, one loss
    async def fake_quote(symbol):
        return 50000.0
    monkeypatch.setattr(trading_mod, "_fetch_quote", fake_quote)

    # Need per-order distinct marks -- patch _fetch_quote to alternate.
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
