"""P4-15: TradingOS challenger in shadow -- logged, never read by the live path."""
import json

import pytest

from backend import tradingos_challenger as tc

CALLS = [{"source": "alpha", "symbol": "PEPE", "direction": "long", "mint": "M1", "conviction": 0.8, "ts": 100},
         {"source": "beta", "symbol": "WIF", "direction": "long", "mint": "M2", "conviction": 0.77, "ts": 101}]


def _resp(sev, impact=-0.2):
    tool = {"signal_id": "x", "challenge_type": "liquidity", "arguments": ["thin pool"],
            "conviction_impact": impact, "severity": sev}
    return {"challenges": [{"agent_type": "challenger", "result": {"tool_calls": [{"input": tool}]}},
                           {"agent_type": "challenger", "result": json.dumps({"severity": "low",
                                                                              "conviction_impact": -0.05,
                                                                              "arguments": ["meh"]})}],
            "errors": []}


def test_summarize_finds_nested_findings():
    s = tc.summarize(_resp("critical"))
    assert s["findings"] == 2 and s["max_severity"] == "critical" and s["would_veto"] is True
    assert s["conviction_impact"] == pytest.approx(-0.25) and "thin pool" in s["arguments"]
    assert tc.summarize(_resp("medium"))["would_veto"] is False
    assert tc.summarize({"challenges": [], "errors": ["x"]})["max_severity"] is None


@pytest.mark.asyncio
async def test_not_configured_is_idle(monkeypatch):
    monkeypatch.delenv("TRADINGOS_INTEL_URL", raising=False)
    assert (await tc.challenge_once(CALLS))["status"] == "not_configured"


@pytest.mark.asyncio
async def test_shadow_pass_once_per_call(monkeypatch):
    monkeypatch.setenv("TRADINGOS_INTEL_URL", "http://tradingos.test")
    sent = []

    def post(url, body):
        sent.append((url, body))
        if body["signal_data"]["asset_symbol"] == "WIF":
            raise OSError("down")
        return _resp("high")
    calls = [dict(c, ts=c["ts"] + 10_000) for c in CALLS]
    out = await tc.challenge_once(calls, post=post)
    assert out == {"challenged": 1, "already": 0, "failed": 1, "status": "ok"}
    assert sent[0][0] == "http://tradingos.test/challenge"
    assert sent[0][1]["challenge_types"] == ["liquidity", "security", "manipulation"]
    again = await tc.challenge_once(calls, post=post)
    assert again["already"] == 2 and len(sent) == 2  # one try per call
    got = await tc.recent()
    row = {r["symbol"]: r for r in got["rows"]}
    assert row["PEPE"]["would_veto"] == 1 and row["PEPE"]["live_rank"] == 1 and row["WIF"]["status"] == "failed"
    assert got["shadow_only"] is True


def test_live_path_never_reads_the_shadow_table():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for f in ("pipeline.py", "trading_gate.py", "jev_decision.py", "trading_decision.py", "shadow_rank.py"):
        p = root / f
        if p.exists():
            assert "tradingos_challenges" not in p.read_text(), f


@pytest.mark.asyncio
async def test_endpoint(client):
    r = await client.get("/api/pipeline/tradingos-challenger")
    assert r.status_code == 200 and r.json()["shadow_only"] is True
