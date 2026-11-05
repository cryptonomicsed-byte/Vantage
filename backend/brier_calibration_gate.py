"""Brier score calibration gate: rolling prediction accuracy, and the gate it drives.

Every decision is recorded, including abstentions. A "skip" is a deliberate
non-prediction: it is stored with abstained=1, it is resolved against the real
market outcome later (so a skipping strategy can be audited rather than
guessed at), and it is EXCLUDED from the Brier score, because scoring a call
that was never made inverts the meaning of the number.

Why the distinction matters here: before this file recorded abstentions, only
verdicts of "yes"/"no" at confidence >= the caller's 0.65 threshold were
written -- and the live decision path (trading_decision.jev_decide ->
system_one.decide) returns "skip" whenever should_trade is false, which is the
default. brier_predictions stayed empty, compute_rolling_brier() returned 0.0
on the empty set, and 0.0 reads as perfect calibration. The gate logged
Brier=0.000 forever and could never close.

Outcomes come from backend.prediction_markets (Polymarket gamma, Limitless),
which returns "yes" | "no" | "unresolved" | "void" | None. Only "yes"/"no"
score. "void" is stored and excluded. None -- venue unreachable, unknown
venue, unparsed payload -- leaves the row pending: a missing answer is not a
settlement, and inventing one here would silently move the gate.
"""
from __future__ import annotations

import time
from typing import Optional

try:  # package import
    from .db import get_db
    from .memory_engine import brier_score as _julia_brier
except ImportError:  # backend/ itself on sys.path
    from db import get_db
    from memory_engine import brier_score as _julia_brier

BRIER_WINDOW = 100  # last N scored predictions
BRIER_THRESHOLD = 0.35  # gate closes if rolling Brier > threshold (0=perfect, 1=worst)

SCORED_OUTCOMES = ("yes", "no")
TERMINAL_OUTCOMES = ("yes", "no", "void")

# Venues backend.prediction_markets can read a settlement from. A source not
# listed here (kalshi today) is never selected for resolution: no reader
# exists for it, and a missing reader must not be mistaken for a market that
# has not settled -- that mistake parks the whole queue head forever.
RESOLVABLE_VENUES = ("polymarket", "limitless")


async def record_prediction(
    signal_id: str,
    verdict: str,
    confidence: float,
    *,
    direction: Optional[str] = None,
    conviction: Optional[str] = None,
    should_trade: Optional[bool] = None,
    market_id: Optional[str] = None,
    reasoning: Optional[str] = None,
    abstained: Optional[int] = None,
) -> None:
    """Record one decision, whatever it was.

    First decision wins (INSERT OR IGNORE on the signal_id primary key): a
    re-decision of the same signal is a different experiment, and overwriting
    the stored row would quietly rescore history against whichever verdict was
    produced last.
    """
    if abstained is None:
        abstained = 0 if verdict in SCORED_OUTCOMES else 1
    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO brier_predictions
               (signal_id, verdict, confidence, predicted_ts, outcome, scored,
                abstained, direction, conviction, should_trade, market_id, reasoning)
               VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                verdict,
                float(confidence),
                int(time.time()),
                int(bool(abstained)),
                direction,
                conviction,
                None if should_trade is None else float(bool(should_trade)),
                market_id,
                reasoning,
            ),
        )
        await db.commit()


async def record_outcome(
    signal_id: str,
    outcome: str,
    confidence: float = 0.5,
    domain: str = "signal",
    source: str = "unknown",
) -> bool:
    """Attach a settlement to a recorded prediction. True if a row was updated.

    `outcome` must be one of TERMINAL_OUTCOMES. Anything else (notably
    "unresolved") is refused rather than stored: marking a still-open market as
    scored would freeze a guess into the calibration history.
    """
    if outcome not in TERMINAL_OUTCOMES:
        return False

    async with get_db() as db:
        cur = await db.execute(
            "UPDATE brier_predictions SET outcome=?, scored=1, resolved_ts=? WHERE signal_id=?",
            (outcome, int(time.time()), signal_id),
        )
        await db.commit()
        updated = cur.rowcount > 0

    if not updated or outcome not in SCORED_OUTCOMES:
        # A void settlement has no correctness to learn from.
        return updated

    # Hermetic REM routing -- fail-open, and deliberately after the write: the
    # calibration record is the durable fact, the trace is the telemetry.
    try:
        from .hermetic_rem import score_memory, MemoryFate
    except ImportError:  # pragma: no cover - backend/ on sys.path
        try:
            from hermetic_rem import score_memory, MemoryFate
        except Exception:
            return updated

    try:
        correct = outcome == "yes"
        novelty = 0.3 if source in ("kalshi", "polymarket") else 0.6
        collective_value = 0.7 if correct else 0.4
        rem = score_memory(
            {
                "confidence": confidence,
                "correct": correct,
                "novelty": novelty,
                "collective_value": collective_value,
                "recency_hours": 0.0,
                "domain": domain,
            }
        )
        _emit_rem_trace(signal_id, domain, rem, confidence, correct)
    except Exception:
        pass
    return updated


def _emit_rem_trace(signal_id, domain, rem, confidence: float, correct: bool) -> None:
    """Emit a mycelium trace for collective-learning-value outcomes (fail-open)."""
    try:
        import sys

        try:
            from .hermetic_rem import MemoryFate
        except ImportError:  # pragma: no cover
            from hermetic_rem import MemoryFate

        sys.path.insert(0, "/opt/ares/mycelium-gateway/pysrc")
        from mycelium import core as mc

        mc.emit(
            agent="vantage-jev",
            session=signal_id,
            kind="rem-outcome",
            action=rem.fate.value,
            outcome=(
                f"domain={domain} score={rem.overall_score:.3f} "
                f"correct={correct} conf={confidence:.2f} {rem.reasoning}"
            ),
        )
        if rem.fate in (MemoryFate.PROMOTE, MemoryFate.FOLD_TO_HIVE):
            mc.add_finding(
                agent="vantage-jev",
                session=signal_id,
                finding=(
                    f"[{rem.fate.value}] {domain}: conf={confidence:.2f} "
                    f"correct={correct} -> {rem.reasoning}"
                ),
                severity="info",
            )
    except Exception:
        pass


async def compute_rolling_brier() -> float:
    """Rolling Brier over the last BRIER_WINDOW *scored directional* predictions.

    Abstentions are filtered out on purpose. So are voids. An empty window
    returns 0.0 for compatibility, but callers that need to distinguish
    "perfect" from "no data" must read brier_stats()['scored'].
    """
    async with get_db() as db:
        try:
            rows = await (
                await db.execute(
                    """SELECT verdict, confidence, outcome FROM brier_predictions
                       WHERE scored = 1
                         AND outcome IN ('yes', 'no')
                         AND COALESCE(abstained, 0) = 0
                       ORDER BY predicted_ts DESC LIMIT ?""",
                    (BRIER_WINDOW,),
                )
            ).fetchall()
        except Exception:
            return 0.0
    if not rows:
        return 0.0
    preds = [r[1] if r[0] == "yes" else (1.0 - r[1]) for r in rows]
    outcomes = [1.0 if r[2] == "yes" else 0.0 for r in rows]
    result = await _julia_brier(predictions=preds, outcomes=outcomes)
    return float(result.get("brier_score", 0.0))


async def brier_stats() -> dict:
    """Counts behind the score, so "0.000" can be told apart from "nothing yet"."""
    async with get_db() as db:

        async def one(sql: str, args: tuple = ()) -> int:
            try:
                row = await (await db.execute(sql, args)).fetchone()
                return int(row[0]) if row else 0
            except Exception:
                return 0

        recorded = await one("SELECT COUNT(*) FROM brier_predictions")
        scored = await one(
            """SELECT COUNT(*) FROM brier_predictions
               WHERE scored=1 AND outcome IN ('yes','no') AND COALESCE(abstained,0)=0"""
        )
        abstained_pending = await one(
            """SELECT COUNT(*) FROM brier_predictions
               WHERE COALESCE(abstained,0)=1 AND scored=0"""
        )
        abstained_resolved = await one(
            """SELECT COUNT(*) FROM brier_predictions
               WHERE COALESCE(abstained,0)=1 AND outcome IN ('yes','no')"""
        )
        pending = await one(
            "SELECT COUNT(*) FROM brier_predictions WHERE scored=0 AND outcome IS NULL"
        )
        void = await one("SELECT COUNT(*) FROM brier_predictions WHERE outcome='void'")

    score = await compute_rolling_brier()
    return {
        "recorded": recorded,
        "scored": scored,
        "pending": pending,
        "abstentions_pending": abstained_pending,
        "abstentions_resolved": abstained_resolved,
        "void": void,
        "brier_score": score,
        "threshold": BRIER_THRESHOLD,
        # A gate over an empty window is vacuous, not open-by-merit.
        "vacuous": scored == 0,
        "gated": scored > 0 and score > BRIER_THRESHOLD,
    }


async def should_gate() -> tuple:
    """Returns (gated: bool, brier_score: float). If gated, do not emit signals.

    The empty-window case returns False, but brier_stats()['vacuous'] is True
    there -- the daemon logs that distinction instead of reporting a clean
    0.000 that was never measured.
    """
    score = await compute_rolling_brier()
    return score > BRIER_THRESHOLD, score


async def gate_status() -> dict:
    """{gated, brier_score, ...counts} -- alias retained for daemon compatibility."""
    stats = await brier_stats()
    return {
        "gated": stats["gated"],
        "brier_score": stats["brier_score"],
        "scored": stats["scored"],
        "recorded": stats["recorded"],
        "vacuous": stats["vacuous"],
    }


async def resolve_pending_outcomes(limit: int = 25) -> dict:
    """Ask the venue for a settlement on pending predictions; record what resolves.

    Bounded per call (default 25) so a tick stays cheap: each candidate is one
    HTTP round-trip to gamma/Limitless. Only markets whose source has a reader
    are selected -- see RESOLVABLE_VENUES.

    Returns counters so a tick is auditable:
        checked / resolved / void / pending / errors
    """
    counters = {"checked": 0, "resolved": 0, "void": 0, "pending": 0, "errors": 0}

    async with get_db() as db:
        rows = await (
            await db.execute(
                """SELECT p.signal_id, p.confidence, p.abstained,
                          COALESCE(p.market_id, s.market_id) AS market_id,
                          LOWER(COALESCE(s.source, '')) AS source
                   FROM brier_predictions p
                   LEFT JOIN prediction_signals s ON s.id = p.signal_id
                   WHERE p.scored = 0
                     AND p.outcome IS NULL
                     AND COALESCE(p.market_id, s.market_id) IS NOT NULL
                     AND LOWER(COALESCE(s.source, '')) IN ('polymarket', 'limitless')
                   ORDER BY p.predicted_ts ASC
                   LIMIT ?""",
                (int(limit),),
            )
        ).fetchall()

    if not rows:
        return counters

    try:
        from . import prediction_markets
    except ImportError:  # pragma: no cover - backend/ on sys.path
        import prediction_markets

    for signal_id, confidence, abstained, market_id, source in rows:
        counters["checked"] += 1
        try:
            outcome = await prediction_markets.resolution(source, market_id)
        except Exception:
            counters["errors"] += 1
            continue
        if outcome in SCORED_OUTCOMES:
            await record_outcome(
                signal_id,
                outcome,
                confidence=float(confidence or 0.5),
                domain="prediction_market",
                source=source,
            )
            counters["resolved"] += 1
        elif outcome == "void":
            await record_outcome(signal_id, "void", confidence=float(confidence or 0.5))
            counters["void"] += 1
        else:
            counters["pending"] += 1

    return counters
