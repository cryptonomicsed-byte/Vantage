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
    monkeypatch.setattr(rt, "CURVE_MIN_BUYERS", 1)
    c = cl.connect()
    rows = []
    for i, (w, side) in enumerate([("dev", "buy"), ("a", "buy"), ("a", "buy"), ("b", "sell"), ("c", "buy")]):
        rows.append({"sig": f"x{i}", "ix": 0, "slot": 100 + i, "block_time": 1762600000 + i, "mint": "CM", "wallet": w,
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
        from backend.runner_detect import ensure_table
        await ensure_table(db)
        await rt._ensure(db)
        await db.execute("DELETE FROM runner_trace_queue WHERE mint='QQ'")
        # only liquidity-backed runners are queued (#134)
        await db.execute("INSERT OR REPLACE INTO runner_tokens (mint, tier_usd, tier_weight, peak_mcap_usd, peak_at, "
                         "detected_at, backed, liq_usd) VALUES ('QQ', 1e6, 1.0, 1e6, 1, 1, 1, 90000)")
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


@pytest.mark.asyncio
async def test_trade_event_gives_exact_buyer_and_sol(monkeypatch):
    """A bundled create+buy: the fee payer only pays fees; the TradeEvent
    names the real buyer and SOL."""
    import base64
    import struct
    from backend import curve_listener as cl
    B58 = cl._B58

    def unb58(s):
        n = 0
        for ch in s:
            n = n * 58 + B58.index(ch)
        return n.to_bytes(32, "big")
    mint = cl.b58encode(b"\x07" * 32)
    user = cl.b58encode(b"\x09" * 32)
    raw = (cl.TRADE_DISC + unb58(mint) + struct.pack("<QQ", 2_500_000_000, 10_000_000_000) + b"\x01"
           + unb58(user) + struct.pack("<qQQ", 1762600000, 32_500_000_000, 1_000_000_000_000_000))

    async def fake(client, key, method, params):
        return {"blockTime": 5, "meta": {
            "preTokenBalances": [], "postTokenBalances": [
                {"mint": mint, "accountIndex": 1, "owner": user, "uiTokenAmount": {"uiAmount": 10000}}],
            "preBalances": [1_000_000_000], "postBalances": [998_500_000],
            "logMessages": ["Program data: " + base64.b64encode(raw).decode()]}}
    monkeypatch.setattr(eb, "_rpc", fake)
    b = await eb._parse_buyer_from_tx(None, "k", "sig", mint)
    assert b["wallet_address"] == user and b["sol_spent"] == 2.5  # not the payer's 0.0015


@pytest.mark.asyncio
async def test_parse_retries_only_when_asked(monkeypatch):
    calls = {"n": 0}

    async def flaky(client, key, method, params):
        calls["n"] += 1
        return None if calls["n"] < 3 else {"blockTime": 1, "meta": {
            "preTokenBalances": [], "postTokenBalances": [
                {"mint": "M", "accountIndex": 1, "owner": "W", "uiTokenAmount": {"uiAmount": 1}}],
            "preBalances": [2], "postBalances": [1]}}
    monkeypatch.setattr(eb, "_rpc", flaky)
    monkeypatch.setattr(eb.asyncio, "sleep", _nosleep)
    assert await eb._parse_buyer_from_tx(None, "k", "s", "M") is None and calls["n"] == 1  # default: no retry
    calls["n"] = 0
    assert (await eb._parse_buyer_from_tx(None, "k", "s", "M", retries=4))["wallet_address"] == "W"


async def _nosleep(*a, **k):
    return None


@pytest.mark.asyncio
async def test_thin_curve_falls_back_to_rpc(tmp_path, monkeypatch):
    """A curve that completed inside its create tx (one 85 SOL dev buy) has a
    single curve buyer: that is not 'early buyers', so the RPC walk decides."""
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "c.db"))
    c = cl.connect()
    cl.write_batch(c, [{"sig": "create", "ix": 0, "slot": 7, "block_time": 1762600000, "mint": "THIN", "wallet": "dev",
                        "side": "buy", "sol": 85.0, "tokens": 1.0, "v_sol_reserves": 115, "v_token_reserves": 1e9,
                        "mcap_usd": 35000.0, "source": "ws"}], {"THIN"})
    assert c.execute("SELECT COUNT(*) FROM curve_trades WHERE mint='THIN'").fetchone()[0] == 1  # row is real
    assert rt.curve_first_buyers("THIN") is None  # ...but one buyer is too thin
    called = []

    async def rpc(mint, *a, **k):
        called.append(mint)
        return {"buyers": [{"wallet": f"w{i}", "rank": i + 1, "entry_ts": 1000 + i, "entry_slot": 7, "sol": 1,
                            "tokens": 1, "signature": f"s{i}", "funded_by": None, "is_major": 0} for i in range(30)],
                "reached_origin": True, "origin_slot": 7, "origin_time": 1000, "origin_signature": "create",
                "signatures_scanned": 5000, "source": "rpc", "cursor": None}
    monkeypatch.setattr(rt, "rpc_first_buyers", rpc)
    out = await rt.trace_one("THIN")
    assert called == ["THIN"] and out["status"] == "done" and out["source"] == "rpc" and out["buyers"] == 30


@pytest.mark.asyncio
async def test_thin_curve_traces_are_requeued():
    from backend.db import get_db
    async with get_db() as db:
        await rt._ensure(db)
        await db.execute("INSERT OR REPLACE INTO runner_trace_queue (mint, status, attempts, source, buyers, "
                         "enqueued_at) VALUES ('OLDTHIN','done',1,'curve',1,1)")
        await db.execute("INSERT OR REPLACE INTO runner_trace_queue (mint, status, attempts, source, buyers, "
                         "enqueued_at) VALUES ('FULL','done',1,'curve',100,1)")
        await db.commit()
        await rt._ensure(db)
        st = dict(await (await db.execute(
            "SELECT mint, status FROM runner_trace_queue WHERE mint IN ('OLDTHIN','FULL')")).fetchall())
    assert st == {"OLDTHIN": "pending", "FULL": "done"}



@pytest.mark.asyncio
async def test_dust_does_not_use_up_the_first_n(monkeypatch):
    """Live: GTA6's oldest 100 txs were 51 sub-0.01 SOL bot trades and one
    real buy, and the walk stopped there. Real buys alone now fill FIRST_N,
    reading up to scan_sigs txs; majors/funders are chosen from real buys."""
    monkeypatch.setattr(eb, "_helius_key", lambda: "k")
    base = _fake_rpc(3000, lambda i: f"w{i}")

    async def fake(client, key, method, params):
        out = await base(client, key, method, params)
        if method == "getTransaction" and int(params[0][1:]) >= 2940:     # the oldest 60: dust
            out["meta"]["postBalances"] = [3_000_000_000 - 1_500_000]       # 0.0015 SOL
        return out
    monkeypatch.setattr(eb, "_rpc", fake)
    looked_up = []

    async def funder(client, key, wallet, retries=0):
        looked_up.append(wallet)
        return None
    monkeypatch.setattr(eb, "_find_funder", funder)
    out = await eb.backtrack_early_buyers("MINT", max_buyers=5, oldest_first=True, use_cache=False,
                                          max_total=100_000, scan_sigs=200)
    from backend.early_runner_intel import classify
    real = [b for b in out["buyers"] if classify({"sol": b["sol_spent"], "tokens": b["token_amount"]}) == "buy"]
    assert len(real) == 5 and [b["wallet_address"] for b in real][0] == "w2939"
    assert sum(1 for b in out["buyers"] if b["sol_spent"] < 0.01) == 60          # dust kept, classified
    assert looked_up and all(int(w[1:]) < 2940 for w in looked_up)               # no funder calls on dust


@pytest.mark.asyncio
async def test_mostly_dust_rpc_trace_is_rewalked_once():
    async with get_db() as db:
        await rt._ensure(db)
        await db.execute("DELETE FROM runner_trace_queue WHERE mint='DUSTY'")
        await db.execute("DELETE FROM runner_early_buyers WHERE mint='DUSTY'")
        await db.execute("INSERT INTO runner_trace_queue (mint, enqueued_at, status, attempts, source, buyers) "
                         "VALUES ('DUSTY', 1, 'done', 1, 'rpc', 52)")
        for i in range(52):
            await db.execute("INSERT INTO runner_early_buyers (mint, wallet, rank, sol, source) "
                             "VALUES ('DUSTY', ?, ?, ?, 'rpc')", (f"x{i}", i + 1, 0.105 if i == 0 else 0.0015))
        await db.execute("UPDATE runner_trace_queue SET dust_rescanned=0 WHERE mint='DUSTY'")
        await db.commit()
        assert await rt.requeue_dust_traces(db) >= 1
        q = await (await db.execute("SELECT status, dust_rescanned FROM runner_trace_queue WHERE mint='DUSTY'")).fetchone()
        assert tuple(q) == ("pending", 1)
        await db.execute("UPDATE runner_trace_queue SET status='done' WHERE mint='DUSTY'")
        await db.commit()
        await rt.requeue_dust_traces(db)                                            # only once
        await rt._ensure(db)                                                        # reads never requeue
        q = await (await db.execute("SELECT status FROM runner_trace_queue WHERE mint='DUSTY'")).fetchone()
        assert q[0] == "done"
