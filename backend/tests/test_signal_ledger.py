"""signal_ledger (handoff P1-03): every ingested signal kept, never trimmed.

Acceptance from the ticket:
  * 600 ingests leave 500 pool rows and 600 ledger rows
  * signal_promotions ids still resolve through ledger.pool_id
  * a ledger write failure never fails the ingest (logged)
plus: flag off writes nothing, entry price via the mint-exact quote path with
a timeout, the read endpoint, record fields carried, chain on the pool.
"""
import asyncio
import os
import random
import time

import pytest

from backend import signal_ledger as sl

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MINT = "So11111111111" + "111111111111111111111111111112"  # secret-scan:allow (wrapped SOL mint, public constant)


def _headers():
    return {"X-Vantage-Tool": "intel", "X-Vantage-Tool-Key": os.environ["VANTAGE_TOOL_INTEL"]}


@pytest.fixture
def ledger_on(monkeypatch):
    monkeypatch.setenv("SIGNAL_LEDGER_ENABLED", "1")
    async def quote(symbol, mint):
        return (1.25, "mint") if mint else (None, "none")
    monkeypatch.setattr(sl, "_entry_price", quote)


async def _count(sql, *args):
    from backend.db import get_db
    async with get_db() as db:
        await sl.ensure_tables(db)
        return (await (await db.execute(sql, args)).fetchone())[0]


def test_mint_constant_is_wrapped_sol():
    assert MINT == "So" + "1" * 40 + "2"


async def test_600_ingests_leave_500_pool_rows_and_600_ledger_rows(client, ledger_on):
    from backend.routers.intel import ingest_signal_internal
    src = f"ledger600_{random.randint(0, 10**9)}"
    for i in range(600):
        await ingest_signal_internal(f"T{i}", src, "alpha", 0.5, "BUY", "", "")
    await sl.drain()
    assert await _count("SELECT COUNT(*) FROM signal_pool WHERE source=?", src) == 500
    assert await _count("SELECT COUNT(*) FROM signal_pool") == 500
    assert await _count("SELECT COUNT(*) FROM signal_ledger WHERE source=?", src) == 600
    # the oldest 100 are gone from the pool but present in the ledger with pool_id
    oldest = await _count("SELECT MIN(pool_id) FROM signal_ledger WHERE source=?", src)
    assert await _count("SELECT COUNT(*) FROM signal_pool WHERE id=?", oldest) == 0
    assert await _count("SELECT COUNT(*) FROM signal_ledger WHERE pool_id=?", oldest) == 1


async def test_promotion_ids_resolve_through_ledger_pool_id(client, ledger_on):
    from backend.db import get_db
    from backend.routers.intel import ingest_signal_internal
    src = f"ledgerpromo_{random.randint(0, 10**9)}"
    await ingest_signal_internal("PROMO", src, "alpha", 0.8, "BUY", "", MINT)
    await sl.drain()
    async with get_db() as db:
        pool_id = (await (await db.execute(
            "SELECT id FROM signal_pool WHERE source=?", (src,))).fetchone())[0]
        await db.execute(
            "INSERT OR REPLACE INTO signal_promotions (signal_id, order_id, agent_id, source, symbol) "
            "VALUES (?,?,?,?,?)", (pool_id, 999999, 1, src, "PROMO"))
        await db.execute("DELETE FROM signal_pool WHERE id=?", (pool_id,))  # trimmed
        await db.commit()
        row = await (await db.execute(
            "SELECT l.symbol, l.address, l.entry_price_usd, l.entry_price_src FROM signal_promotions p "
            "JOIN signal_ledger l ON l.pool_id = p.signal_id WHERE p.signal_id=?", (pool_id,))).fetchone()
    assert row == ("PROMO", MINT, 1.25, "mint")


async def test_a_ledger_failure_never_fails_the_ingest(client, ledger_on, monkeypatch, caplog):
    from backend.routers.intel import ingest_signal_internal
    async def broken(db):
        raise RuntimeError("disk full")
    monkeypatch.setattr(sl, "ensure_tables", broken)
    out = await ingest_signal_internal("FAILOK", "ledger_fail", "alpha", 0.5, "BUY", "", "")
    await sl.drain()
    assert out["status"] == "ingested"
    assert any("signal_ledger write failed" in r.message for r in caplog.records)

    def boom(*a, **k):
        raise ValueError("bad row")
    monkeypatch.setattr(sl, "row_for", boom)
    out = await ingest_signal_internal("FAILOK2", "ledger_fail", "alpha", 0.5, "BUY", "", "")
    assert out["status"] == "ingested"


async def test_flag_off_writes_no_ledger_rows(client, monkeypatch):
    from backend.routers.intel import ingest_signal_internal
    monkeypatch.setenv("SIGNAL_LEDGER_ENABLED", "0")
    src = f"ledgeroff_{random.randint(0, 10**9)}"
    await ingest_signal_internal("OFF", src, "alpha", 0.5, "BUY", "", "")
    await sl.drain()
    assert await _count("SELECT COUNT(*) FROM signal_ledger WHERE source=?", src) == 0


async def test_entry_price_times_out_to_null(client, monkeypatch):
    async def slow(symbol, mint=None):
        await asyncio.sleep(5)
        return 9.0
    import backend.routers.trading as trading
    monkeypatch.setattr(trading, "_fetch_quote", slow)
    monkeypatch.setattr(sl, "QUOTE_TIMEOUT_S", 0.05)
    assert await sl._entry_price("X", MINT) == (None, "none")

    async def fast(symbol, mint=None):
        assert mint == MINT  # mint-exact: the mint is what gets priced
        return 2.5
    monkeypatch.setattr(trading, "_fetch_quote", fast)
    assert await sl._entry_price("X", MINT) == (2.5, "mint")


async def test_record_fields_and_pool_chain_are_carried(client, ledger_on, monkeypatch):
    from backend import signal_record as sr
    monkeypatch.setenv("SIGNAL_RECORD_V1", "1")
    async def exists(chain, address):
        return True
    monkeypatch.setattr(sr, "address_checker", exists)
    rid = "".join(random.choice(_CROCKFORD) for _ in range(26))
    src = f"ledgerrec{random.randint(0, 10**9)}"
    rec = {"schema": "signal_record/1", "id": rid, "created_at": int(time.time()) - 3,
           "source": src, "source_ref": "tx:abc", "type": "convergence", "direction": "long",
           "asset": {"symbol": "WSOL", "chain": "solana", "address": MINT}, "conviction": 0.9,
           "parents": ["01JCD9W3Q2Z8K5M0V7XGTB4R1N"], "target_pct": 30,
           "producer": {"kind": "daemon", "lineage": "unit", "env": "ci", "blind": False},
           "evidence": {"buyers": 3}, "tags": ["curve"]}
    r = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r.status_code == 200, r.text
    await sl.drain()
    rows = await sl.query(source=src)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == rid and row["schema"] == "signal_record/1" and row["door"] == "intel"
    assert row["conviction"] == 0.69 and row["conviction_raw"] == 0.9
    assert row["parents"] == ["01JCD9W3Q2Z8K5M0V7XGTB4R1N"] and row["producer"]["blind"] is False
    assert row["chain"] == "solana" and row["address"] == MINT and row["target_pct"] == 30
    assert await _count("SELECT COUNT(*) FROM signal_pool WHERE source=? AND chain='solana'", src) == 1


async def test_in_memory_pool_entries_keep_their_shape(client, ledger_on):
    """GET /api/intel/signals serves these dicts; chain must not leak into them."""
    from backend.routers import intel
    await intel.ingest_signal_internal("SHAPE", "ledger_shape", "alpha", 0.5, "BUY", "", "", chain="base")
    entry = [s for s in intel._signal_pool if s["symbol"] == "SHAPE"][-1]
    assert set(entry) == {"symbol", "source", "type", "conviction", "direction", "detail", "mint", "ts"}


async def test_ledger_endpoint_reads_with_agent_auth(client, fresh_agent, ledger_on):
    from backend.routers.intel import ingest_signal_internal
    src = f"ledgerapi_{random.randint(0, 10**9)}"
    await ingest_signal_internal("API1", src, "alpha", 0.5, "BUY", "", MINT)
    await ingest_signal_internal("API2", src, "alpha", 0.5, "SELL", "", "")
    await sl.drain()
    assert (await client.get(f"/api/intel/ledger?source={src}")).status_code == 401
    agent = await fresh_agent()
    r = await client.get(f"/api/intel/ledger?source={src}&address={MINT}",
                         headers={"X-Agent-Key": agent["api_key"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["count"] == 1 and body["rows"][0]["symbol"] == "API1"
