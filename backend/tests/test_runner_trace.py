"""P4-03: backtrace to origin -- paging, oldest-first, curve path, queue."""
import time

import pytest

from backend import curve_listener as cl
from backend import early_buyer as eb
from backend import runner_trace as rt
from backend.db import get_db


def _sigs(n, start=0):
    # newest-first, as RPC returns; slot falls as we go back
    return [{"signature": f"s{start + i}", "slot": 10_000 - (start + i), "blockTime": 2_000_000_000 - (start + i)}
            for i in range(n)]


def _fake_rpc(total_sigs, owner_for):
    pages = {}
    all_s = _sigs(total_sigs)

    async def fake(client, key, method, params):
        if method == "getSignaturesForAddress":
            before = params[1].get("before")
            lim = params[1]["limit"]
            i = 0 if before is None else next(k for k, s in enumerate(all_s) if s["signature"] == before) + 1
            pages[before] = True
            return all_s[i:i + lim]
        if method == "getTransaction":
            sig = params[0]
            idx = int(sig[1:])
            return {"blockTime": 2_000_000_000 - idx, "meta": {
                "preTokenBalances": [], "postTokenBalances": [
                    {"mint": "MINT", "accountIndex": 1, "owner": owner_for(idx), "uiTokenAmount": {"uiAmount": 5}}],
                "preBalances": [3_000_000_000], "postBalances": [2_000_000_000]},
                "transaction": {"message": {"accountKeys": []}}}
        return None
    return fake


@pytest.mark.asyncio
async def test_paginate_to_origin_reaches_or_not(monkeypatch):
    monkeypatch.setattr(eb, "_rpc", _fake_rpc(2037, lambda i: f"w{i}"))
    sigs, reached, cur, n = await eb._paginate_to_origin(None, "k", "MINT", max_total=100_000)
    assert len(sigs) == 2037 and reached is True and sigs[-1]["signature"] == "s2036" and n == 2037
    sigs, reached, cur, n = await eb._paginate_to_origin(None, "k", "MINT", max_total=1500)
    # stopped short: cursor re-fetches the last full page, count excludes it
    assert reached is False and len(sigs) == 2000 and cur == "s999" and n == 1000
    sigs, reached, cur, n = await eb._paginate_to_origin(None, "k", "MINT", max_total=100_000,
                                                          before=cur, already=n)
    assert reached is True and sigs[0]["signature"] == "s1000" and n == 2037


@pytest.mark.asyncio
async def test_oldest_first_returns_the_real_first_buyers(monkeypatch):
    monkeypatch.setattr(eb, "_helius_key", lambda: "k")
    monkeypatch.setattr(eb, "_rpc", _fake_rpc(3000, lambda i: f"w{i}"))

    async def no_funder(*a, **k):
        return None
    monkeypatch.setattr(eb, "_find_funder", no_funder)
    out = await eb.backtrack_early_buyers("MINT", max_buyers=5, oldest_first=True, use_cache=False,
                                          max_total=100_000)
    assert out["reached_origin"] is True and out["signatures_scanned"] == 3000
    assert out["origin"]["signature"] == "s2999" and out["origin"]["slot"] == 10_000 - 2999
    # the five truly oldest txs (s2999..s2995), in entry order
    assert [b["wallet_address"] for b in out["buyers"]] == ["w2999", "w2998", "w2997", "w2996", "w2995"]


@pytest.mark.asyncio
async def test_default_call_shape_unchanged(monkeypatch):
    monkeypatch.setattr(eb, "_helius_key", lambda: "k")
    monkeypatch.setattr(eb, "_rpc", _fake_rpc(3000, lambda i: f"d{i}"))

    async def no_funder(*a, **k):
        return None
    monkeypatch.setattr(eb, "_find_funder", no_funder)
    out = await eb.backtrack_early_buyers("MINT2", max_buyers=5)
    assert set(out) == {"mint", "buyers", "new", "total", "cached"}  # no new keys by default


def test_curve_first_buyers(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "c.db"))
    c = cl.connect()
    rows = []
    for i, (w, side) in enumerate([("dev", "buy"), ("a", "buy"), ("a", "buy"), ("b", "sell"), ("c", "buy")]):
        rows.append({"sig": f"x{i}", "ix": 0, "slot": 100 + i, "block_time": 1000 + i, "mint": "CM", "wallet": w,
                     "side": side, "sol": 1.0, "tokens": 1.0, "v_sol_reserves": 30, "v_token_reserves": 1e9,
                     "mcap_usd": 1.0, "source": "rpc"})
    cl.write_batch(c, rows, {"CM"})
    out = rt.curve_first_buyers("CM")
    assert [b["wallet"] for b in out["buyers"]] == ["dev", "a", "c"]
    assert out["buyers"][1]["sol"] == 2.0 and out["origin_slot"] == 100 and out["reached_origin"]
    cl.write_batch(c, [dict(rows[0], sig="y", mint="NOCREATE")], set())
    assert rt.curve_first_buyers("NOCREATE") is None


@pytest.mark.asyncio
async def test_trace_one_statuses(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "none.db"))

    async def partial(mint, *a, **k):
        return {"buyers": [{"wallet": "w", "rank": 1, "entry_ts": 1, "entry_slot": None, "sol": 1, "tokens": 1,
                            "signature": "s", "funded_by": None, "is_major": 1}],
                "reached_origin": False, "origin_slot": 5, "origin_time": 1, "origin_signature": "s",
                "signatures_scanned": 100000, "source": "rpc"}
    monkeypatch.setattr(rt, "rpc_first_buyers", partial)
    out = await rt.trace_one("TR1")
    assert out["status"] == "walking" and "not reached" in out["error"]

    async def boom(mint, *a, **k):
        raise RuntimeError("no_helius_key")
    monkeypatch.setattr(rt, "rpc_first_buyers", boom)
    out = await rt.trace_one("TR2")
    assert out["status"] == "failed" and "no_helius_key" in out["error"]
    got = await rt.early_buyers("TR1")
    assert got["trace"]["status"] == "walking" and got["buyers"][0]["wallet"] == "w"


@pytest.mark.asyncio
async def test_walk_resumes_from_cursor(monkeypatch, tmp_path):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "none.db"))
    calls = []

    async def fake(mint, resume_before=None, resume_count=0):
        calls.append((resume_before, resume_count))
        if len(calls) == 1:
            return {"buyers": [], "reached_origin": False, "origin_slot": None, "origin_time": None,
                    "origin_signature": None, "signatures_scanned": 11000, "cursor": "C1", "source": "rpc"}
        return {"buyers": [{"wallet": "first", "rank": 1, "entry_ts": 1, "entry_slot": None, "sol": 1,
                            "tokens": 1, "signature": "o", "funded_by": None, "is_major": 1}],
                "reached_origin": True, "origin_slot": 7, "origin_time": 1, "origin_signature": "o",
                "signatures_scanned": 52000, "cursor": None, "source": "rpc"}
    monkeypatch.setattr(rt, "rpc_first_buyers", fake)
    assert (await rt.trace_one("RES"))["status"] == "walking"
    assert (await rt.trace_one("RES"))["status"] == "done"
    assert calls == [(None, 0), ("C1", 11000)]
    got = await rt.early_buyers("RES")
    assert got["trace"]["origin_slot"] == 7 and got["trace"]["status"] == "done"


@pytest.mark.asyncio
async def test_enqueue_and_endpoints(client, registered_agent):
    async with get_db() as db:
        from backend.runner_detect import DDL
        await db.execute(DDL)
        await db.execute("INSERT OR IGNORE INTO runner_tokens (mint, tier_usd, tier_weight, peak_mcap_usd, peak_at, "
                         "detected_at) VALUES ('QQ', 1e6, 1.0, 1e6, 1, 1)")
        await db.commit()
        assert await rt.enqueue_new_runners(db) >= 1
    assert (await client.post("/api/intel/runners/QQ/trace")).status_code == 401
    r = await client.post("/api/intel/runners/QQ/trace", headers={"X-Agent-Key": registered_agent["api_key"]})
    assert r.status_code == 200 and r.json()["queued"]
    r = await client.get("/api/intel/runners/QQ/early-buyers")
    assert r.status_code == 200 and r.json()["trace"]["status"] == "pending"


@pytest.mark.asyncio
async def test_parse_buyer_no_longer_nameerrors(monkeypatch):
    """Regression: _EXCLUDED was undefined, so every parse raised and every
    backtrack returned zero buyers."""
    monkeypatch.setattr(eb, "_rpc", _fake_rpc(10, lambda i: f"w{i}"))
    b = await eb._parse_buyer_from_tx(None, "k", "s3", "MINT")
    assert b["wallet_address"] == "w3" and b["sol_spent"] == 1.0
    monkeypatch.setattr(eb, "_rpc", _fake_rpc(10, lambda i: "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"))
    assert await eb._parse_buyer_from_tx(None, "k", "s3", "MINT") is None  # programs excluded
