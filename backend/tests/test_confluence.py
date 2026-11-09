"""P4-09: confluence rules, mcap ceiling, anti-pump, dedupe, posting, enricher."""
import time

import pytest

from backend import confluence as cf
from backend import curve_listener as cl
from backend import degen_filters as df
from backend.db import get_db

NOW = int(time.time())


def test_rules_and_conviction_cap():
    assert cf.rule_for(3, 0) == "watch>=3" and cf.rule_for(0, 2) == "kols>=2"
    assert cf.rule_for(1, 1) == "watch+kol" and cf.rule_for(2, 0) is None and cf.rule_for(0, 1) is None
    assert cf.conviction_for(3, 0) == pytest.approx(0.61)
    assert cf.conviction_for(50, 50) == 0.69


def test_anti_pump():
    t = [{"wallet": w, "sol": 1.0, "side": "buy"} for w in ("a", "b", "c", "d")]
    assert df.anti_pump(t, {})["ok"]
    assert not df.anti_pump(t[:2], {})["ok"]  # < 3 distinct buyers
    r = df.anti_pump(t, {"a": ["bundler"], "b": ["sniper"], "c": ["bot"]})
    assert not r["ok"] and r["labelled_share"] == 0.75
    whale = t + [{"wallet": "z", "sol": 10.0, "side": "buy"}]
    assert "one wallet" in " ".join(df.anti_pump(whale, {})["reasons"])
    dump = t + [{"wallet": "dv", "sol": 1.0, "side": "sell"}]
    assert df.anti_pump(dump, {"dv": ["dev"]})["dev_sold"] == ["dv"]


def _trades(mint, wallets, mcap):
    return [{"sig": f"{mint}{w}", "ix": 0, "slot": 1, "block_time": NOW - 60, "mint": mint, "wallet": w,
             "side": "buy", "sol": 1.0, "tokens": 1, "v_sol_reserves": 30, "v_token_reserves": 1e9,
             "mcap_usd": mcap, "source": "ws"} for w in wallets]


async def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "c.db"))
    c = cl.connect()
    cl.write_batch(c, _trades("HOT", ["E1", "E2", "E3", "x", "y"], 30_000), set())
    cl.write_batch(c, _trades("BIG", ["E1", "E2", "E3", "x"], 90_000), set())
    cl.write_batch(c, _trades("TWO", ["E1", "E2", "x", "y"], 20_000), set())
    cl.write_batch(c, _trades("KOLM", ["E1", "p", "q"], 25_000), set())
    c.close()
    async with get_db() as db:
        from backend.runner_watchlist import DDL
        for d in DDL:
            await db.execute(d)
        await db.execute("DELETE FROM runner_watchlist_wallets")
        await db.execute("DELETE FROM confluence_events") if (await (await db.execute(
            "SELECT name FROM sqlite_master WHERE name='confluence_events'")).fetchone()) else None
        await db.executemany("INSERT INTO runner_watchlist_wallets VALUES (?,?)",
                             [("E1", "wallet:E1"), ("E2", "wallet:E2"), ("E3", "funder:F")])
        await db.execute("""CREATE TABLE IF NOT EXISTS social_signals (id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER, platform TEXT, username TEXT, ticker TEXT, contract_address TEXT, sentiment TEXT,
            confidence REAL, post_text TEXT, post_url TEXT, signal_type TEXT DEFAULT 'mention',
            created_at TEXT DEFAULT (datetime('now')))""")
        await db.execute("DELETE FROM social_signals WHERE contract_address IN ('KOLM','TWO')")
        await db.execute("INSERT INTO social_signals (platform, username, ticker, contract_address) "
                         "VALUES ('x','kolA','KOLT','KOLM')")
        await db.execute("INSERT INTO social_signals (platform, username, ticker, contract_address, created_at) "
                         "VALUES ('x','kolOld','T','TWO', datetime('now','-2 hours'))")
        await db.commit()


@pytest.mark.asyncio
async def test_scan_fires_on_rules_and_respects_ceiling(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch)
    async with get_db() as db:
        ev = {e["mint"]: e for e in await cf.scan(db, NOW)}
        assert set(ev) == {"HOT", "KOLM"}  # BIG over $60k; TWO has 2 + an out-of-window post
        assert ev["HOT"]["rule"] == "watch>=3" and ev["HOT"]["watch"] == 3
        assert ev["KOLM"]["rule"] == "watch+kol" and ev["KOLM"]["symbol"] == "KOLT"
        assert all(e["conviction"] <= 0.69 for e in ev.values())
        assert await cf.scan(db, NOW + 30) == []  # same window, nothing new
    got = await cf.recent()
    assert got["total"] >= 2 and got["events"][0]["anti_pump"]["ok"]


@pytest.mark.asyncio
async def test_post_goes_to_pool_capped(tmp_path, monkeypatch):
    from backend.routers import intel
    seen = []

    async def fake(symbol, source, type_, conviction, direction, detail, mint, chain, record=None):
        seen.append((symbol, source, type_, conviction, direction, mint, chain))
        return {"status": "ingested"}
    monkeypatch.setattr(intel, "ingest_signal_internal", fake)
    await _setup(tmp_path, monkeypatch)
    async with get_db() as db:
        ev = await cf.scan(db, NOW)
    assert await cf.post(ev) == len(ev)
    assert {s[1] for s in seen} == {"runner_confluence"} and all(s[3] <= 0.69 and s[4] == "BUY" for s in seen)


@pytest.mark.asyncio
async def test_enricher_off_by_default_and_only_adds(tmp_path, monkeypatch):
    from backend.signal_fusion import enrich_from_confluence
    await _setup(tmp_path, monkeypatch)
    async with get_db() as db:
        await cf.scan(db, NOW)
    sig = {"mint": "HOT", "conviction": 0.5}
    assert await enrich_from_confluence(dict(sig)) == sig
    monkeypatch.setenv("CONFLUENCE_ENRICHER", "1")
    out = await enrich_from_confluence(dict(sig))
    assert out["conviction"] == 0.5 and out["confluence_rule"] == "watch>=3"


@pytest.mark.asyncio
async def test_endpoint(client):
    r = await client.get("/api/intel/confluence")
    assert r.status_code == 200 and r.json()["max_conviction"] == 0.69
