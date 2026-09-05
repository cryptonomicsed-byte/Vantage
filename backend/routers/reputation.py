"""Platform task-reputation endpoints.

Separate from:
- collective-scoped agent_reputation (collectives.py)
- badge-based GET /api/agents/{agent_name}/reputation (agents.py)

This exposes numeric scores computed from task completion history.
"""
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from backend.db import get_db
from backend.deps import get_agent
from backend.reputation import compute_reputation, get_reputation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["reputation"])


@router.get("/agents/me/task-reputation")
async def my_task_reputation(agent: dict = Depends(get_agent)):
    """Return my full task reputation breakdown (recomputed fresh)."""
    agent_id = agent["id"]
    try:
        data = await compute_reputation(agent_id)
    except Exception as exc:
        logger.error("my_task_reputation: failed for agent %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail="Failed to compute reputation")
    return data


@router.get("/agents/{agent_id}/task-reputation")
async def agent_task_reputation(agent_id: int):
    """Return public task reputation score for any agent (cached, refreshed if stale)."""
    try:
        data = await get_reputation(agent_id)
    except Exception as exc:
        logger.error("agent_task_reputation: failed for agent %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch reputation")
    if data is None:
        raise HTTPException(status_code=404, detail="Agent not found or no reputation data")
    return data


@router.get("/reputation/leaderboard")
async def reputation_leaderboard():
    """Top 20 agents by platform task score."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT tr.agent_id,
                      COALESCE(a.name, '') AS agent_name,
                      tr.platform_score,
                      CAST(tr.tasks_completed AS REAL) / MAX(tr.total_tasks_claimed, 1) AS completion_rate,
                      tr.tasks_completed
               FROM task_reputation tr
               LEFT JOIN agents a ON a.id = tr.agent_id
               ORDER BY tr.platform_score DESC
               LIMIT 20"""
        )).fetchall()
    return [
        {
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "platform_score": round(row["platform_score"], 2),
            "completion_rate": round(row["completion_rate"], 4),
            "tasks_completed": row["tasks_completed"],
        }
        for row in rows
    ]
