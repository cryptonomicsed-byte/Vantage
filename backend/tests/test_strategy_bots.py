"""daemons/strategy_bots.py — paper on every strategy, live only behind the toggle.

The properties that matter: an unarmed/disabled strategy still produces paper
data; nothing becomes a live order unless enabled+armed+live AND the global
switch is on; a skipped position insert never places an order; SMA strategies
are paper-only; copy-trade mirrors are paper fills.
"""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from daemons import strategy_bots as sb  # noqa: E402

SCHEMA = """
CREATE TABLE trading_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, wallet_id INTEGER,
  order_type TEXT, side TEXT, symbol TEXT, chain TEXT, quantity REAL, price REAL, filled_quantity REAL,
  avg_fill_price REAL, status TEXT DEFAULT 'pending', trigger_reason TEXT, signal_id INTEGER,
  strategy_id INTEGER, tx_hash TEXT, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  executed_at TEXT, settled_at TEXT, notes TEXT, slippage_bps INTEGER, copied_from_order_id INTEGER,
  copied_from_agent_id INTEGER, mint TEXT);
CREATE TABLE trading_trade_journal (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, agent_id INTEGER,
  entry_reasoning TEXT, exit_reasoning TEXT, tags TEXT);
CREATE TABLE live_mode (id INTEGER PRIMARY KEY, enabled INTEGER);
CREATE TABLE copy_trading_subscriptions (id INTEGER PRIMARY KEY, follower_agent_id INTEGER,
  leader_agent_id INTEGER, status TEXT, created_at TEXT);
CREATE TABLE trading_wallets (id INTEGER PRIMARY KEY, agent_id INTEGER, chain TEXT);
"""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "v.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO live_mode VALUES (1, 0)")
    c.commit()
    c.close()
    monkeypatch.setattr(sb, "DB_PATH", str(db))
    monkeypatch.delenv(sb.GLOBAL_LIVE_ENV, raising=False)
    c = sb.db_conn(str(db))
    yield c
    c.close()


def strat(**kw):
    base = dict(id=1, agent_id=5, wallet_id=13, strategy_type="scalper_5020", enabled=0, armed=0, live=0,
                take_profit_pct=30, stop_loss_pct=-12, max_position_size_usd=2, max_concurrent_trades=1,
                target_tiers="", target_symbols="", config="{}")
    base.update(kw)
    return base


def cand(mint="M" * 40, price=1.0, tiers=("migrated",)):
    return {"mint": mint, "symbol": "TOK", "sources": {"dex_new", "ogun_degen"}, "conviction": 1,
            "price": price, "tiers": set(tiers), "score": 1}


def orders(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM trading_orders ORDER BY id")]


def test_unarmed_disabled_strategy_still_paper_trades(conn):
    s = strat()
    sb.process_tp_sl(conn, s, {}, [cand()], {})
    o = orders(conn)
    assert len(o) == 1 and o[0]["side"] == "BUY" and o[0]["status"] == "filled"
    assert o[0]["tx_hash"].startswith("paper:")
    assert o[0]["trigger_reason"] == "bot:scalper_5020"


def test_armed_live_strategy_is_paper_while_switch_off(conn):
    s = strat(enabled=1, armed=1, live=1)
    assert not sb.wants_live(s)
    sb.process_tp_sl(conn, s, {}, [cand()], {})
    assert orders(conn)[0]["status"] == "filled"


def test_live_pending_only_when_every_gate_open(conn, monkeypatch):
    monkeypatch.setenv(sb.GLOBAL_LIVE_ENV, "1")
    assert not sb.wants_live(strat(enabled=1, armed=1, live=0))
    assert not sb.wants_live(strat(enabled=0, armed=1, live=1))
    s = strat(enabled=1, armed=1, live=1)
    assert sb.wants_live(s)
    sb.process_tp_sl(conn, s, {}, [cand()], {})
    o = orders(conn)[0]
    assert o["status"] == "pending" and not (o["tx_hash"] or "")
    pos = conn.execute("SELECT * FROM strategy_positions").fetchone()
    assert pos["pending_order_id"] == o["id"] and pos["is_paper"] == 0


def test_env_brake_beats_db_switch(conn, monkeypatch):
    conn.execute("UPDATE live_mode SET enabled=1")
    conn.commit()
    assert sb.global_live_enabled()
    monkeypatch.setenv(sb.GLOBAL_LIVE_ENV, "0")
    assert not sb.global_live_enabled()


def test_take_profit_closes_with_costed_pnl_and_no_duplicate_entry(conn):
    s = strat()
    c = cand(price=1.0)
    sb.process_tp_sl(conn, s, {}, [c], {})
    sb.process_tp_sl(conn, s, {}, [c], {c["mint"]: 1.0})  # already open: no second buy
    assert len(orders(conn)) == 1
    sb.process_tp_sl(conn, s, {}, [], {c["mint"]: 1.40})
    pos = conn.execute("SELECT * FROM strategy_positions").fetchone()
    assert pos["status"] == "closed"
    entry, exit_ = 1.0 * 1.015, 1.40 * 0.985
    assert pos["pnl_pct"] == pytest.approx((exit_ - entry) / entry * 100)
    # cooldown: the same mint is not bought straight back
    sb.process_tp_sl(conn, s, {}, [c], {})
    assert len(orders(conn)) == 2


def test_stop_loss_sign_is_normalised():
    assert sb.tp_sl(strat(take_profit_pct=15, stop_loss_pct=5)) == (15.0, -5.0)
    assert sb.tp_sl(strat(take_profit_pct=None, stop_loss_pct=None)) == (30.0, -12.0)


def test_target_tiers_filter(conn):
    s = strat(target_tiers="pre_migration,just_launch")
    sb.process_tp_sl(conn, s, {}, [cand(tiers=("migrated",))], {})
    assert orders(conn) == []
    sb.process_tp_sl(conn, s, {}, [cand(mint="N" * 40, tiers=("pre_migration",))], {})
    assert len(orders(conn)) == 1


def test_pair_tiers():
    now = time.time() * 1000
    t = sb.pair_tiers({"dexId": "pumpfun", "marketCap": 14000, "pairCreatedAt": now - 3600e3}, now)
    assert t == {"pre_migration", "pumpfun_10k_20k", "just_launch"}
    assert sb.pair_tiers({"dexId": "raydium", "marketCap": 2e6, "pairCreatedAt": now - 48 * 3600e3}, now) == {"migrated"}


def test_sma_strategy_enters_and_exits_paper_only(conn, monkeypatch):
    monkeypatch.setenv(sb.GLOBAL_LIVE_ENV, "1")
    s = strat(id=4, strategy_type="paper", enabled=1, armed=1, live=1, target_symbols="SOL/USDC",
              take_profit_pct=None, stop_loss_pct=None)
    up = [float(i) for i in range(1, 61)]
    sb.process_sma(conn, s, {"fast": 3, "slow": 10}, [], {}, closes_fn=lambda *a: up)
    o = orders(conn)
    assert len(o) == 1 and o[0]["symbol"] == "SOL" and o[0]["status"] == "filled"
    down = up + [40.0] * 5 + [20.0] * 5
    sb.process_sma(conn, s, {"fast": 3, "slow": 10}, [], {}, closes_fn=lambda *a: down)
    o = orders(conn)
    assert [x["side"] for x in o] == ["BUY", "SELL"] and all(x["status"] == "filled" for x in o)


def test_no_paper_model_types_are_skipped():
    assert sb.processor_for("arbitrage") is None and sb.processor_for("manual") is None
    assert sb.processor_for("momentum") is sb.process_sma
    assert sb.processor_for("some_new_type") is sb.process_tp_sl


def test_copy_trade_mirrors_new_leader_fills_as_paper(conn):
    conn.execute("INSERT INTO copy_trading_subscriptions VALUES (1, 9, 5, 'active', datetime('now'))")
    conn.execute("INSERT INTO trading_wallets VALUES (77, 9, 'solana')")
    conn.execute("INSERT INTO trading_orders (agent_id, side, symbol, chain, quantity, avg_fill_price, status) "
                 "VALUES (5, 'BUY', 'OLD', 'solana', 1, 1, 'filled')")
    conn.commit()
    sb.run_copy(conn)  # first sight sets the cursor; history is not back-filled
    assert len(orders(conn)) == 1
    conn.execute("INSERT INTO trading_orders (agent_id, side, symbol, chain, quantity, avg_fill_price, status) "
                 "VALUES (5, 'BUY', 'NEW', 'solana', 2, 3, 'filled')")
    conn.commit()
    sb.run_copy(conn)
    copy = orders(conn)[-1]
    assert copy["agent_id"] == 9 and copy["wallet_id"] == 77 and copy["symbol"] == "NEW"
    assert copy["status"] == "filled" and copy["trigger_reason"] == "copy:5" and copy["copied_from_agent_id"] == 5
    sb.run_copy(conn)
    assert len(orders(conn)) == 3  # not mirrored twice


def test_stats_scoreboard(conn):
    s = strat()
    c = cand(price=1.0)
    sb.process_tp_sl(conn, s, {}, [c], {})
    sb.process_tp_sl(conn, s, {}, [], {c["mint"]: 0.5})
    sb.refresh_stats(conn)
    r = conn.execute("SELECT * FROM strategy_paper_stats").fetchone()
    assert r["n_closed"] == 1 and r["wins"] == 0 and r["win_rate"] == 0
