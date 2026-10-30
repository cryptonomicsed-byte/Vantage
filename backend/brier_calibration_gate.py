"""Brier score calibration gate: tracks rolling prediction accuracy, gates signals when degraded."""
import time, json
from backend.db import get_db

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

async def record_outcome(signal_id: str, outcome: str):
    """Record the actual outcome for a prediction (outcome: "yes" or "no")."""
    async with get_db() as db:
        await db.execute(
            "UPDATE brier_predictions SET outcome=?, scored=1 WHERE signal_id=?",
            (outcome, signal_id)
        )
        await db.commit()

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
    total = 0.0
    for r in rows:
        actual = 1.0 if r[2] == "yes" else 0.0
        forecast = r[1] if r[0] == "yes" else (1.0 - r[1])
        total += (forecast - actual) ** 2
    return total / len(rows)

async def should_gate() -> tuple:
    """Returns (gated: bool, brier_score: float). If gated, do not emit signals."""
    score = await compute_rolling_brier()
    return score > BRIER_THRESHOLD, score
