"""A2A Task Delegation router — agents can delegate guild tasks to other agents.

The delegating agent remains accountable in the receipt chain; the work is
done by the delegate.

Endpoints:
  POST /api/guilds/{guild_slug}/tasks/{task_id}/delegate
  GET  /api/agents/me/delegations
  POST /api/delegations/{delegation_id}/accept
  POST /api/delegations/{delegation_id}/reject
  POST /api/delegations/{delegation_id}/complete
"""
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent
from ..event_bus import VantageEvent, emit

router = APIRouter(prefix="/api", tags=["delegation"])


# ── Request bodies ────────────────────────────────────────────────────────────

class DelegateBody(BaseModel):
    to_agent_id: int
    instructions: str = ""
    deadline: Optional[str] = None


class RejectBody(BaseModel):
    reason: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_task(task_id: str, guild_slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_slug=?",
            (task_id, guild_slug),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Task not found in this guild")
    return dict(row)


async def _get_delegation(delegation_id: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_task_delegations WHERE id=?", (delegation_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Delegation not found")
    return dict(row)


async def _get_agent_row(agent_id: int) -> dict:
    """Fetch a minimal agent record (id, name) by id."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name FROM agents WHERE id=?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return dict(row)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/guilds/{guild_slug}/tasks/{task_id}/delegate",
    summary="Delegate a guild task to another agent",
    description=(
        "The task must be in 'claimed' status and the caller must be the claimer. "
        "Creates a delegation record and emits a DelegationCreated event."
    ),
)
async def delegate_task(
    guild_slug: str,
    task_id: str,
    body: DelegateBody,
    agent: dict = Depends(get_agent),
):
    task = await _get_task(task_id, guild_slug)

    if task["status"] != "claimed":
        raise HTTPException(
            400, f"Task must be in 'claimed' status to delegate (current: {task['status']})"
        )
    if task.get("claimed_by_id") != agent["id"]:
        raise HTTPException(403, "Only the task claimer can delegate it")

    # Resolve the target agent's name
    to_agent = await _get_agent_row(body.to_agent_id)

    delegation_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_task_delegations
               (id, task_id, guild_slug, from_agent_id, from_agent_name,
                to_agent_id, to_agent_name, instructions, deadline, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                delegation_id,
                task_id,
                guild_slug,
                agent["id"],
                agent["name"],
                body.to_agent_id,
                to_agent["name"],
                body.instructions,
                body.deadline,
            ),
        )
        await db.commit()

    delegation = await _get_delegation(delegation_id)

    await emit(VantageEvent(
        event_type="DelegationCreated",
        actor_id=agent["id"],
        actor_name=agent["name"],
        aggregate_id=delegation_id,
        aggregate_type="delegation",
        payload={
            "delegation_id": delegation_id,
            "task_id": task_id,
            "guild_slug": guild_slug,
            "from_agent_id": agent["id"],
            "from_agent_name": agent["name"],
            "to_agent_id": body.to_agent_id,
            "to_agent_name": to_agent["name"],
        },
    ))

    return delegation


@router.get(
    "/agents/me/delegations",
    summary="Get delegations sent and received by the current agent",
)
async def get_my_delegations(
    status: Optional[str] = Query(None, description="Filter by status: pending|accepted|rejected|completed|expired"),
    agent: dict = Depends(get_agent),
):
    agent_id = agent["id"]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        sent_query = "SELECT * FROM guild_task_delegations WHERE from_agent_id=?"
        recv_query = "SELECT * FROM guild_task_delegations WHERE to_agent_id=?"
        params: list = [agent_id]

        if status:
            sent_query += " AND status=?"
            recv_query += " AND status=?"
            params.append(status)

        sent_query += " ORDER BY created_at DESC"
        recv_query += " ORDER BY created_at DESC"

        async with db.execute(sent_query, params) as cur:
            sent = [dict(row) for row in await cur.fetchall()]

        async with db.execute(recv_query, params) as cur:
            received = [dict(row) for row in await cur.fetchall()]

    return {"sent": sent, "received": received}


@router.post(
    "/delegations/{delegation_id}/accept",
    summary="Accept a delegation",
    description="The accepting agent must be the to_agent_id. Updates task status to 'executing'.",
)
async def accept_delegation(
    delegation_id: str,
    agent: dict = Depends(get_agent),
):
    delegation = await _get_delegation(delegation_id)

    if delegation["to_agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the target agent can accept this delegation")
    if delegation["status"] != "pending":
        raise HTTPException(400, f"Delegation is not pending (current: {delegation['status']})")

    async with get_db() as db:
        await db.execute(
            """UPDATE guild_task_delegations
               SET status='accepted', accepted_at=datetime('now')
               WHERE id=?""",
            (delegation_id,),
        )
        # Update task status to 'executing' if not already
        await db.execute(
            """UPDATE guild_tasks SET status='executing', updated_at=datetime('now')
               WHERE id=? AND status NOT IN ('executing', 'completed', 'accepted', 'rejected')""",
            (delegation["task_id"],),
        )
        await db.commit()

    return await _get_delegation(delegation_id)


@router.post(
    "/delegations/{delegation_id}/reject",
    summary="Reject a delegation",
)
async def reject_delegation(
    delegation_id: str,
    body: RejectBody,
    agent: dict = Depends(get_agent),
):
    delegation = await _get_delegation(delegation_id)

    if delegation["to_agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the target agent can reject this delegation")
    if delegation["status"] != "pending":
        raise HTTPException(400, f"Delegation is not pending (current: {delegation['status']})")

    async with get_db() as db:
        await db.execute(
            """UPDATE guild_task_delegations
               SET status='rejected', reject_reason=?
               WHERE id=?""",
            (body.reason, delegation_id),
        )
        await db.commit()

    return await _get_delegation(delegation_id)


@router.post(
    "/delegations/{delegation_id}/complete",
    summary="Mark a delegation as completed",
    description="Marks the delegation completed and emits a DelegationCompleted event.",
)
async def complete_delegation(
    delegation_id: str,
    agent: dict = Depends(get_agent),
):
    delegation = await _get_delegation(delegation_id)

    if delegation["to_agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the delegate agent can complete this delegation")
    if delegation["status"] not in ("accepted", "pending"):
        raise HTTPException(400, f"Cannot complete delegation in status '{delegation['status']}'")

    async with get_db() as db:
        await db.execute(
            """UPDATE guild_task_delegations
               SET status='completed', completed_at=datetime('now')
               WHERE id=?""",
            (delegation_id,),
        )
        await db.commit()

    updated = await _get_delegation(delegation_id)

    await emit(VantageEvent(
        event_type="DelegationCompleted",
        actor_id=agent["id"],
        actor_name=agent["name"],
        aggregate_id=delegation_id,
        aggregate_type="delegation",
        payload={
            "delegation_id": delegation_id,
            "task_id": delegation["task_id"],
            "guild_slug": delegation["guild_slug"],
            "from_agent_id": delegation["from_agent_id"],
            "from_agent_name": delegation["from_agent_name"],
            "to_agent_id": agent["id"],
            "to_agent_name": agent["name"],
        },
    ))

    return updated
