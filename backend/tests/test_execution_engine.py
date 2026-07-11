"""Execution engine tests — order routing, safety guards, dry-run Jupiter path.

These exercise the engine against a temp SQLite DB with the real schema and a
real encrypted wallet, mocking only the outbound Jupiter/Helius HTTP so no
network or funds are touched. Live submission is never exercised here.
"""
import asyncio
import sqlite3
from unittest import mock

import pytest

from backend import execution_engine as ee
from backend.config import settings
from backend.crypto_utils import encrypt_private_key


def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT, api_key TEXT);
        CREATE TABLE trading_wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, label TEXT,
            chain TEXT, address TEXT, encrypted_private_key TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE trading_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, wallet_id INTEGER,
            order_type TEXT, side TEXT, symbol TEXT, chain TEXT, quantity REAL,
            price REAL, status TEXT DEFAULT 'pending', trigger_reason TEXT DEFAULT '',
            signal_id INTEGER, strategy_id INTEGER, tx_hash TEXT DEFAULT '',
            error TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
            executed_at TEXT, settled_at TEXT);
    """)
    api_key = "test-api-key-12345"
    con.execute("INSERT INTO agents (id, name, api_key) VALUES (1, 'trader', ?)", (api_key,))
    # A real fake Solana secret key (64 bytes) base58-ish; only used in dry-run,
    # never signed with, so its validity is irrelevant here.
    enc = encrypt_private_key("5" * 64, api_key, 1)
    con.execute("INSERT INTO trading_wallets (id, agent_id, label, chain, address, encrypted_private_key)"
                " VALUES (1, 1, 'main', 'solana', '85SFCufake', ?)", (enc,))
    con.commit()
    con.close()
    return api_key


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "vantage.db")
    _make_db(path)
    monkeypatch.setattr(ee, "DB_PATH", path)
    yield path


def _add_order(path, **kw):
    con = sqlite3.connect(path)
    cols = {"agent_id": 1, "wallet_id": 1, "order_type": "market", "side": "BUY",
            "symbol": "SOL/USDC", "chain": "solana", "quantity": 0.005, "status": "pending"}
    cols.update(kw)
    keys = ",".join(cols)
    cur = con.execute(f"INSERT INTO trading_orders ({keys}) VALUES ({','.join('?' * len(cols))})",
                      tuple(cols.values()))
    con.commit(); oid = cur.lastrowid; con.close()
    return oid


def _order(path, oid):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM trading_orders WHERE id=?", (oid,)).fetchone())
    con.close()
    return row


class _FakeResp:
    def __init__(self, data): self._data = data
    def raise_for_status(self): pass
    def json(self): return self._data


def _mock_jupiter_client(quote=None):
    """AsyncClient whose GET returns a Jupiter quote."""
    quote = quote or {"outAmount": "1234567", "priceImpactPct": "0.1", "routePlan": [{}]}

    client = mock.AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = _FakeResp(quote)
    # For the mint-authority safety check POST (returns no mint authority).
    client.post.return_value = _FakeResp(
        {"result": {"value": {"data": {"parsed": {"info": {"mintAuthority": None}}}}}})
    return client


def test_dry_run_marks_order_ready(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "TRADING_LIVE_ENABLED", False)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", "fake")
    oid = _add_order(temp_db, symbol="BONK/SOL", side="BUY", quantity=0.005)

    with mock.patch("httpx.AsyncClient", return_value=_mock_jupiter_client()):
        asyncio.run(ee.process_order(_order(temp_db, oid)))

    row = _order(temp_db, oid)
    assert row["status"] == "ready"
    assert "DRY-RUN" in row["error"]
    assert row["tx_hash"] == ""


def test_per_order_sol_cap_rejects(temp_db, monkeypatch):
    # Daily cap high so the per-order cap is the guard under test.
    monkeypatch.setattr(settings, "TRADING_DAILY_SOL_CAP", 10.0)
    monkeypatch.setattr(settings, "TRADING_MAX_SOL_PER_ORDER", 0.01)
    oid = _add_order(temp_db, symbol="BONK/SOL", side="BUY", quantity=0.05)  # over per-order cap

    with mock.patch("httpx.AsyncClient", return_value=_mock_jupiter_client()):
        asyncio.run(ee.process_order(_order(temp_db, oid)))

    row = _order(temp_db, oid)
    assert row["status"] == "failed"
    assert "per-order cap" in row["error"]


def test_daily_cap_rejects(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "TRADING_DAILY_SOL_CAP", 0.02)
    # Pre-existing submitted spend today = 0.02 → any new buy exceeds the cap.
    con = sqlite3.connect(temp_db)
    con.execute("INSERT INTO trading_orders (agent_id, wallet_id, side, symbol, chain, quantity,"
                " status, order_type, executed_at) VALUES (1,1,'BUY','BONK/SOL','solana',0.02,"
                "'submitted','market',datetime('now'))")
    con.commit(); con.close()
    oid = _add_order(temp_db, symbol="WIF/SOL", side="BUY", quantity=0.005)

    with mock.patch("httpx.AsyncClient", return_value=_mock_jupiter_client()):
        asyncio.run(ee.process_order(_order(temp_db, oid)))

    assert _order(temp_db, oid)["status"] == "failed"
    assert "daily SOL cap" in _order(temp_db, oid)["error"]


def test_mint_authority_rejected(temp_db, monkeypatch):
    # A BUY of a risky mint: the output token is the risky mint, so the
    # mint-authority safety check inspects it and rejects the rug risk.
    monkeypatch.setattr(settings, "TRADING_LIVE_ENABLED", False)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", "fake")
    monkeypatch.setattr(settings, "TRADING_DAILY_SOL_CAP", 10.0)
    oid = _add_order(temp_db, symbol="EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm/SOL",
                     side="BUY", quantity=0.005)

    client = _mock_jupiter_client()
    client.post.return_value = _FakeResp(
        {"result": {"value": {"data": {"parsed": {"info": {"mintAuthority": "SomeAuth"}}}}}})
    with mock.patch("httpx.AsyncClient", return_value=client):
        asyncio.run(ee.process_order(_order(temp_db, oid)))

    assert _order(temp_db, oid)["status"] == "failed"
    assert "mint authority" in _order(temp_db, oid)["error"]


def test_unknown_token_rejected(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "TRADING_LIVE_ENABLED", False)
    oid = _add_order(temp_db, symbol="!!!/SOL", side="BUY", quantity=0.005)
    with mock.patch("httpx.AsyncClient", return_value=_mock_jupiter_client()):
        asyncio.run(ee.process_order(_order(temp_db, oid)))
    row = _order(temp_db, oid)
    assert row["status"] == "failed"
    assert "unknown Solana token" in row["error"]


def test_no_wallet_fails_fast(temp_db):
    oid = _add_order(temp_db, wallet_id=None)
    asyncio.run(ee.process_order(_order(temp_db, oid)))
    assert _order(temp_db, oid)["status"] == "failed"


def test_symbol_mint_resolution():
    assert ee._resolve_solana_mint("SOL") == ee._WSOL
    assert ee._resolve_solana_mint("bonk") == ee.SOLANA_TOKENS["BONK"]
    mint = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
    assert ee._resolve_solana_mint(mint) == mint
    assert ee._resolve_solana_mint("!!!") is None
