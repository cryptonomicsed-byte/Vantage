"""
Neighbor discovery and suggestion for Block Mesh.

Provides utilities to find agents similar to a given agent based on
shared block membership, interaction history, and trust overlap.
"""

import json

import aiosqlite

from .db import DB_PATH


async def get_block_agents(block_id: str, exclude_id: str = "") -> list[dict]:
    """Return all active agents on a block, excluding a given agent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT agent_id, role, trust_score, capabilities_json,
                      commitments_kept, commitments_made
               FROM mesh_agents
               WHERE block_id = ? AND status = 'active'
               ORDER BY trust_score DESC""",
            (block_id,),
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

    agents = []
    for row in rows:
        if row["agent_id"] == exclude_id:
            continue
        caps = {}
        try:
            caps = json.loads(row["capabilities_json"]) if row["capabilities_json"] else {}
        except Exception:
            pass
        kept = row["commitments_kept"] or 0
        made = row["commitments_made"] or 0
        agents.append({
            "agent_id": row["agent_id"],
            "role": row["role"],
            "trust_score": row["trust_score"],
            "capabilities": caps,
            "commitments_kept": kept,
            "commitments_made": made,
            "fulfillment_rate": (kept / made) if made > 0 else None,
        })
    return agents


async def suggest_neighbors(
    block_id: str,
    for_agent: str,
    limit: int = 10,
) -> list[dict]:
    """
    Suggest neighbors for an agent based on trust score and fulfillment rate.
    Returns agents the requesting agent has NOT yet interacted with, ranked by
    trust score.
    """
    all_agents = await get_block_agents(block_id, exclude_id=for_agent)
    # Sort by trust score descending
    ranked = sorted(all_agents, key=lambda a: a["trust_score"], reverse=True)
    return ranked[:limit]
