"""P4-06/P4-07: watchlist scoring rules, clustering, exclusions, radar import."""
import json
import time

import pytest

from backend import runner_watchlist as rw
from backend.db import get_db

NOW = 1_800_000_000.0
EX = frozenset({"dev", "sniper", "bundler", "insider", "bot", "funding_hub"})


def _buy(mint, wallet, rank, sol=5.0, funder=None, w=1.0, age_days=0.0):
    return {"mint": mint, "wallet": wallet, "rank": rank, "sol": sol, "funded_by": funder,
            "entry_ts": NOW - age_days * 86400, "tier_weight": w, "symbol": mint}


def test_contribution_formula():
    assert rw.contribution(1.0, 1, 5.0, 0) == 1.0
    assert rw.contribution(2.0, 4, 2.5, 0) == pytest.approx(2.0 * 0.25 * 0.5)
    assert rw.contribution(1.0, 1, 50.0, 0) == 1.0           # size caps at 1
    assert rw.contribution(1.0, 1, 5.0, 90) == pytest.approx(0.5)  # 90-day half-life
    assert rw.contribution(1.0, 101, 5.0, 0) == 0.0           # first 100 only


def test_needs_two_runners_and_scores_sum():
    out = rw.compute([_buy("R1", "A", 1), _buy("R2", "A", 2), _buy("R1", "ONCE", 1)], {}, NOW, EX)
    assert [e["entity"] for e in out] == ["wallet:A"]
    assert out[0]["score"] == pytest.approx(1.5) and out[0]["runners"] == 2


def test_excluded_labels_contribute_nothing():
    buys = [_buy("R1", "BOT", 1), _buy("R2", "BOT", 1), _buy("R1", "OK", 3), _buy("R2", "OK", 3)]
    out = rw.compute(buys, {"BOT": ["bot"]}, NOW, EX)
    assert [e["entity"] for e in out] == ["wallet:OK"]


def test_cluster_counts_once_per_runner():
    # one funder splits into three wallets on R1 and R2: counts as its best member, once per runner
    buys = [_buy("R1", "s1", 1, funder="F"), _buy("R1", "s2", 2, funder="F"), _buy("R1", "s3", 3, funder="F"),
            _buy("R2", "s2", 2, funder="F")]
    out = rw.compute(buys, {}, NOW, EX)
    assert len(out) == 1 and out[0]["entity"] == "funder:F"
    assert out[0]["score"] == pytest.approx(1.0 + 0.5) and set(out[0]["wallets"]) == {"s1", "s2", "s3"}


def test_funding_hub_is_not_a_cluster():
    buys = [_buy("R1", "x", 1, funder="EXCH"), _buy("R2", "x", 1, funder="EXCH"),
            _buy("R1", "y", 2, funder="EXCH"), _buy("R2", "y", 2, funder="EXCH")]
    out = rw.compute(buys, {"EXCH": ["funding_hub"]}, NOW, EX)
    assert sorted(e["entity"] for e in out) == ["wallet:x", "wallet:y"]


def test_read_radar_dict_and_list(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema_version": 1, "generated_at": "2026-08-27T16:52:00Z", "meta": {},
                             "watchlist": [{"address": "Rad1", "conviction": 0.9, "tier": "S", "evidence": {}},
                                           {"address": "bad"}]}))
    r = rw.read_radar(str(p))
    assert r["present"] and r["generated_at"].startswith("2026-08-27") and [w["address"] for w in r["wallets"]] == ["Rad1"]
    p.write_text(json.dumps([{"address": "L", "conviction": 0.6}]))
    assert rw.read_radar(str(p))["wallets"][0]["address"] == "L"
    assert rw.read_radar(str(tmp_path / "missing.json"))["present"] is False


@pytest.mark.asyncio
async def test_rebuild_with_radar_separate_source(tmp_path, monkeypatch):
    rp = tmp_path / "radar.json"
    rp.write_text(json.dumps({"generated_at": "2026-08-27T00:00:00Z", "watchlist": [
        {"address": "W1", "conviction": 0.8}, {"address": "LOW", "conviction": 0.3},
        {"address": "RADONLY", "conviction": 0.7}, {"address": "RBOT", "conviction": 0.9}]}))
    monkeypatch.setattr(rw, "RADAR_PATH", str(rp))
    async with get_db() as db:
        from backend import wallet_labels
        await wallet_labels._ensure(db)
        for d in rw.DDL:
            await db.execute(d)
        from backend.runner_detect import ensure_table
        await ensure_table(db)
        await db.execute("DELETE FROM runner_early_buyers WHERE mint IN ('WR1','WR2')")
        for m in ("WR1", "WR2"):
            await db.execute("INSERT OR REPLACE INTO runner_tokens (mint, symbol, tier_usd, tier_weight, peak_mcap_usd, "
                             "peak_at, detected_at, backed, liq_usd) VALUES (?,?,1e6,1.0,1e6,1,?,1,90000)",
                             (m, m, int(time.time())))
            await db.execute("INSERT INTO runner_early_buyers (mint, wallet, rank, entry_ts, sol, source) "
                             "VALUES (?, 'W1', 1, ?, 5.0, 'curve')", (m, int(time.time())))
        await wallet_labels.put(db, [("RBOT", "bot", {"x": 1})])
        await db.commit()
        out = await rw.rebuild(db)
        mapped = dict(await (await db.execute("SELECT wallet, entity FROM runner_watchlist_wallets")).fetchall())
    assert out["radar_wallets"] == 2  # W1 + RADONLY (LOW under 0.50, RBOT excluded label)
    assert mapped["W1"] == "wallet:W1" and mapped["RADONLY"] == "radar:RADONLY" and "LOW" not in mapped
    got = await rw.get()
    ent = next(e for e in got["entities"] if e["entity"] == "wallet:W1")
    assert ent["radar_conviction"] == 0.8 and ent["score"] == pytest.approx(2.0, rel=1e-3)
    assert got["radar"]["wallets"] == 2 and got["radar"]["generated_at"].startswith("2026-08-27")


@pytest.mark.asyncio
async def test_endpoint(client):
    r = await client.get("/api/intel/runner-watchlist")
    assert r.status_code == 200 and "entities" in r.json() and "radar" in r.json()
