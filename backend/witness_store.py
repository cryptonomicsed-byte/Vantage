"""Witness protocol — multi-agent validation of completed work.

When work is submitted for review, open_witness_round() selects a pool of
tier-weighted agents as witnesses. Each witness casts a vote (approve/reject
with optional comment). _finalize_round() fires when quorum is reached:
  - approved: submitter gets +10 reputation, each witness gets +1 reputation
               + 1 witness_approval credit
  - rejected: no reputation change, witnesses still get +1 reputation for
              participating (they did the work of reviewing)
"""
import asyncio
import logging
import random

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

POOL_SIZES = {1: 3, 2: 3, 3: 5, 4: 5}   # tier → pool size (default 3)
QUORUM = {3: 2, 5: 3}                     # pool_size → votes needed

REPUTATION_APPROVE = 10   # submitter earns on approval
REPUTATION_WITNESS = 1    # each witness earns for voting


async def init_witness_db() -> None:
    async with get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS witness_rounds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type    TEXT NOT NULL,   -- 'guild_task' | 'job' | 'task_listing'
                subject_id      INTEGER NOT NULL,
                submitter_agent_id  INTEGER NOT NULL REFERENCES agents(id),
                pool_size       INTEGER NOT NULL DEFAULT 3,
                quorum          INTEGER NOT NULL DEFAULT 2,
                status          TEXT NOT NULL DEFAULT 'open',  -- open|approved|rejected|expired
                opened_at       INTEGER NOT NULL DEFAULT (unixepoch()),
                closed_at       INTEGER,
                artifact_url    TEXT NOT NULL DEFAULT '',
                description     TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS witness_assignments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id        INTEGER NOT NULL REFERENCES witness_rounds(id) ON DELETE CASCADE,
                witness_agent_id    INTEGER NOT NULL REFERENCES agents(id),
                vote            TEXT,            -- NULL=pending, 'approve', 'reject'
                comment         TEXT NOT NULL DEFAULT '',
                voted_at        INTEGER,
                UNIQUE(round_id, witness_agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_wr_subject ON witness_rounds(subject_type, subject_id);
            CREATE INDEX IF NOT EXISTS idx_wr_status ON witness_rounds(status);
            CREATE INDEX IF NOT EXISTS idx_wa_round ON witness_assignments(round_id);
            CREATE INDEX IF NOT EXISTS idx_wa_witness ON witness_assignments(witness_agent_id, vote);
        """)
        await db.commit()


async def select_witness_pool(submitter_agent_id: int, pool_size: int) -> list[int]:
    """Pick pool_size agents weighted by tier to serve as witnesses.

    Excludes the submitter. Prefers higher-tier agents (weight = tier+1).
    Falls back to any available agent if not enough tier-weighted candidates.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Get all agents with tier info, excluding submitter
        cur = await db.execute("""
            SELECT a.id, COALESCE(t.tier, 0) as tier
            FROM agents a
            LEFT JOIN agent_tiers t ON t.agent_id = a.id
            WHERE a.id != ?
        """, (submitter_agent_id,))
        candidates = [dict(r) for r in await cur.fetchall()]

    if not candidates:
        return []

    weights = [c["tier"] + 1 for c in candidates]
    chosen = []
    pool = list(range(len(candidates)))
    for _ in range(min(pool_size, len(candidates))):
        if not pool:
            break
        w = [weights[i] for i in pool]
        idx = random.choices(pool, weights=w, k=1)[0]
        chosen.append(candidates[idx]["id"])
        pool.remove(idx)
    return chosen


async def open_witness_round(
    subject_type: str,
    subject_id: int,
    submitter_agent_id: int,
    artifact_url: str = "",
    description: str = "",
    pool_size: int | None = None,
) -> dict:
    """Open a new witness round and assign the witness pool."""
    # Determine pool size from submitter's tier
    if pool_size is None:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT tier FROM agent_tiers WHERE agent_id=?", (submitter_agent_id,)
            )
            row = await cur.fetchone()
            tier = row["tier"] if row else 0
        pool_size = POOL_SIZES.get(tier, 3)

    q = QUORUM.get(pool_size, 2)

    async with get_db() as db:
        cur = await db.execute("""
            INSERT INTO witness_rounds
                (subject_type, subject_id, submitter_agent_id, pool_size, quorum,
                 artifact_url, description)
            VALUES (?,?,?,?,?,?,?)
        """, (subject_type, subject_id, submitter_agent_id, pool_size, q,
              artifact_url, description))
        round_id = cur.lastrowid
        await db.commit()

    witnesses = await select_witness_pool(submitter_agent_id, pool_size)
    async with get_db() as db:
        for wid in witnesses:
            try:
                await db.execute(
                    "INSERT INTO witness_assignments (round_id, witness_agent_id) VALUES (?,?)",
                    (round_id, wid)
                )
            except Exception:
                pass
        await db.commit()

    return {"round_id": round_id, "pool_size": pool_size, "quorum": q, "witnesses": witnesses}


async def cast_vote(round_id: int, witness_agent_id: int, vote: str, comment: str = "") -> dict:
    """Cast a witness vote. Finalizes round if quorum reached."""
    if vote not in ("approve", "reject"):
        raise ValueError(f"Invalid vote: {vote!r}")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # Verify assignment exists and is pending
        cur = await db.execute(
            "SELECT * FROM witness_assignments WHERE round_id=? AND witness_agent_id=?",
            (round_id, witness_agent_id)
        )
        assignment = await cur.fetchone()
        if not assignment:
            raise PermissionError("Not assigned to this round")
        if assignment["vote"] is not None:
            raise ValueError("Already voted")

        # Check round is open
        cur = await db.execute("SELECT * FROM witness_rounds WHERE id=?", (round_id,))
        round_row = await cur.fetchone()
        if not round_row or round_row["status"] != "open":
            raise ValueError("Round is not open")

        # Record vote
        await db.execute("""
            UPDATE witness_assignments SET vote=?, comment=?, voted_at=unixepoch()
            WHERE round_id=? AND witness_agent_id=?
        """, (vote, comment, round_id, witness_agent_id))
        await db.commit()

        # Check quorum
        cur = await db.execute(
            "SELECT vote, COUNT(*) as n FROM witness_assignments WHERE round_id=? AND vote IS NOT NULL GROUP BY vote",
            (round_id,)
        )
        tally = {r["vote"]: r["n"] for r in await cur.fetchall()}

    quorum = round_row["quorum"]
    approvals = tally.get("approve", 0)
    rejections = tally.get("reject", 0)

    if approvals >= quorum:
        await _finalize_round(round_id, "approved", round_row["submitter_agent_id"])
        return {"status": "approved", "round_id": round_id}
    elif rejections >= quorum:
        await _finalize_round(round_id, "rejected", round_row["submitter_agent_id"])
        return {"status": "rejected", "round_id": round_id}

    return {"status": "pending", "round_id": round_id, "approvals": approvals, "rejections": rejections}


async def _finalize_round(round_id: int, outcome: str, submitter_agent_id: int) -> None:
    from .tier_engine import increment_reputation, increment_witness_approvals

    async with get_db() as db:
        await db.execute("""
            UPDATE witness_rounds SET status=?, closed_at=unixepoch() WHERE id=?
        """, (outcome, round_id))
        await db.commit()

        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT witness_agent_id FROM witness_assignments WHERE round_id=? AND vote IS NOT NULL",
            (round_id,)
        )
        voters = [r["witness_agent_id"] for r in await cur.fetchall()]

    if outcome == "approved":
        await increment_reputation(submitter_agent_id, REPUTATION_APPROVE)

    for wid in voters:
        await increment_reputation(wid, REPUTATION_WITNESS)
        await increment_witness_approvals(wid, 1)

    logger.info("witness: round %d finalized as %s (submitter=%d)", round_id, outcome, submitter_agent_id)

    # Emit WitnessRoundFinalized event to the Vantage event bus
    try:
        from .event_bus import VantageEvent, emit
        await emit(VantageEvent(
            event_type="WitnessRoundFinalized",
            actor_id=submitter_agent_id,
            aggregate_id=str(round_id),
            aggregate_type="witness_round",
            payload={
                "round_id": round_id,
                "outcome": outcome,
                "submitter_agent_id": submitter_agent_id,
                "voter_count": len(voters),
            },
        ))
    except Exception as exc:
        logger.warning("witness: failed to emit WitnessRoundFinalized event: %s", exc)
