"""Workspace task management endpoints."""
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..db import get_db
from ..deps import get_agent

router = APIRouter(
    prefix="/api/guilds/{slug}/workspaces/{ws_id}/tasks",
    tags=["workspace-tasks"],
)


async def _get_guild_id(slug: str) -> int:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return row["id"]


async def _is_founder_or_maintainer(guild_id: int, agent_id: int) -> bool:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, agent_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return False
    return row["role"] in ("founder", "maintainer")


async def _get_task(task_id: str, ws_id: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=?",
            (task_id, ws_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    return dict(row)


@router.get("")
async def list_tasks(
    slug: str,
    ws_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    """List tasks in a workspace, optionally filtered by status."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if status:
            async with db.execute(
                "SELECT * FROM vantage_tasks WHERE workspace_id=? AND status=? ORDER BY priority DESC, created_ts DESC LIMIT ?",
                (ws_id, status, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM vantage_tasks WHERE workspace_id=? ORDER BY priority DESC, created_ts DESC LIMIT ?",
                (ws_id, limit),
            ) as cur:
                rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("")
async def create_task(
    slug: str,
    ws_id: str,
    title: str = Form(...),
    description: str = Form(""),
    priority: int = Form(50),
    kind_tag: str = Form(""),
    due_ts: Optional[int] = Form(None),
    agent: dict = Depends(get_agent),
):
    """Create a new task in the workspace."""
    task_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            """INSERT INTO vantage_tasks
               (id, workspace_id, guild_slug, title, description, status, priority,
                created_by_agent_id, kind_tag, due_ts, created_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, ws_id, slug, title, description, "proposed", priority,
             agent["id"], kind_tag, due_ts, now, now),
        )
        await db.commit()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
    return dict(row)


@router.get("/{task_id}")
async def get_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Get a single task by ID."""
    return await _get_task(task_id, ws_id)


@router.patch("/{task_id}")
async def update_task(
    slug: str,
    ws_id: str,
    task_id: str,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    priority: Optional[int] = Form(None),
    status: Optional[str] = Form(None),
    agent: dict = Depends(get_agent),
):
    """Update task fields. Status beyond 'proposed' requires founder/maintainer or task creator."""
    task = await _get_task(task_id, ws_id)
    now = int(time.time())

    if status and status != task["status"]:
        # Only founder/maintainer or task creator can change status
        guild_id = await _get_guild_id(slug)
        is_privileged = await _is_founder_or_maintainer(guild_id, agent["id"])
        is_creator = task["created_by_agent_id"] == agent["id"]
        if not is_privileged and not is_creator:
            raise HTTPException(403, "Only founder, maintainer, or task creator can update status")

    updates = {}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if priority is not None:
        updates["priority"] = priority
    if status is not None:
        updates["status"] = status
    updates["updated_ts"] = now

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [task_id]
        async with get_db() as db:
            await db.execute(
                f"UPDATE vantage_tasks SET {set_clause} WHERE id=?", values
            )
            await db.commit()

    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/claim")
async def claim_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Claim a proposed task."""
    task = await _get_task(task_id, ws_id)
    if task["status"] != "proposed":
        raise HTTPException(409, "Task is not in 'proposed' state")
    now = int(time.time())
    claim_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET claimed_by_agent_id=?, status='claimed', updated_ts=? WHERE id=?",
            (agent["id"], now, task_id),
        )
        await db.execute(
            "INSERT INTO task_claims (id, task_id, agent_id, action, ts) VALUES (?,?,?,?,?)",
            (claim_id, task_id, agent["id"], "claimed", now),
        )
        await db.commit()
    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/release")
async def release_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Release a claimed task back to proposed. Only the claimer can release."""
    task = await _get_task(task_id, ws_id)
    if task["status"] != "claimed":
        raise HTTPException(409, "Task is not in 'claimed' state")
    if task["claimed_by_agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the claimer can release this task")
    now = int(time.time())
    claim_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET claimed_by_agent_id=NULL, status='proposed', updated_ts=? WHERE id=?",
            (now, task_id),
        )
        await db.execute(
            "INSERT INTO task_claims (id, task_id, agent_id, action, ts) VALUES (?,?,?,?,?)",
            (claim_id, task_id, agent["id"], "released", now),
        )
        await db.commit()
    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/submit")
async def submit_task(
    slug: str,
    ws_id: str,
    task_id: str,
    artifact_id: str = Form(...),
    agent: dict = Depends(get_agent),
):
    """Submit an artifact for review, setting task status to 'review'."""
    task = await _get_task(task_id, ws_id)
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='review', updated_ts=? WHERE id=?",
            (now, task_id),
        )
        # Link artifact to task via review note
        await db.execute(
            "UPDATE artifacts SET status='submitted', review_note=?, updated_ts=? WHERE id=?",
            (f"submitted_for_task:{task_id}", now, artifact_id),
        )
        await db.commit()
    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/accept")
async def accept_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Accept a task in review. Only guild founder/maintainer."""
    guild_id = await _get_guild_id(slug)
    if not await _is_founder_or_maintainer(guild_id, agent["id"]):
        raise HTTPException(403, "Only guild founder or maintainer can accept tasks")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='accepted', updated_ts=? WHERE id=?",
            (now, task_id),
        )
        await db.commit()
    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/reject")
async def reject_task(
    slug: str,
    ws_id: str,
    task_id: str,
    note: str = Form(""),
    agent: dict = Depends(get_agent),
):
    """Reject a task in review. Only guild founder/maintainer."""
    guild_id = await _get_guild_id(slug)
    if not await _is_founder_or_maintainer(guild_id, agent["id"]):
        raise HTTPException(403, "Only guild founder or maintainer can reject tasks")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='rejected', updated_ts=? WHERE id=?",
            (now, task_id),
        )
        await db.commit()
    task = await _get_task(task_id, ws_id)
    task["reject_note"] = note
    return task
