"""Pump.fun scan → signal → order pipeline tests.

Exercises the safety filter, conviction mapping, and in-process signal/order
creation against a temp DB with mocked GeckoTerminal data — no network.
"""
import sqlite3
import asyncio
from unittest import mock

import pytest

from backend.routers import pumpfun
from backend.config import settings


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "vantage.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT, api_key TEXT);
        CREATE TABLE trading_wallets (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER,
            label TEXT, chain TEXT, address TEXT, encrypted_private_key TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE trading_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER,
            wallet_id INTEGER, order_type TEXT, side TEXT, symbol TEXT, chain TEXT,
            quantity REAL, trigger_reason TEXT, status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')));
    """)
    con.execute("INSERT INTO agents VALUES (1,'trader','k')")
    con.execute("INSERT INTO trading_wallets (id,agent_id,label,chain,address) "
                "VALUES (1,1,'main','solana','85SFwallet')")
    con.commit(); con.close()
    monkeypatch.setattr(pumpfun, "DB", path)
    return path


def test_safety_filter_volume_floor():
    ok, why = pumpfun._passes_safety(
        {"volume_usd": {"h24": 100}, "transactions": {"h24": {"buys": 50, "sells": 50}}},
        min_volume=5000, max_top5_pct=40)
    assert not ok and "volume" in why


def test_safety_filter_txn_floor():
    ok, why = pumpfun._passes_safety(
        {"volume_usd": {"h24": 9000}, "transactions": {"h24": {"buys": 3, "sells": 2}}},
        min_volume=5000, max_top5_pct=40)
    assert not ok and "txns" in why


def test_safety_filter_passes_healthy_pool():
    ok, why = pumpfun._passes_safety(
        {"volume_usd": {"h24": 50000}, "reserve_in_usd": "12000",
         "transactions": {"h24": {"buys": 200, "sells": 120}}},
        min_volume=5000, max_top5_pct=40)
    assert ok and why == "ok"


def test_conviction_rewards_momentum_and_buy_ratio():
    hot = pumpfun._conviction_from_pool(
        {"price_change_percentage": {"h24": 80}, "transactions": {"h24": {"buys": 300, "sells": 60}}})
    cold = pumpfun._conviction_from_pool(
        {"price_change_percentage": {"h24": 2}, "transactions": {"h24": {"buys": 40, "sells": 90}}})
    assert hot > cold
    assert 0.0 <= cold <= hot <= 0.99


def test_high_conviction_creates_order(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "TRADING_MAX_SOL_PER_ORDER", 0.01)
    res = pumpfun._record_pumpfun_signal(
        agent_id=1, symbol="WIF", mint="EKpQmint", conviction=0.85,
        reason="trending", conviction_threshold=0.72, max_sol=0.01)
    assert res["signaled"] and res.get("order_created")
    assert res["quantity_sol"] == 0.01

    con = sqlite3.connect(temp_db)
    order = con.execute("SELECT side, symbol, chain, quantity, status FROM trading_orders").fetchone()
    signal = con.execute("SELECT type, symbol, conviction FROM trading_signals").fetchone()
    con.close()
    assert order == ("BUY", "EKpQmint", "solana", 0.01, "pending")
    assert signal[0] == "pumpfun" and signal[2] == 0.85


def test_low_conviction_signals_without_order(temp_db):
    res = pumpfun._record_pumpfun_signal(
        agent_id=1, symbol="MEH", mint="mehMint", conviction=0.55,
        reason="trending", conviction_threshold=0.72, max_sol=0.01)
    assert res["signaled"] and "order_created" not in res
    con = sqlite3.connect(temp_db)
    n_orders = con.execute("SELECT COUNT(*) FROM trading_orders").fetchone()[0]
    n_signals = con.execute("SELECT COUNT(*) FROM trading_signals").fetchone()[0]
    con.close()
    assert n_orders == 0 and n_signals == 1


def test_scan_once_skips_without_agent(monkeypatch):
    monkeypatch.setattr(settings, "PUMPFUN_SCAN_AGENT_ID", 0)
    out = asyncio.run(pumpfun.pumpfun_scan_once())
    assert out["status"] == "skipped"


def test_scan_once_end_to_end(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "PUMPFUN_SCAN_AGENT_ID", 1)
    monkeypatch.setattr(settings, "PUMPFUN_MIN_VOLUME_USD", 5000)
    monkeypatch.setattr(settings, "PUMPFUN_SCAN_CONVICTION", 0.72)
    monkeypatch.setattr(settings, "TRADING_MAX_SOL_PER_ORDER", 0.01)

    gecko = {
        "data": [{
            "attributes": {"name": "WIF / SOL", "volume_usd": {"h24": 90000},
                           "reserve_in_usd": "40000",
                           "price_change_percentage": {"h24": 60},
                           "transactions": {"h24": {"buys": 400, "sells": 100}}},
            "relationships": {"base_token": {"data": {"id": "tok_wif"}}},
        }, {
            "attributes": {"name": "RUG / SOL", "volume_usd": {"h24": 10},  # filtered
                           "transactions": {"h24": {"buys": 1, "sells": 1}}},
            "relationships": {"base_token": {"data": {"id": "tok_rug"}}},
        }],
        "included": [{"id": "tok_wif", "attributes": {"address": "WIFmintAddr"}}],
    }
    monkeypatch.setattr(pumpfun, "_fetch", lambda *a, **k: gecko)
    out = asyncio.run(pumpfun.pumpfun_scan_once())
    assert out["status"] == "ok"
    assert out["signaled"] == 1 and out["skipped"] == 1
    assert len(out["orders"]) == 1

    con = sqlite3.connect(temp_db)
    order = con.execute("SELECT symbol, side, quantity FROM trading_orders").fetchone()
    con.close()
    assert order == ("WIFmintAddr", "BUY", 0.01)
