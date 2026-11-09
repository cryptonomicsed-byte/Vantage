"""P4-16: walk-forward -- no leakage, live vs shadow top-K, challenger split."""
import pytest

from backend import walk_forward as wf

DAY = 86400
T0 = 1_800_000_000 - 1_800_000_000 % DAY


def _call(i, src, conv, day, mint="M", direction="long"):
    return {"id": f"r{i}", "source": src, "direction": direction, "conviction": conv,
            "created_at": T0 + day * DAY + 3600, "mint": f"{mint}{i}", "symbol": "T"}


def test_shadow_beats_live_once_history_exists_and_nothing_leaks():
    calls, marks = [], {}
    i = 0
    # day 0: history only -- 'loud' is high-conviction and always wrong, 'quiet' always right
    for _ in range(20):
        for src, conv, hit in (("loud", 0.9, False), ("quiet", 0.6, True)):
            calls.append(_call(i, src, conv, 0)); marks[f"r{i}"] = {"hit": hit, "scored": True,
                                                                  "marked_at": T0 + DAY - 60}; i += 1
    # day 1: same pattern; outcomes marked on day 2 (must NOT inform day 1)
    for _ in range(3):
        for src, conv, hit in (("loud", 0.9, False), ("quiet", 0.6, True)):
            calls.append(_call(i, src, conv, 1)); marks[f"r{i}"] = {"hit": hit, "scored": True,
                                                                  "marked_at": T0 + 2 * DAY + 60}; i += 1
    out = wf.compute(calls, marks, T0, 2, k=3)
    d0, d1 = out["days"]
    # day 0 had no prior history: shadow == live order
    assert d0["live_top_k_hit_rate"] == d0["shadow_top_k_hit_rate"] == 0.0 and d0["sources_with_history"] == 0
    # day 1 uses day-0 marks (marked before day 1) -> shadow promotes 'quiet'
    assert d1["sources_with_history"] == 2
    assert d1["live_top_k_hit_rate"] == 0.0 and d1["shadow_top_k_hit_rate"] == 1.0
    assert out["summary"]["enough_data"] is False or out["summary"]["top_k_slots"] >= 10


def test_leakage_guard_marks_after_day_start_not_used():
    calls = [_call(0, "a", 0.9, 0), _call(1, "b", 0.5, 0)]
    marks = {"r0": {"hit": False, "scored": True, "marked_at": T0 + 10},
             "r1": {"hit": True, "scored": True, "marked_at": T0 + 10}}
    d0 = wf.compute(calls, marks, T0, 1, k=1)["days"][0]
    assert d0["sources_with_history"] == 0 and d0["live_top_k_hit_rate"] == d0["shadow_top_k_hit_rate"]


def test_unscored_and_hold_excluded_and_empty_days_listed():
    calls = [_call(0, "a", 0.9, 0, direction="hold"), _call(1, "a", 0.9, 0)]
    marks = {"r0": {"hit": True, "scored": True, "marked_at": T0}, "r1": {"hit": None, "scored": False,
                                                                       "marked_at": T0}}
    out = wf.compute(calls, marks, T0, 3, k=5)
    assert [d["n"] for d in out["days"]] == [0, 0, 0] and out["summary"]["live_top_k_hit_rate"] is None


def test_challenger_split_needs_min_n():
    calls, marks, ch = [], {}, []
    for i in range(24):
        c = _call(i, "s", 0.8, 0)
        calls.append(c)
        hit = i % 2 == 0
        marks[c["id"]] = {"hit": hit, "scored": True, "marked_at": T0}
        ch.append({"source": "s", "direction": "long", "mint": c["mint"], "symbol": "T",
                   "call_ts": c["created_at"] + 600, "would_veto": 0 if hit else 1})
    out = wf.compute(calls, marks, T0, 1, k=5, challenges=ch)["challenger"]
    assert out["vetoed"] == 12 and out["vetoed_hit_rate"] == 0.0 and out["kept_hit_rate"] == 1.0
    small = wf.compute(calls[:4], marks, T0, 1, k=5, challenges=ch[:4])["challenger"]
    assert small["vetoed_hit_rate"] is None  # below MIN_N


@pytest.mark.asyncio
async def test_report_fail_open_and_endpoint(client):
    out = await wf.report(3, 5, "24h", now=T0 + 3 * DAY, fetch=lambda s, w: None)
    assert out["status"] == "mycelium_unreachable" and out["summary"] is None
    out = await wf.report(3, 5, "24h", now=T0 + 3 * DAY, fetch=lambda s, w: {})
    assert out["status"] == "ok" and len(out["days"]) == 3
    r = await client.get("/api/pipeline/walk-forward?days=2")
    assert r.status_code == 200 and "status" in r.json()
