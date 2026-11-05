"""Brier score calibration gate: tracks rolling prediction accuracy, gates signals when degraded."""
import time, json
from backend.db import get_db
from backend.memory_engine import brier_score as _julia_brier

BRIER_WINDOW = 100  # last N predictions
BRIER_THRESHOLD = 0.35  # gate closes if rolling Brier > threshold (0=perfect, 1=worst)

async def record_prediction(signal_id: str, verdict: str, confidence: float):
    """Record a prediction for later scoring."""
    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO brier_predictions (signal_id, verdict, confidence, predicted_ts, outcome, scored)
               VALUES (?, ?, ?, ?, NULL, 0)""",
            (signal_id, verdict, confidence, int(time.time()))
        )
        await db.commit()

async def record_outcome(signal_id: str, outcome: str, confidence: float = 0.5,
                         domain: str = "signal", source: str = "unknown"):
    """Record outcome, run Hermetic REM valve, emit mycelium trace."""
    async with get_db() as db:
        await db.execute(
            "UPDATE brier_predictions SET outcome=?, scored=1 WHERE signal_id=?",
            (outcome, signal_id)
        )
        await db.commit()

    # Hermetic REM routing — fail-open
    try:
        from backend.hermetic_rem import score_memory, MemoryFate
        correct = (outcome == "yes")
        # Novelty: low for common sources, high for novel
        novelty = 0.3 if source in ("kalshi", "polymarket") else 0.6
        collective_value = 0.7 if correct else 0.4
        rem = score_memory({
            "confidence": confidence,
            "correct": correct,
            "novelty": novelty,
            "collective_value": collective_value,
            "recency_hours": 0.0,
            "domain": domain,
        })
        _emit_rem_trace(signal_id, domain, rem, confidence, correct)
    except Exception:
        pass


def _emit_rem_trace(signal_id: str, domain: str, rem, confidence: float, correct: bool):
    """Emit mycelium trace for collective-learning-value outcomes (fail-open)."""
    from backend.hermetic_rem import MemoryFate
    try:
        import sys, os
        sys.path.insert(0, "/opt/ares/mycelium-gateway/pysrc")
        from mycelium import core as mc
        mc.emit(
            agent="vantage-jev",
            session=signal_id,
            kind="rem-outcome",
            action=rem.fate.value,
            outcome=f"domain={domain} score={rem.overall_score:.3f} correct={correct} conf={confidence:.2f} {rem.reasoning}",
        )
        # For promote/fold-to-hive, also add a finding
        if rem.fate in (MemoryFate.PROMOTE, MemoryFate.FOLD_TO_HIVE):
            mc.add_finding(
                agent="vantage-jev",
                session=signal_id,
                finding=f"[{rem.fate.value}] {domain}: conf={confidence:.2f} correct={correct} → {rem.reasoning}",
                severity="info",
            )
    except Exception:
        pass


async def compute_rolling_brier() -> float:
    """Compute rolling Brier score over last BRIER_WINDOW scored predictions."""
    async with get_db() as db:
        try:
            rows = await (await db.execute(
                """SELECT verdict, confidence, outcome FROM brier_predictions
                   WHERE scored=1 AND outcome IS NOT NULL
                   ORDER BY predicted_ts DESC LIMIT ?""",
                (BRIER_WINDOW,)
            )).fetchall()
        except Exception:
            return 0.0
    if not rows:
        return 0.0
    preds = [r[1] if r[0] == "yes" else (1.0 - r[1]) for r in rows]
    outcomes = [1.0 if r[2] == "yes" else 0.0 for r in rows]
    result = await _julia_brier(predictions=preds, outcomes=outcomes)
    return result.get("brier_score", 0.0)

async def should_gate() -> tuple:
    """Returns (gated: bool, brier_score: float). If gated, do not emit signals."""
    score = await compute_rolling_brier()
    return score > BRIER_THRESHOLD, score


async def gate_status() -> dict:
    """Returns {gated: bool, brier_score: float} dict. Alias for should_gate() for daemon compatibility."""
    gated, score = await should_gate()
    return {"gated": gated, "brier_score": score}
