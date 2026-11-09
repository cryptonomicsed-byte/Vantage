"""P3-06: bondhive reliability scoring for guild agents.

A faithful Python port of bondhive ``core/src/score.rs`` (BondScore), applied to
guild agents inside a trading space. Pure math lives in ``compute_bond_score``
and must stay bit-for-bit equivalent to score.rs; the golden vectors in
``backend/tests/test_guild_reliability.py`` were produced by compiling score.rs
itself and pin that equivalence.

Formula (score.rs is authoritative):
- start at 50 (neutral)
- +1 per success, -2 per failure, -5 per fraud
- between consecutive events, and from the last event to ``now``, the score
  mean-reverts toward 50 exponentially with a 180-day half-life
- clamped to [0, 100] ONCE, on the final result (intermediate values may leave
  the range; e.g. 12 frauds then 1 success is still 0, not 1)
- an event at ``at == 0`` is treated as "no previous event" for reversion, and
  out-of-order events (``at`` earlier than the previous one) skip reversion —
  both quirks are copied from score.rs on purpose.

Storage (additive, created lazily):
- guild_agent_outcomes: append-only resolved outcomes per (guild, space, agent)
- guild_agent_scores: cached score per (guild, space, agent), refreshed whenever
  outcomes are recorded or reliability is read. The cache is derived data; the
  outcomes table is the source of truth.

No background behaviour: nothing runs unless an outcome is recorded or the
reliability endpoint is read.
"""
from __future__ import annotations

import math
import time
from typing import Iterable, Optional, Sequence, Tuple

# ── score.rs port ──────────────────────────────────────────────────────────────

REPUTATION_HALF_LIFE_SECS: float = 180.0 * 86_400.0
NEUTRAL_SCORE: float = 50.0

OUTCOME_DELTAS = {"success": 1.0, "failure": -2.0, "fraud": -5.0}
OUTCOMES = tuple(OUTCOME_DELTAS)


def outcome_delta(outcome: str) -> float:
    try:
        return OUTCOME_DELTAS[outcome]
    except KeyError:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")


def _revert(score: float, dt_secs: int) -> float:
    k = (float(dt_secs) / REPUTATION_HALF_LIFE_SECS) * math.log(2.0)
    return NEUTRAL_SCORE + (score - NEUTRAL_SCORE) * math.exp(-k)


def compute_bond_score(history: Sequence[Tuple[str, int]], now: int) -> float:
    """Port of ``score::compute_bond_score``. ``history`` is ``[(outcome, at), ...]``
    in the order events should be applied (chronological)."""
    score = NEUTRAL_SCORE
    last_ts = 0
    for outcome, at in history:
        at = int(at)
        if last_ts != 0 and at > last_ts:
            score = _revert(score, at - last_ts)
        score += outcome_delta(outcome)
        last_ts = at
    now = int(now)
    if last_ts != 0 and now > last_ts:
        score = _revert(score, now - last_ts)
    return min(max(score, 0.0), 100.0)


def premium_discount(score: float) -> float:
    """Port of ``score::premium_discount`` (informational for guild agents)."""
    if score >= 80.0:
        return 0.50
    if score >= 70.0:
        return 0.25
    if score >= 60.0:
        return 0.10
    return 0.0


def effective_premium(base_premium: int, score: float) -> int:
    """Port of ``score::effective_premium`` (Rust ``f64::round`` = half away from zero)."""
    discounted = float(base_premium) * (1.0 - premium_discount(score))
    return int(math.floor(discounted + 0.5)) if discounted >= 0 else -int(math.floor(-discounted + 0.5))


def outcome_counts(history: Iterable[Tuple[str, int]]) -> dict:
    c = {"success": 0, "failure": 0, "fraud": 0}
    for outcome, _ in history:
        c[outcome] += 1
    c["total"] = c["success"] + c["failure"] + c["fraud"]
    return c


def outcome_from_verdict(attestation_verdict: str, arbitration: Optional[str]) -> Optional[str]:
    """Port of ``score::outcome_from_verdict``."""
    if arbitration == "uphold":
        return "failure"
    if arbitration == "reject":
        return "success"
    if arbitration == "split":
        return "failure"
    if attestation_verdict == "pass":
        return "success"
    if attestation_verdict == "fail":
        return "failure"
    return None


# ── storage ────────────────────────────────────────────────────────────────────

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS guild_agent_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_slug TEXT NOT NULL,
        space_id TEXT NOT NULL,
        agent_id INTEGER NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ('success','failure','fraud')),
        at INTEGER NOT NULL,
        ref TEXT,
        note TEXT,
        recorded_by_agent_id INTEGER,
        created_ts INTEGER NOT NULL DEFAULT (unixepoch())
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_guild_agent_outcomes_space_agent "
    "ON guild_agent_outcomes(guild_slug, space_id, agent_id, at, id)",
    """
    CREATE TABLE IF NOT EXISTS guild_agent_scores (
        guild_slug TEXT NOT NULL,
        space_id TEXT NOT NULL,
        agent_id INTEGER NOT NULL,
        score REAL NOT NULL,
        success INTEGER NOT NULL DEFAULT 0,
        failure INTEGER NOT NULL DEFAULT 0,
        fraud INTEGER NOT NULL DEFAULT 0,
        last_outcome_at INTEGER,
        computed_at INTEGER NOT NULL,
        PRIMARY KEY (guild_slug, space_id, agent_id)
    )
    """,
)


async def ensure_tables(db) -> None:
    for stmt in _SCHEMA:
        await db.execute(stmt)


async def _history(db, slug: str, space_id: str, agent_id: int) -> list:
    async with db.execute(
        "SELECT outcome, at FROM guild_agent_outcomes "
        "WHERE guild_slug=? AND space_id=? AND agent_id=? ORDER BY at ASC, id ASC",
        (slug, space_id, agent_id),
    ) as cur:
        return [(r[0], int(r[1])) for r in await cur.fetchall()]


async def refresh_score(db, slug: str, space_id: str, agent_id: int, now: Optional[int] = None) -> dict:
    """Recompute one agent's score from its outcomes and upsert the cache row."""
    now = int(time.time()) if now is None else int(now)
    hist = await _history(db, slug, space_id, agent_id)
    score = compute_bond_score(hist, now)
    counts = outcome_counts(hist)
    last_at = hist[-1][1] if hist else None
    await db.execute(
        "INSERT INTO guild_agent_scores (guild_slug, space_id, agent_id, score, success, failure, fraud, "
        "last_outcome_at, computed_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_slug, space_id, agent_id) DO UPDATE SET score=excluded.score, "
        "success=excluded.success, failure=excluded.failure, fraud=excluded.fraud, "
        "last_outcome_at=excluded.last_outcome_at, computed_at=excluded.computed_at",
        (slug, space_id, agent_id, score, counts["success"], counts["failure"], counts["fraud"], last_at, now),
    )
    return {
        "agent_id": agent_id,
        "score": score,
        "premium_discount": premium_discount(score),
        "outcomes": counts,
        "last_outcome_at": last_at,
        "computed_at": now,
    }


async def record_outcome(
    db,
    slug: str,
    space_id: str,
    agent_id: int,
    outcome: str,
    at: Optional[int] = None,
    ref: Optional[str] = None,
    note: Optional[str] = None,
    recorded_by_agent_id: Optional[int] = None,
) -> dict:
    """Append one resolved outcome and refresh the cached score. Caller commits."""
    outcome_delta(outcome)  # validates
    at = int(time.time()) if at is None else int(at)
    await ensure_tables(db)
    cur = await db.execute(
        "INSERT INTO guild_agent_outcomes (guild_slug, space_id, agent_id, outcome, at, ref, note, "
        "recorded_by_agent_id) VALUES (?,?,?,?,?,?,?,?)",
        (slug, space_id, agent_id, outcome, at, ref, note, recorded_by_agent_id),
    )
    row = await refresh_score(db, slug, space_id, agent_id)
    row["outcome_id"] = cur.lastrowid
    return row


async def space_reliability(db, slug: str, space_id: str, agent_id: Optional[int] = None,
                            now: Optional[int] = None) -> list:
    """Scores for every agent with outcomes in the space (or just ``agent_id``)."""
    await ensure_tables(db)
    if agent_id is not None:
        ids = [int(agent_id)]
    else:
        async with db.execute(
            "SELECT DISTINCT agent_id FROM guild_agent_outcomes WHERE guild_slug=? AND space_id=? ORDER BY agent_id",
            (slug, space_id),
        ) as cur:
            ids = [int(r[0]) for r in await cur.fetchall()]
    rows = [await refresh_score(db, slug, space_id, i, now) for i in ids]
    rows.sort(key=lambda r: (-r["score"], r["agent_id"]))
    return rows
