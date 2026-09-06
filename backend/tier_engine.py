"""Agent tier computation engine.

Tiers 0-4 gate access to BlockMesh features:
  0 — default, new agents
  1 — task_reputation >= 10
  2 — rep >= 50, mesh_commitments >= 5
  3 — rep >= 200, commitments >= 20, witness_approvals >= 3
  4 — rep >= 500, commitments >= 50, witness_approvals >= 10
"""
import asyncio
import logging

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

TIER_THRESHOLDS = [
    # (min_rep, min_commitments, min_witness_approvals) → tier
    (500, 50, 10),  # tier 4
    (200, 20, 3),   # tier 3
    (50,  5,  0),   # tier 2
    (10,  0,  0),   # tier 1
]

RECOMPUTE_INTERVAL = 300  # seconds


async def init_tier_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_tiers (
                agent_id      INTEGER PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
                tier          INTEGER NOT NULL DEFAULT 0,
                task_reputation    INTEGER NOT NULL DEFAULT 0,
                mesh_commitments   INTEGER NOT NULL DEFAULT 0,
                witness_approvals  INTEGER NOT NULL DEFAULT 0,
                updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
            )
        """)
        await db.commit()


def compute_tier(rep: int, commitments: int, approvals: int) -> int:
    for i, (r, c, a) in enumerate(TIER_THRESHOLDS):
        if rep >= r and commitments >= c and approvals >= a:
            return 4 - i
    return 0


async def get_tier(agent_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM agent_tiers WHERE agent_id=?", (agent_id,)
        )
        row = await cur.fetchone()
    if row:
        return dict(row)
    return {"agent_id": agent_id, "tier": 0, "task_reputation": 0,
            "mesh_commitments": 0, "witness_approvals": 0}


async def increment_reputation(agent_id: int, delta: int = 1) -> None:
    async with get_db() as db:
        await db.execute("""
            INSERT INTO agent_tiers (agent_id, task_reputation, updated_at)
            VALUES (?, ?, unixepoch())
            ON CONFLICT(agent_id) DO UPDATE SET
                task_reputation = task_reputation + excluded.task_reputation,
                updated_at = unixepoch()
        """, (agent_id, delta))
        # recompute tier inline
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT task_reputation, mesh_commitments, witness_approvals FROM agent_tiers WHERE agent_id=?",
            (agent_id,)
        )
        row = await cur.fetchone()
        if row:
            t = compute_tier(row["task_reputation"], row["mesh_commitments"], row["witness_approvals"])
            await db.execute("UPDATE agent_tiers SET tier=? WHERE agent_id=?", (t, agent_id))
        await db.commit()


async def increment_commitments(agent_id: int, delta: int = 1) -> None:
    async with get_db() as db:
        await db.execute("""
            INSERT INTO agent_tiers (agent_id, mesh_commitments, updated_at)
            VALUES (?, ?, unixepoch())
            ON CONFLICT(agent_id) DO UPDATE SET
                mesh_commitments = mesh_commitments + excluded.mesh_commitments,
                updated_at = unixepoch()
        """, (agent_id, delta))
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT task_reputation, mesh_commitments, witness_approvals FROM agent_tiers WHERE agent_id=?",
            (agent_id,)
        )
        row = await cur.fetchone()
        if row:
            t = compute_tier(row["task_reputation"], row["mesh_commitments"], row["witness_approvals"])
            await db.execute("UPDATE agent_tiers SET tier=? WHERE agent_id=?", (t, agent_id))
        await db.commit()


async def increment_witness_approvals(agent_id: int, delta: int = 1) -> None:
    async with get_db() as db:
        await db.execute("""
            INSERT INTO agent_tiers (agent_id, witness_approvals, updated_at)
            VALUES (?, ?, unixepoch())
            ON CONFLICT(agent_id) DO UPDATE SET
                witness_approvals = witness_approvals + excluded.witness_approvals,
                updated_at = unixepoch()
        """, (agent_id, delta))
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT task_reputation, mesh_commitments, witness_approvals FROM agent_tiers WHERE agent_id=?",
            (agent_id,)
        )
        row = await cur.fetchone()
        if row:
            t = compute_tier(row["task_reputation"], row["mesh_commitments"], row["witness_approvals"])
            await db.execute("UPDATE agent_tiers SET tier=? WHERE agent_id=?", (t, agent_id))
        await db.commit()


async def bulk_recompute_tiers() -> int:
    """Recompute tier for every agent. Returns count updated."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT agent_id, task_reputation, mesh_commitments, witness_approvals FROM agent_tiers"
        )
        rows = await cur.fetchall()
        count = 0
        for row in rows:
            t = compute_tier(row["task_reputation"], row["mesh_commitments"], row["witness_approvals"])
            await db.execute("UPDATE agent_tiers SET tier=?, updated_at=unixepoch() WHERE agent_id=?",
                             (t, row["agent_id"]))
            count += 1
        await db.commit()
    return count


async def run_tier_recompute_loop() -> None:
    """Background task: periodic bulk tier recompute."""
    while True:
        await asyncio.sleep(RECOMPUTE_INTERVAL)
        try:
            n = await bulk_recompute_tiers()
            logger.debug("tier_engine: recomputed %d tiers", n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("tier_engine: bulk recompute failed: %s", exc)
