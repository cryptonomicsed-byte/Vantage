"""P2-05: the shadow rank re-ranks by conviction x source reliability, never
fed back. Acceptance: reliability = (hits+15)/(n+30); shadow_score =
conviction x reliability; a source with no data gets the 0.5 prior; the
ranking is logged to shadow_rank_log with live_rank vs shadow_rank; a signal
from a strong source overtakes a higher-conviction signal from a weak one.
"""
import json

import pytest

from backend import shadow_rank as sr


def test_reliability_is_laplace_smoothed():
    assert sr.reliability(0, 0) == 0.5            # prior
    assert sr.reliability(15, 30) == 0.5          # neutral
    assert abs(sr.reliability(30, 30) - 0.75) < 1e-9  # strong


def test_shadow_rank_reorders_by_reliability():
    signals = [
        {"source": "weak", "conviction": 0.9, "symbol": "A", "direction": "BUY", "mint": "m1", "ts": 100},
        {"source": "strong", "conviction": 0.6, "symbol": "B", "direction": "BUY", "mint": "m2", "ts": 90},
    ]
    hit_rates = {"weak": {"hits": 0, "n": 30}, "strong": {"hits": 30, "n": 30}}
    ranked = sr.shadow_rank(signals, hit_rates)
    # live rank: weak (0.9) first; shadow rank: strong overtakes it
    by_source = {r["source"]: r for r in ranked}
    assert by_source["weak"]["live_rank"] == 1 and by_source["weak"]["shadow_rank"] == 2
    assert by_source["strong"]["live_rank"] == 2 and by_source["strong"]["shadow_rank"] == 1
    # weak: 0.9 * (15/30=0.5) = 0.45 ; strong: 0.6 * (45/60=0.75) = 0.45 -> tie? no:
    # weak reliability = (0+15)/(30+30)=15/60=0.25 -> 0.9*0.25=0.225
    # strong reliability = (30+15)/(30+30)=45/60=0.75 -> 0.6*0.75=0.45
    assert abs(by_source["strong"]["shadow_score"] - 0.45) < 1e-9
    assert abs(by_source["weak"]["shadow_score"] - 0.225) < 1e-9


def test_missing_source_gets_the_prior():
    signals = [{"source": "newcomer", "conviction": 0.8, "symbol": "C",
                "direction": "BUY", "mint": "m3", "ts": 1}]
    ranked = sr.shadow_rank(signals, {})
    assert ranked[0]["reliability"] == 0.5
    assert abs(ranked[0]["shadow_score"] - 0.4) < 1e-9


def test_fetch_source_hit_rates_parses_payload_string(monkeypatch):
    findings = {"findings": [
        {"miner": "source_hit_rate", "payload": json.dumps(
            {"source": "src", "window": "24h", "n": 30, "hits": 20, "min_samples_met": True})},
        {"miner": "source_hit_rate", "payload": json.dumps(
            {"source": "other", "window": "1h", "n": 5, "hits": 1})},
    ]}
    monkeypatch.setattr(sr, "_get", lambda url, timeout=10: findings)
    rates = sr.fetch_source_hit_rates()
    assert rates == {"src": {"hits": 20, "n": 30}}  # only 24h, only source_hit_rate


def test_fetch_failure_is_fail_open(monkeypatch):
    monkeypatch.setattr(sr, "_get", lambda url, timeout=10: None)
    assert sr.fetch_source_hit_rates() == {}


@pytest.mark.asyncio
async def test_log_ranking_writes_shadow_rank_log():
    from backend.db import get_db
    ranked = [
        {"source": "a", "symbol": "X", "direction": "BUY", "mint": "m", "ts": 1,
         "conviction": 0.8, "live_rank": 1, "shadow_rank": 2,
         "reliability": 0.5, "hits": 0, "n": 0},
    ]
    n = await sr.log_ranking(ranked)
    assert n == 1
    async with get_db() as db:
        db.row_factory = None
        row = await (await db.execute(
            "SELECT call_key, live_rank, shadow_rank, conviction, reliability, n "
            "FROM shadow_rank_log ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert row[0] == "a|X|BUY|m"
        assert row[1] == 1 and row[2] == 2
        assert abs(row[3] - 0.8) < 1e-9 and abs(row[4] - 0.5) < 1e-9
        assert row[5] == 0


def test_n_eff_preferred_when_present(monkeypatch):
    """P3-02: ten echoes of one call don't buy ten calls of reliability."""
    import json as _j
    from backend import shadow_rank as _sr
    payloads = [{"source": "echo", "window": "24h", "n": 40, "hits": 40, "n_eff": 4, "hits_eff": 4.0},
                {"source": "old", "window": "24h", "n": 40, "hits": 20}]
    monkeypatch.setattr(_sr, "_get", lambda url, timeout=10: {
        "findings": [{"payload": _j.dumps(p)} for p in payloads]})
    hr = _sr.fetch_source_hit_rates()
    assert hr["echo"]["n"] == 4 and hr["echo"]["hits"] == 4.0 and hr["echo"]["n_raw"] == 40
    assert hr["old"] == {"hits": 20, "n": 40}
    # (4+15)/(4+30) is far closer to the prior than (40+15)/(40+30)
    assert _sr.reliability(4.0, 4) < _sr.reliability(40, 40)
