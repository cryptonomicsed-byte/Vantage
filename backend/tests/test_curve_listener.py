"""P4-01: curve listener decode, storage, retention, gaps and coverage.

fixtures_curve_logs.json is real public pump.fun program logs captured from
the free mainnet websocket (logsSubscribe), so decode is tested against the
chain's actual bytes, not a hand-built buffer only.
"""
import base64
import json
import struct
import time
from pathlib import Path

import pytest

from backend import curve_listener as cl

FIX = json.loads((Path(__file__).parent / "fixtures_curve_logs.json").read_text())


def _event(mint=b"\x01" * 32, user=b"\x02" * 32, sol=1_500_000_000, tok=50_000_000_000_000,
           buy=True, ts=1_700_000_000, vsol=31_500_000_000, vtok=1_020_000_000_000_000, extra=b""):
    return (cl.TRADE_DISC + mint + struct.pack("<QQ", sol, tok) + bytes([1 if buy else 0]) + user
            + struct.pack("<qQQ", ts, vsol, vtok) + extra)


def test_discriminator_is_pinned():
    assert cl.TRADE_DISC.hex() == "bddb7fd34ee661ee"


def test_decode_synthetic_and_ignores_appended_fields():
    e = cl.decode_trade_event(_event(extra=b"\x00" * 64))
    assert e["sol"] == 1.5 and e["tokens"] == 50_000_000 and e["side"] == "buy"
    assert e["v_sol_reserves"] == 31.5 and e["v_token_reserves"] == 1_020_000_000
    assert e["timestamp"] == 1_700_000_000
    assert cl.decode_trade_event(b"\x00" * 200) is None
    assert cl.decode_trade_event(cl.TRADE_DISC + b"\x00" * 10) is None


def test_decode_real_chain_logs():
    for item in FIX["trades"]:
        rows, _ = cl.rows_from_notification(item["value"], item["slot"], 150.0)
        assert rows, "every captured trade tx carries at least one TradeEvent"
        for r in rows:
            assert len(r["mint"]) in (43, 44) and len(r["wallet"]) in (43, 44)
            assert r["side"] in ("buy", "sell") and r["sol"] >= 0 and r["tokens"] > 0
            assert r["v_token_reserves"] > 0
            if r["quote"] == "sol":
                assert r["mcap_usd"] and r["mcap_usd"] > 0
            else:  # non-SOL quote curve: no SOL-based market cap, never $0
                assert r["mcap_usd"] is None and r["v_sol_reserves"] == 0
            assert r["slot"] == item["slot"] and r["sig"] == item["value"]["signature"]
            # timestamp is a real recent unix time (2025+)
            assert 1_735_000_000 < r["block_time"] < 2_100_000_000


def test_create_is_detected_on_real_logs():
    item = FIX["create"]
    rows, created = cl.rows_from_notification(item["value"], item["slot"], 150.0)
    assert created and created <= {r["mint"] for r in rows}


def test_real_fixture_includes_a_non_sol_quote_curve():
    quotes = {r["quote"] for it in FIX["trades"] for r in cl.rows_from_notification(it["value"], 1, 150.0)[0]}
    assert quotes == {"sol", "other"}


def test_token_account_create_is_not_a_pump_create():
    assert not cl.saw_create(["Program log: Instruction: CreateIdempotentAssociatedTokenAccount"])
    assert cl.saw_create(["Program log: Instruction: CreateV2"])


def test_failed_tx_yields_nothing():
    v = dict(FIX["trades"][0]["value"], err={"InstructionError": [0, "Custom"]})
    assert cl.rows_from_notification(v, 1, 150.0) == ([], set())


def test_mcap_formula():
    # 30 SOL / 1.073B tokens virtual, SOL=$150 -> ~$4.19k
    assert round(cl.mcap_usd(30.0, 1_073_000_000, 150.0)) == 4194
    assert cl.mcap_usd(30.0, 1_073_000_000, None) is None


def _row(sig, ix, mint, ts, mc, wallet="W", slot=1):
    return {"sig": sig, "ix": ix, "slot": slot, "block_time": ts, "mint": mint, "wallet": wallet,
            "side": "buy", "sol": 1.0, "tokens": 1.0, "v_sol_reserves": 30.0,
            "v_token_reserves": 1e9, "mcap_usd": mc, "source": "rpc"}


def test_storage_summary_dedupe_and_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "curve.db"))
    monkeypatch.setattr(cl, "KEEP_FIRST", 3)
    c = cl.connect()
    now = int(time.time())
    old = now - 8 * 3600  # older than RECENT_HOURS, younger than FIRST_DAYS
    rows = [_row(f"s{i}", 0, "MINT", old + i, 1000.0 * (i + 1)) for i in range(6)]
    assert cl.write_batch(c, rows, {"MINT"}) == 6
    assert cl.write_batch(c, rows[:2], set()) == 0  # backfill overlap is ignored
    m = c.execute("SELECT trades_seen, created_seen, max_mcap_usd, first_seen FROM curve_mints").fetchone()
    assert m == (6, 1, 6000.0, old)
    cl.write_batch(c, [_row("new", 0, "MINT", now, 500.0)], set())
    assert cl.prune(c, now) == 3
    left = [r[0] for r in c.execute("SELECT n_in_mint FROM curve_trades ORDER BY n_in_mint")]
    assert left == [1, 2, 3, 7]  # first 3 kept + the recent one
    assert c.execute("SELECT max_mcap_usd FROM curve_mints").fetchone()[0] == 6000.0


def test_old_mints_keep_first_trades_only_if_big_or_pinned(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "curve.db"))
    c = cl.connect()
    now = int(time.time())
    ancient = now - 10 * 86400
    cl.write_batch(c, [_row("a", 0, "SMALL", ancient, 3000.0), _row("b", 0, "BIG", ancient, 40000.0),
                       _row("c", 0, "PINNED", ancient, 3000.0)], set())
    cl.pin(c, "PINNED")
    cl.prune(c, now)
    assert {r[0] for r in c.execute("SELECT mint FROM curve_trades")} == {"BIG", "PINNED"}
    assert c.execute("SELECT COUNT(*) FROM curve_mints").fetchone()[0] == 3  # summaries kept


def test_coverage_without_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "none.db"))
    monkeypatch.delenv("CURVE_LISTENER_ENABLED", raising=False)
    cov = cl.coverage()
    assert cov["enabled"] is False and cov["db"] is False and cov["trades"] == 0


def test_coverage_reports_gaps_as_they_are(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "curve.db"))
    c = cl.connect()
    cl.write_batch(c, [_row("a", 0, "M1", int(time.time()), 1.0)], set())
    gid = cl._open_gap(time.time() - 60, 10, "a")
    cl._close_gap(gid, time.time(), 20)
    cl._mark_gap(gid, "partial", 5, "rate limited (429) during backfill")
    cov = cl.coverage()
    assert cov["trades"] == 1 and cov["mints"] == 1 and cov["by_source"] == {"rpc": 1}
    assert cov["gaps"][0]["backfill"] == "partial" and cov["gaps"][0]["backfilled_events"] == 5


@pytest.mark.asyncio
async def test_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "x.db"))
    r = await client.get("/api/intel/curve/coverage")
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("CURVE_LISTENER_ENABLED", raising=False)
    assert cl.enabled() is False
