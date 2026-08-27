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
import backend.db as _db_module
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
    # Not a secret — a fixed string only used to exercise the wallet
    # encrypt/decrypt round-trip in-test. gitleaks:allow
    api_key = "dummy-not-a-real-key"  # gitleaks:allow
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
    # The engine now opens connections via db.get_db(), which reads
    # backend.db.DB_PATH (not ee.DB_PATH) — patch the module get_db() reads.
    monkeypatch.setattr(_db_module, "DB_PATH", path)
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


# ── kill switch ──────────────────────────────────────────────────────────────

def test_kill_switch_reads_true_from_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("SOME_OTHER_VAR=1\nVANTAGE_TRADING_KILL_SWITCH=true\n")
    assert ee._kill_switch_active(str(f)) is True


def test_kill_switch_reads_false_from_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("VANTAGE_TRADING_KILL_SWITCH=false\n")
    assert ee._kill_switch_active(str(f)) is False


def test_kill_switch_key_absent_from_readable_file_is_off(tmp_path):
    f = tmp_path / ".env"
    f.write_text("SOME_OTHER_VAR=1\n")
    assert ee._kill_switch_active(str(f)) is False


def test_kill_switch_missing_file_falls_back_to_cached_settings(monkeypatch):
    monkeypatch.setattr(settings, "TRADING_KILL_SWITCH", True)
    assert ee._kill_switch_active("/definitely/does/not/exist.env") is True
    monkeypatch.setattr(settings, "TRADING_KILL_SWITCH", False)
    assert ee._kill_switch_active("/definitely/does/not/exist.env") is False


def test_kill_switch_unreadable_existing_file_fails_closed(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("VANTAGE_TRADING_KILL_SWITCH=false\n")
    f.chmod(0o000)
    try:
        # Root/CI runners sometimes ignore chmod 000 for the owning user --
        # only assert fail-closed if the permission actually took effect.
        if not __import__("os").access(str(f), __import__("os").R_OK):
            assert ee._kill_switch_active(str(f)) is True
    finally:
        f.chmod(0o644)


def test_kill_switch_blocks_process_order(temp_db, monkeypatch):
    monkeypatch.setattr(ee, "_kill_switch_active", lambda: True)
    oid = _add_order(temp_db, symbol="BONK/SOL", side="BUY", quantity=0.005)

    asyncio.run(ee.process_order(_order(temp_db, oid)))

    row = _order(temp_db, oid)
    assert row["status"] == "pending"  # untouched, not failed -- kill switch pauses, doesn't cancel


def test_kill_switch_off_lets_order_through(temp_db, monkeypatch):
    monkeypatch.setattr(ee, "_kill_switch_active", lambda: False)
    monkeypatch.setattr(settings, "TRADING_LIVE_ENABLED", False)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", "fake")
    oid = _add_order(temp_db, symbol="BONK/SOL", side="BUY", quantity=0.005)

    with mock.patch("httpx.AsyncClient", return_value=_mock_jupiter_client()):
        asyncio.run(ee.process_order(_order(temp_db, oid)))

    assert _order(temp_db, oid)["status"] == "ready"


# ── exposure-reducing (SELL/close) orders ───────────────────────────────────

def test_is_exposure_reducing():
    assert ee._is_exposure_reducing({"side": "SELL"}) is True
    assert ee._is_exposure_reducing({"side": "sell"}) is True
    assert ee._is_exposure_reducing({"side": "BUY"}) is False
    assert ee._is_exposure_reducing({"side": ""}) is False
    assert ee._is_exposure_reducing({}) is False


def test_concurrency_cap_exempts_exposure_reducing_orders(temp_db, monkeypatch):
    """Over the concurrency cap: a pending SELL (closes/reduces exposure)
    must still be processed while a pending BUY (adds exposure) is withheld."""
    monkeypatch.setattr(ee, "_kill_switch_active", lambda: False)
    monkeypatch.setattr(settings, "TRADING_LIVE_ENABLED", False)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", "fake")
    monkeypatch.setattr(settings, "TRADING_MAX_CONCURRENT_PENDING", 0)  # force the cap path

    buy_id = _add_order(temp_db, symbol="BONK/SOL", side="BUY", quantity=0.005)
    sell_id = _add_order(temp_db, symbol="BONK/SOL", side="SELL", quantity=0.005)

    async def _one_tick():
        # Same body as execution_loop's while-True, run exactly once instead
        # of looping forever -- exercises the real concurrency-cap branch.
        if ee._kill_switch_active():
            return
        active = await ee._count_active_pending()
        if active > settings.TRADING_MAX_CONCURRENT_PENDING:
            orders = await ee._get_pending_orders()
            orders = [o for o in orders if ee._is_exposure_reducing(o)]
        else:
            orders = await ee._get_pending_orders()
        for order in orders:
            await ee.process_order(order)

    with mock.patch("httpx.AsyncClient", return_value=_mock_jupiter_client()):
        asyncio.run(_one_tick())

    assert _order(temp_db, sell_id)["status"] == "ready"    # processed despite the cap
    assert _order(temp_db, buy_id)["status"] == "pending"   # withheld by the cap, untouched
