"""Ecology branch of System One — memory / signal routing with a Hermetic REM valve.

Same shape as trading_decision.py: build a state dict, ask system_one.decide()
for a calibrated answer, then reconcile the model's answer against a
deterministic scorer.

Difference: for memory the deterministic scorer is authoritative.
hermetic_rem.score_memory() is a pure function over the candidate's numeric
features, so unlike an LLM it cannot drift, hallucinate a fate, or be prompt
injected by the memory content itself. The LLM contributes the qualitative
read (salience, actionable, why) and is recorded next to the REM verdict —
it never overrides it. Where the two disagree, the result carries
agreement=False and both verdicts, which is what makes the disagreement
observable instead of silently averaged away.

High-stakes candidates (memory that gates money, identity, or publication) also
get an Ifá odu cast via odu_cast: see eco_decide(high_stakes=True).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

try:  # package import (daemon: `from backend.eco_decision import ...`)
    from .hermetic_rem import MemoryFate, RemResult, score_memory
    from .system_one import CHOICE, NOUL, SCORE, decide
except ImportError:  # top-level import (backend/ itself on sys.path)
    from hermetic_rem import MemoryFate, RemResult, score_memory
    from system_one import CHOICE, NOUL, SCORE, decide

logger = logging.getLogger(__name__)

FATE_OPTIONS = [f.value for f in MemoryFate]
SALIENCE_OPTIONS = ["none", "low", "medium", "high"]

# A model fate is only recorded as contradicting REM when it commits to it.
CONTRADICTION_CONFIDENCE = float(os.environ.get("ECO_CONTRADICTION_CONF", "0.7"))

# Stakes above this trigger an odu cast even when the caller did not ask.
HIGH_STAKES_USD = float(os.environ.get("ECO_HIGH_STAKES_USD", "250"))

ODU_AGENT_SEED = os.environ.get("ODU_AGENT_SEED", "vantage-eco")

MYCELIUM_PYSRC = "/opt/ares/mycelium-gateway/pysrc"


def _num(candidate: dict, key: str, default: float) -> float:
    try:
        value = candidate.get(key, default)
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_state(candidate: dict) -> dict:
    """Narrow the candidate to what the model should see.

    Deliberately excludes anything that looks like a live secret: memory bodies
    routinely carry API keys, and a decision prompt is the last place they
    should land. `content` is truncated, and redaction is the caller's job for
    the remainder.
    """
    content = candidate.get("content") or candidate.get("summary") or ""
    return {
        "content": str(content)[:2000],
        "domain": candidate.get("domain", "unknown"),
        "source": candidate.get("source", "unknown"),
        "confidence": _num(candidate, "confidence", 0.5),
        "novelty": _num(candidate, "novelty", 0.5),
        "collective_value": _num(candidate, "collective_value", 0.5),
        "recency_hours": _num(candidate, "recency_hours", 24.0),
        "correct": candidate.get("correct"),
        "tags": candidate.get("tags", []),
    }


QUESTIONS = {
    "fate": {
        "type": CHOICE,
        "options": FATE_OPTIONS,
        "description": (
            "Where this memory belongs: promote (anchor as a finding), "
            "fold-to-hive (share with the swarm), fold-to-private (keep, do not "
            "broadcast), archive (dormant)"
        ),
    },
    "salience": {
        "type": SCORE,
        "options": SALIENCE_OPTIONS,
        "description": "How much attention this memory deserves",
    },
    "actionable": {
        "type": NOUL,
        "description": "Does this memory change what an agent should do next?",
    },
}


async def eco_decide(
    candidate: dict,
    *,
    emit: bool = False,
    high_stakes: Optional[bool] = None,
    stake_usd: Optional[float] = None,
    model_override: Optional[str] = None,
) -> dict:
    """Route one memory candidate.

    Returns:
      {
        fate, score, principles, reasoning,     # REM — authoritative
        llm: {fate, salience, actionable, probability, model_used, latency_ms},
        agreement: bool, contested: bool,
        odu: {...} | None,
        emitted: bool,
      }
    """
    state = _build_state(candidate)
    stake = _num(candidate, "stake_usd", 0.0) if stake_usd is None else float(stake_usd)

    rem: RemResult = score_memory(candidate)

    try:
        result = await decide(state, QUESTIONS, model_override=model_override)
    except Exception as exc:  # decide() is fail-soft, but never let it break routing
        logger.warning("eco_decide: system_one failed (%s)", exc)
        result = {"answers": {}, "error": str(exc)}

    answers = result.get("answers") or {}
    fate_ans = answers.get("fate", {}) or {}
    salience_ans = answers.get("salience", {}) or {}
    actionable_ans = answers.get("actionable", {}) or {}

    llm_fate = fate_ans.get("value")
    llm_prob = _safe_float(fate_ans.get("probability"), 0.0)
    if llm_fate not in FATE_OPTIONS:
        llm_fate = None

    agreement = llm_fate is None or llm_fate == rem.fate.value
    contested = (
        llm_fate is not None
        and llm_fate != rem.fate.value
        and llm_prob >= CONTRADICTION_CONFIDENCE
    )

    payload = {
        "fate": rem.fate.value,
        "score": round(rem.overall_score, 4),
        "principles": {p.name: round(p.score, 4) for p in rem.principles},
        "reasoning": rem.reasoning,
        "llm": {
            "fate": llm_fate,
            "fate_probability": llm_prob,
            "salience": salience_ans.get("value"),
            "salience_probability": _safe_float(salience_ans.get("probability"), 0.0),
            "actionable": actionable_ans.get("value"),
            "actionable_probability": _safe_float(actionable_ans.get("probability"), 0.0),
            "model_used": result.get("model_used"),
            "latency_ms": result.get("latency_ms", 0),
            "error": result.get("error"),
        },
        "agreement": agreement,
        "contested": contested,
        "odu": None,
        "emitted": False,
    }

    wants_cast = high_stakes if high_stakes is not None else stake >= HIGH_STAKES_USD
    if wants_cast:
        payload["odu"] = _cast_and_gate(candidate, stake)

    if emit:
        payload["emitted"] = _emit_trace(candidate, rem, payload)

    return payload


def _safe_float(value, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _cast_and_gate(candidate: dict, stake_usd: float) -> Optional[dict]:
    """Ifá cast for high-stakes routing. Fail-open: None on any error."""
    try:
        try:
            from . import odu_cast
        except ImportError:
            import odu_cast

        nonce = str(
            candidate.get("id")
            or candidate.get("signal_id")
            or candidate.get("content", "")[:64]
        )
        casting = odu_cast.cast(ODU_AGENT_SEED, nonce=nonce)
        verdict = odu_cast.gate(casting, stake_usd=stake_usd)
        return verdict.to_dict()
    except Exception as exc:
        logger.warning("eco_decide: odu cast failed (fail-open): %s", exc)
        return None


def _emit_trace(candidate: dict, rem: RemResult, payload: dict) -> bool:
    """Publish to the mycelium substrate. Fail-open, never raises."""
    try:
        import sys

        if MYCELIUM_PYSRC not in sys.path:
            sys.path.insert(0, MYCELIUM_PYSRC)
        from mycelium import core as mc

        session = str(candidate.get("id") or candidate.get("signal_id") or "")
        if not session:
            odu = payload.get("odu") or {}
            session = odu.get("cast_id") or "eco"
        domain = candidate.get("domain", "unknown")
        mc.emit(
            agent="vantage-eco",
            session=session,
            kind="rem-memory",
            action=rem.fate.value,
            outcome=(
                f"domain={domain} score={rem.overall_score:.3f} "
                f"agreement={payload['agreement']} contested={payload['contested']} "
                f"{rem.reasoning}"
            ),
        )
        if rem.fate in (MemoryFate.PROMOTE, MemoryFate.FOLD_TO_HIVE):
            mc.add_finding(
                agent="vantage-eco",
                session=session,
                finding=(
                    f"[{rem.fate.value}] {domain}: score={rem.overall_score:.3f} "
                    f"→ {rem.reasoning}"
                ),
                severity="info",
            )
        return True
    except Exception as exc:
        logger.debug("eco_decide: mycelium emit skipped: %s", exc)
        return False
