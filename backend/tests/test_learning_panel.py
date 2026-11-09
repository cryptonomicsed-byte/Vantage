"""Learning panel backend: GET /api/pipeline/learning is read-only and honest.

Acceptance: it reports every flag; it never writes shadow_rank_log (only the
loop does); recent_log returns the latest batch with live->shadow movement;
hit rates come through from Mycelium findings, and an unreachable Mycelium
yields an empty list rather than an error.
"""
import json

import pytest

from backend import shadow_rank as sr


@pytest.mark.asyncio
async def test_recent_log_returns_latest_batch_with_movement():
    ranked = [
        {"source": "a", "symbol": "X", "direction": "BUY", "mint": "m1", "ts": 1,
         "conviction": 0.9, "live_rank": 1, "shadow_rank": 2, "reliability": 0.25, "hits": 0, "n": 30},
        {"source": "b", "symbol": "Y", "direction": "BUY", "mint": "m2", "ts": 1,
         "conviction": 0.6, "live_rank": 2, "shadow_rank": 1, "reliability": 0.75, "hits": 30, "n": 30},
    ]
    await sr.log_ranking(ranked)
    out = await sr.recent_log()
    assert out["ts"] is not None and out["batches"] >= 1
    first = out["rows"][0]
    assert first["shadow_rank"] == 1 and first["source"] == "b" and first["symbol"] == "Y"
    assert first["moved"] == 1  # climbed one place under the shadow rank


def test_hit_rate_findings_all_windows(monkeypatch):
    payloads = [{"source": "s", "window": w, "n": 40, "hits": 20, "hit_rate": 0.5,
                 "brier": 0.2, "min_samples_met": True} for w in ("1h", "24h")]
    monkeypatch.setattr(sr, "_get", lambda url, timeout=10: {
        "findings": [{"payload": json.dumps(p)} for p in payloads]})
    rows = sr.fetch_hit_rate_findings()
    assert {r["window"] for r in rows} == {"1h", "24h"}
    monkeypatch.setattr(sr, "_get", lambda url, timeout=10: None)
    assert sr.fetch_hit_rate_findings() == []


def test_enabled_reads_env_each_call(monkeypatch):
    monkeypatch.setenv("SHADOW_RANK_ENABLED", "1")
    assert sr.enabled() is True
    monkeypatch.setenv("SHADOW_RANK_ENABLED", "0")
    assert sr.enabled() is False


@pytest.mark.asyncio
async def test_learning_endpoint_is_read_only(client, registered_agent, monkeypatch):
    monkeypatch.setattr(sr, "_get", lambda url, timeout=10: None)
    h = {"X-Agent-Key": registered_agent["api_key"]}
    r1 = await client.get("/api/pipeline/learning", headers=h)
    r2 = await client.get("/api/pipeline/learning", headers=h)
    assert r1.status_code == 200 and r2.status_code == 200, r1.text
    body = r1.json()
    assert set(body["flags"]) == {"SIGNAL_RECORD_V1", "SIGNAL_LEDGER_ENABLED",
                                  "LEDGER_MIRROR_ENABLED", "SHADOW_RANK_ENABLED"}
    assert body["hit_rates"] == []
    assert "ledger" in body and "shadow" in body
    # two reads, same shadow batch count: reading never logs
    assert r1.json()["shadow"]["batches"] == r2.json()["shadow"]["batches"]
