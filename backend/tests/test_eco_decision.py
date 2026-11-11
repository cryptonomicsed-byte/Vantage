"""eco_decision: REM is authoritative, the model is advisory."""
import asyncio
import os

import pytest

from backend import eco_decision
from backend.hermetic_rem import MemoryFate, score_memory


def _candidate(**over):
    base = {
        "id": "mem-1",
        "content": "Kalshi mispricing on CPI print, 3 consecutive confirmations",
        "domain": "prediction_market",
        "source": "kalshi",
        "confidence": 0.8,
        "novelty": 0.6,
        "collective_value": 0.8,
        "recency_hours": 1.0,
        "correct": True,
    }
    base.update(over)
    return base


def _answers(fate, fate_prob=0.9, salience="high", actionable=True):
    return {
        "answers": {
            "fate": {"type": "choice", "value": fate, "probability": fate_prob},
            "salience": {"type": "score", "value": salience, "probability": 0.8},
            "actionable": {"type": "noul", "value": actionable, "probability": 0.75},
        },
        "model_used": "stub",
        "latency_ms": 12,
    }


def test_rem_fate_wins_when_model_disagrees(monkeypatch):
    candidate = _candidate()
    rem = score_memory(candidate)
    other = next(f.value for f in MemoryFate if f != rem.fate)

    async def fake_decide(state, questions, model_override=None):
        return _answers(other)

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    out = asyncio.run(eco_decision.eco_decide(candidate))

    assert out["fate"] == rem.fate.value          # REM authoritative
    assert out["llm"]["fate"] == other            # model recorded, not obeyed
    assert out["agreement"] is False
    assert out["contested"] is True


def test_low_confidence_disagreement_is_not_contested(monkeypatch):
    candidate = _candidate()
    rem = score_memory(candidate)
    other = next(f.value for f in MemoryFate if f != rem.fate)

    async def fake_decide(state, questions, model_override=None):
        return _answers(other, fate_prob=0.3)

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    out = asyncio.run(eco_decision.eco_decide(candidate))
    assert out["agreement"] is False
    assert out["contested"] is False


def test_agreement_when_model_matches(monkeypatch):
    candidate = _candidate()
    rem = score_memory(candidate)

    async def fake_decide(state, questions, model_override=None):
        return _answers(rem.fate.value)

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    out = asyncio.run(eco_decision.eco_decide(candidate))
    assert out["agreement"] is True
    assert out["contested"] is False


def test_fail_open_when_llm_unavailable(monkeypatch):
    candidate = _candidate()
    rem = score_memory(candidate)

    async def boom(state, questions, model_override=None):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(eco_decision, "decide", boom)
    out = asyncio.run(eco_decision.eco_decide(candidate))
    assert out["fate"] == rem.fate.value
    assert out["llm"]["fate"] is None
    assert out["agreement"] is True  # nothing to contradict
    assert "gateway down" in out["llm"]["error"]


def test_no_odu_below_stake_threshold(monkeypatch):
    async def fake_decide(state, questions, model_override=None):
        return _answers("fold-to-private")

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    out = asyncio.run(eco_decision.eco_decide(_candidate(stake_usd=1.0)))
    assert out["odu"] is None


def test_odu_cast_attached_for_high_stakes(monkeypatch):
    async def fake_decide(state, questions, model_override=None):
        return _answers("fold-to-private")

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    # The cast seed is instance config with no built-in default.
    monkeypatch.setattr(eco_decision, "ODU_AGENT_SEED", "test-instance-seed")
    out = asyncio.run(
        eco_decision.eco_decide(_candidate(stake_usd=5000.0), high_stakes=True)
    )
    odu = out["odu"]
    assert odu is not None
    assert odu["decision"] in ("allow", "escalate", "veto")
    assert odu["cast_id"]
    assert 1 <= odu["primary"]["odu_index"] <= 256
    assert 1 <= odu["corroborating"]["odu_index"] <= 256


def test_state_does_not_leak_long_content(monkeypatch):
    async def fake_decide(state, questions, model_override=None):
        assert len(state["content"]) <= 2000
        return _answers("archive")

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    asyncio.run(eco_decision.eco_decide(_candidate(content="x" * 5000)))


def test_emit_is_opt_in(monkeypatch):
    async def fake_decide(state, questions, model_override=None):
        return _answers("archive")

    monkeypatch.setattr(eco_decision, "decide", fake_decide)
    out = asyncio.run(eco_decision.eco_decide(_candidate()))
    assert out["emitted"] is False


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("ECO_LIVE"),
    reason="live gateway test; set ECO_LIVE=1 to run",
)
def test_live_decide_against_gateway():
    """Live smoke test — needs the LiteLLM gateway on 127.0.0.1:8300.

    Opt-in: CI must not fail because a provider was slow.
    """
    out = asyncio.run(eco_decision.eco_decide(_candidate(), model_override="deepseek-chat"))
    assert "fate" in out
    if out["llm"]["error"]:
        pytest.skip(f"gateway unavailable: {out['llm']['error']}")
    assert out["llm"]["fate"] in eco_decision.FATE_OPTIONS
