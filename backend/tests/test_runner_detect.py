"""P4-02: tiers, DexScreener folding, upsert/upgrade, candidates, pinning."""
import json
import time

import pytest

from backend import curve_listener as cl
from backend import runner_detect as rd
from backend.db import get_db


def test_tiers_default_drops_100k(monkeypatch):
    monkeypatch.delenv("RUNNER_TIERS", raising=False)
    monkeypatch.delenv("RUNNER_TIER_100K", raising=False)
    assert rd.tiers() == [(1e6, 1.0), (5e6, 1.5), (1e7, 2.0)]
    monkeypatch.setenv("RUNNER_TIER_100K", "1")
    assert rd.tiers()[0] == (1e5, 0.5)


def test_tier_for():
    tl = [(1e6, 1.0), (5e6, 1.5), (1e7, 2.0)]
    assert rd.tier_for(999_999, tl) is None
    assert rd.tier_for(6e6, tl) == (5e6, 1.5)
    assert rd.tier_for(None, tl) is None


def test_best_by_mint_keeps_highest_pair():
    pairs = [{"baseToken": {"address": "M", "symbol": "X"}, "marketCap": 2e6, "pairCreatedAt": 5},
             {"baseToken": {"address": "M", "symbol": "X"}, "marketCap": 3e6},
             {"baseToken": {"address": "N"}, "fdv": 10.0},
             {"baseToken": {}, "marketCap": 1e9}]
    out = rd.best_by_mint(pairs)
    assert out["M"]["mcap"] == 3e6 and out["N"]["mcap"] == 10.0 and len(out) == 2


@pytest.mark.asyncio
async def test_apply_quotes_insert_upgrade_and_peak(monkeypatch):
    monkeypatch.delenv("RUNNER_TIER_100K", raising=False)
    now = time.time()
    async with get_db() as db:
        await db.execute(rd.DDL)
        await db.execute("DELETE FROM runner_tokens WHERE mint IN ('R1','SMALL')")
        ch = await rd.apply_quotes(db, {"R1": {"mcap": 1.2e6, "symbol": "R"}, "SMALL": {"mcap": 5e5}},
                                   {"R1": {"first_slot": 42, "created_seen": 1, "first_seen": 1}}, now)
        assert ch == ["R1"]
        ch = await rd.apply_quotes(db, {"R1": {"mcap": 6e6}}, {}, now + 60)
        assert ch == ["R1"]  # upgraded to the 5M tier
        await rd.apply_quotes(db, {"R1": {"mcap": 2e6}}, {}, now + 120)  # dumped
        row = await (await db.execute(
            "SELECT tier_usd, tier_weight, peak_mcap_usd, last_mcap_usd, tiers_crossed, created_slot, created_seen "
            "FROM runner_tokens WHERE mint='R1'")).fetchone()
    assert row[0] == 5e6 and row[1] == 1.5 and row[2] == 6e6 and row[3] == 2e6  # tier never drops
    assert set(json.loads(row[4])) == {"1000000", "5000000"} and row[5] == 42 and row[6] == 1


def test_candidates_from_curve(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "c.db"))
    c = cl.connect()
    now = int(time.time())
    base = {"ix": 0, "slot": 7, "wallet": "W", "side": "buy", "sol": 1, "tokens": 1,
            "v_sol_reserves": 30, "v_token_reserves": 1e9, "source": "rpc"}
    cl.write_batch(c, [dict(base, sig="a", mint="GRAD", block_time=now, mcap_usd=30000.0),
                       dict(base, sig="b", mint="TINY", block_time=now, mcap_usd=4000.0),
                       dict(base, sig="c", mint="STALE", block_time=now - 30 * 86400, mcap_usd=30000.0)], {"GRAD"})
    out = rd._candidates(c, ["KNOWN"], now)
    assert set(out) == {"GRAD", "KNOWN"} and out["GRAD"]["created_seen"] == 1


@pytest.mark.asyncio
async def test_detect_once_pins_runners(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "c.db"))
    c = cl.connect()
    cl.write_batch(c, [{"sig": "a", "ix": 0, "slot": 7, "block_time": int(time.time()), "mint": "PINME",
                        "wallet": "W", "side": "buy", "sol": 1, "tokens": 1, "v_sol_reserves": 30,
                        "v_token_reserves": 1e9, "mcap_usd": 30000.0, "source": "rpc"}], set())
    c.close()

    async def fake_fetch(mints):
        return {m: {"mcap": 1.5e6, "symbol": "P"} for m in mints if m == "PINME"}
    monkeypatch.setattr(rd, "_fetch", fake_fetch)
    out = await rd.detect_once()
    assert out["quoted"] >= 1
    c = cl.connect()
    assert c.execute("SELECT pinned FROM curve_mints WHERE mint='PINME'").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_endpoints(client, registered_agent, monkeypatch):
    r = await client.get("/api/intel/runners")
    assert r.status_code == 200 and "runners" in r.json() and r.json()["enabled"] is False
    assert (await client.post("/api/intel/runners/check", json={"mints": ["X"]})).status_code == 401

    async def fake_fetch(mints):
        return {}
    monkeypatch.setattr(rd, "_fetch", fake_fetch)
    r = await client.post("/api/intel/runners/check", json={"mints": ["X"]},
                          headers={"X-Agent-Key": registered_agent["api_key"]})
    assert r.status_code == 200 and r.json()["not_found"] == ["X"]
