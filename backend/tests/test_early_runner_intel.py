"""Early Runner section: buy classification, liquidity-backed runners, profiles.

Both rules come from the live tables (2026-11-09): 111 of 114 detected
"runners" were wash pools quoting up to $750M on ~$2k of liquidity, and 142
of 192 RPC-traced early-buyer rows were sub-0.01 SOL side-pool dust."""
import time

import pytest

from backend import early_runner_intel as eri
from backend import runner_detect as rd
from backend import runner_watchlist as rw
from backend.db import get_db


def test_classify_dust_liquidity_buy():
    assert eri.classify({"sol": 0.0015, "tokens": 0.0139}) == "dust"
    assert eri.classify({"sol": None}) == "dust"
    assert eri.classify({"sol": 5994.99, "tokens": 203_973_578}) == "liquidity"
    assert eri.classify({"sol": 85.005, "tokens": 790_000_000 * 0.01}) == "buy"   # big but not 5% supply
    assert eri.classify({"sol": 0.5, "tokens": 1_000_000}) == "buy"


def test_annotate_real_rank_and_delay():
    rows = [{"wallet": "d", "sol": 0.001, "entry_ts": 100}, {"wallet": "a", "sol": 2.0, "entry_ts": 103},
            {"wallet": "L", "sol": 500, "tokens": 2e8, "entry_ts": 104}, {"wallet": "b", "sol": 1.0, "entry_ts": 110}]
    eri.annotate(rows, origin_time=100)
    assert [(r["kind"], r["real_rank"]) for r in rows] == [("dust", None), ("buy", 1), ("liquidity", None), ("buy", 2)]
    assert rows[1]["delay_s"] == 3 and rows[3]["delay_s"] == 10


def test_profile_bundles_shared_funders_and_dev():
    rows = [{"wallet": "dev", "sol": 2.0, "entry_ts": 100, "labels": ["dev"]}]
    rows += [{"wallet": f"b{i}", "sol": 0.5, "entry_ts": 101, "funded_by": "F", "labels": ["bundler"]} for i in range(4)]
    rows += [{"wallet": "late", "sol": 3.0, "entry_ts": 200}, {"wallet": "x", "sol": 0.001, "entry_ts": 201}]
    p = eri.profile(rows, origin_time=100)
    assert p["real_buyers"] == 6 and p["dust"] == 1 and p["first_real_delay_s"] == 0
    assert p["bundles"] and p["bundles"][0]["size"] == 5          # dev + 4 within 1 s of each other
    assert p["shared_funders"] == {"F": 4} and p["first_row"]["dev"] is True
    assert p["label_mix"]["bundler"] == 4


def test_best_by_mint_prefers_liquidity_backed_quotes():
    pairs = [{"baseToken": {"address": "M", "symbol": "FOTF"}, "marketCap": 699_179_155, "liquidity": {"usd": 1739}},
             {"baseToken": {"address": "M", "symbol": "FOTF"}, "marketCap": 1584, "liquidity": {"usd": 1738}},
             {"baseToken": {"address": "G", "symbol": "GTA6"}, "marketCap": 6_679_997, "liquidity": {"usd": 200_214}},
             {"baseToken": {"address": "G", "symbol": "GTA6"}, "marketCap": 9e9, "liquidity": {"usd": 900}}]
    q = rd.best_by_mint(pairs)
    assert q["M"]["backed"] is False
    assert q["G"]["backed"] is True and q["G"]["mcap"] == 6_679_997    # the wash quote doesn't win


@pytest.mark.asyncio
async def test_unbacked_quote_never_detects_and_demotes_existing():
    now = time.time()
    async with get_db() as db:
        await rd.ensure_table(db)
        await db.execute("DELETE FROM runner_tokens WHERE mint IN ('ERFAKE','ERREAL','EROLD')")
        await db.execute("INSERT INTO runner_tokens (mint, symbol, tier_usd, tier_weight, peak_mcap_usd, peak_at, "
                         "detected_at) VALUES ('EROLD','OLD',1e7,2.0,7e8,1,1)")
        await db.commit()
        await rd.apply_quotes(db, {
            "ERFAKE": {"mcap": 7e8, "symbol": "FAKE", "liq_usd": 1700, "backed": False},
            "ERREAL": {"mcap": 1.4e6, "symbol": "REAL", "liq_usd": 92_000, "backed": True},
            "EROLD": {"mcap": 1584, "symbol": "OLD", "liq_usd": 1738, "backed": False}}, {}, now)
        rows = dict(await (await db.execute(
            "SELECT mint, backed FROM runner_tokens WHERE mint IN ('ERFAKE','ERREAL','EROLD')")).fetchall())
        peak = (await (await db.execute("SELECT peak_mcap_usd FROM runner_tokens WHERE mint='EROLD'")).fetchone())[0]
    assert rows == {"ERREAL": 1, "EROLD": 0}          # fake never inserted; old one demoted
    assert peak == 7e8                                 # history kept, not rewritten
    listed = await rd.list_runners(200)
    mints = {r["mint"] for r in listed["runners"]}
    assert "ERREAL" in mints and "EROLD" not in mints and listed["unbacked_or_unverified"] >= 1


@pytest.mark.asyncio
async def test_watchlist_ignores_dust_and_unbacked_runners(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "RADAR_PATH", str(tmp_path / "none.json"))
    async with get_db() as db:
        from backend import wallet_labels
        await wallet_labels._ensure(db)
        for d in rw.DDL:
            await db.execute(d)
        await rd.ensure_table(db)
        await db.execute("DELETE FROM runner_early_buyers WHERE mint LIKE 'ERW%'")
        for m, b in (("ERW1", 1), ("ERW2", 1), ("ERWFAKE", 0)):
            await db.execute("INSERT OR REPLACE INTO runner_tokens (mint, symbol, tier_usd, tier_weight, "
                             "peak_mcap_usd, peak_at, detected_at, backed) VALUES (?,?,1e6,1.0,1e6,1,?,?)",
                             (m, m, int(time.time()), b))
            # a dust bot first, then the real early buyer
            await db.execute("INSERT INTO runner_early_buyers (mint, wallet, rank, entry_ts, sol, tokens, source) "
                             "VALUES (?, 'DUSTBOT', 1, ?, 0.001, 0.01, 'rpc')", (m, int(time.time())))
            await db.execute("INSERT INTO runner_early_buyers (mint, wallet, rank, entry_ts, sol, tokens, source) "
                             "VALUES (?, 'REALW', 2, ?, 3.0, 5e6, 'rpc')", (m, int(time.time())))
        await db.commit()
        await rw.rebuild(db)
        rows = await (await db.execute("SELECT entity, runners, contributions FROM runner_watchlist")).fetchall()
    ents = {e: n for e, n, _ in rows}
    assert "wallet:DUSTBOT" not in ents and ents.get("wallet:REALW") == 2   # fake runner not counted
    contrib = next(c for e, _, c in rows if e == "wallet:REALW")
    assert '"rank": 1' in contrib                                            # re-ranked among real buys



@pytest.mark.asyncio
async def test_slow_curve_coverage_falls_back_to_last_reading(monkeypatch):
    """Live: the overview showed the listener 'off' when coverage() was slow."""
    from backend import curve_listener as cl
    good = {"enabled": True, "last_event_age_s": 1.0, "trades_1h": 5, "mints_24h": 2, "created_seen_24h": 1,
            "db_bytes": 9, "state": {"gaps": [1, 2]}}
    monkeypatch.setattr(cl, "coverage", lambda: good)
    first = await eri._curve_summary()
    assert first["enabled"] and first["gaps"] == 2 and first["reading_age_s"] == 0

    def boom():
        raise TimeoutError("slow")
    monkeypatch.setattr(cl, "coverage", boom)
    again = await eri._curve_summary()
    assert again["enabled"] is True and again["trades_1h"] == 5 and again["reading_age_s"] >= 0
