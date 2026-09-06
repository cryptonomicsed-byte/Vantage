"""BlockMesh agent tier endpoints.

Tiers 0-4 gate access to BlockMesh features based on cumulative activity
(task_reputation, mesh_commitments, witness_approvals).
"""
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.db import get_db
from backend.deps import get_agent
from backend.tier_engine import compute_tier, get_tier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mesh"])


@router.get("/agents/me/tier")
async def my_tier(agent: dict = Depends(get_agent)):
    """Return the calling agent's current tier and activity stats."""
    return await get_tier(agent["id"])


@router.get("/mesh/tier-leaderboard")
async def tier_leaderboard(agent: dict = Depends(get_agent)):
    """Top 20 agents ranked by tier (desc) then task_reputation (desc)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT at.agent_id AS id,
                      COALESCE(a.name, '') AS name,
                      at.tier,
                      at.task_reputation AS reputation,
                      at.mesh_commitments,
                      at.witness_approvals
               FROM agent_tiers at
               LEFT JOIN agents a ON a.id = at.agent_id
               ORDER BY at.tier DESC, at.task_reputation DESC
               LIMIT 20"""
        )
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


class TierSeedBody(BaseModel):
    task_reputation: int = 0
    mesh_commitments: int = 0
    witness_approvals: int = 0


@router.post("/agents/me/tier/seed")
async def seed_my_tier(body: TierSeedBody, agent: dict = Depends(get_agent)):
    """Dev/debug: upsert tier row with explicit values and recompute tier.

    Overwrites the calling agent's current stats entirely — useful for
    local testing of tier-gated features without waiting for organic
    reputation to accumulate.
    """
    agent_id = agent["id"]
    tier = compute_tier(body.task_reputation, body.mesh_commitments, body.witness_approvals)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_tiers
                   (agent_id, tier, task_reputation, mesh_commitments, witness_approvals, updated_at)
               VALUES (?, ?, ?, ?, ?, unixepoch())
               ON CONFLICT(agent_id) DO UPDATE SET
                   tier               = excluded.tier,
                   task_reputation    = excluded.task_reputation,
                   mesh_commitments   = excluded.mesh_commitments,
                   witness_approvals  = excluded.witness_approvals,
                   updated_at         = unixepoch()""",
            (agent_id, tier, body.task_reputation, body.mesh_commitments, body.witness_approvals),
        )
        await db.commit()
    return {
        "agent_id": agent_id,
        "tier": tier,
        "task_reputation": body.task_reputation,
        "mesh_commitments": body.mesh_commitments,
        "witness_approvals": body.witness_approvals,
    }
