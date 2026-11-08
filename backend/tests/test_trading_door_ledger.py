"""P1-04: the trading ingest door also writes signal_ledger (door 'trading').

Acceptance: existing trading-ingest tests pass unchanged (they run in this
suite); a ledger row appears. Plus: the response is identical with the ledger
on or off, no order is created by the hook, and a broken ledger never fails
the ingest.
"""
import os
import random
import time

import aiosqlite
import pytest

from backend import signal_ledger as sl

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MINT = "So11111111111" + "111111111111111111111111111112"  # secret-scan:allow (wrapped SOL mint, public constant)


def _headers():
    return {"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": os.environ["VANTAGE_TOOL_TRADING"]}


async def _mesh_agent():
    from backend.db import DB_PATH
    aid = f"p104-agent-{random.randint(0, 10**9)}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO mesh_agents (agent_id, block_id) VALUES (?,?)", (aid, "test-block"))
        await db.commit()
    return aid


@pytest.fixture
def ledger_on(monkeypatch):
    monkeypatch.setenv("SIGNAL_LEDGER_ENABLED", "1")
    async def quote(symbol, mint):
        return (3.0, "mint") if mint else (None, "none")
    monkeypatch.setattr(sl, "_entry_price", quote)


async def _orders(aid):
    from backend.db import get_db
    async with get_db() as db:
        return (await (await db.execute(
            "SELECT COUNT(*) FROM trading_orders WHERE agent_id=?", (aid,))).fetchone())[0]


async def test_trading_ingest_writes_a_ledger_row(client, ledger_on):
    aid = await _mesh_agent()
    src = f"p104src_{random.randint(0, 10**9)}"
    r = await client.post("/api/trading/signals/ingest", headers=_headers(), json={
        "symbol": "WSOL", "direction": "BUY", "conviction": 0.4, "source": src,
        "agent_id": aid, "chain": "solana", "mint": MINT})
    assert r.status_code == 200, r.text
    await sl.drain()
    rows = await sl.query(source=src)
    assert len(rows) == 1
    row = rows[0]
    assert row["door"] == "trading" and row["id"].startswith("trading:")
    assert row["pool_id"] is None and row["address"] == MINT and row["chain"] == "solana"
    assert row["conviction"] == 0.4 and row["entry_price_usd"] == 3.0
    assert await _orders(aid) == 0  # the hook never creates an order


async def test_response_is_identical_with_ledger_on_and_off(client, monkeypatch):
    aid = await _mesh_agent()
    body = {"symbol": "SOL", "direction": "BUY", "conviction": 0.3, "source": "p104same", "agent_id": aid}
    monkeypatch.setenv("SIGNAL_LEDGER_ENABLED", "0")
    off = (await client.post("/api/trading/signals/ingest", headers=_headers(), json=body)).json()
    monkeypatch.setenv("SIGNAL_LEDGER_ENABLED", "1")
    on = (await client.post("/api/trading/signals/ingest", headers=_headers(), json=body)).json()
    await sl.drain()
    assert on == off


async def test_record_fields_are_accepted_at_the_trading_door(client, ledger_on):
    aid = await _mesh_agent()
    rid = "".join(random.choice(_CROCKFORD) for _ in range(26))
    src = f"p104rec{random.randint(0, 10**9)}"
    body = {"schema": "signal_record/1", "id": rid, "created_at": int(time.time()) - 2,
            "source": src, "source_ref": "tx:zz", "type": "whale", "direction": "BUY",
            "asset": {"symbol": "WSOL", "chain": "solana", "address": MINT},
            "conviction": 0.5, "producer": {"kind": "daemon", "lineage": "unit", "env": "ci", "blind": True},
            "symbol": "WSOL", "agent_id": aid}
    r = await client.post("/api/trading/signals/ingest", headers=_headers(), json=body)
    assert r.status_code == 200, r.text
    await sl.drain()
    rows = await sl.query(source=src)
    assert len(rows) == 1 and rows[0]["id"] == rid and rows[0]["schema"] == "signal_record/1"
    assert rows[0]["source_ref"] == "tx:zz" and rows[0]["door"] == "trading"


async def test_a_broken_ledger_never_fails_the_trading_ingest(client, ledger_on, monkeypatch):
    aid = await _mesh_agent()
    def boom(*a, **k):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(sl, "schedule", boom)
    r = await client.post("/api/trading/signals/ingest", headers=_headers(), json={
        "symbol": "SOL", "direction": "BUY", "conviction": 0.2, "source": "p104broken", "agent_id": aid})
    assert r.status_code == 200 and r.json()["status"] == "ingested"
