"""Workspace task board: tasks that belong to one workspace channel.

Every route requires membership of the guild (G0). Creating a task needs
``post`` on the workspace, claiming needs ``claim`` (an observer can read
the board but not take work), accepting or rejecting needs ``review`` and
is never done by the claimant (G2). Claims are leases, taken in one atomic
UPDATE, the same rule as every other vantage_tasks route (backend/space_tasks).
"""
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import get_db
from ..deps import get_agent
from ..guild_access import guild_by_slug, require_member, require_not_claimant, require_space_capability
from ..space_tasks import DEFAULT_LEASE_MINUTES, claim_sql, clamped_lease, submit_sql
from ..work_states import envelope
from ..task_events import emit

router = APIRouter(
    prefix="/api/guilds/{slug}/workspaces/{ws_id}/tasks",
    tags=["workspace-tasks"],
)


async def _workspace(slug: str, ws_id: str) -> dict:
    """The workspace channel, by id or slug, shaped like a space so the
    capability resolver can read its roster."""
    guild = await guild_by_slug(slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if ws_id.isdigit():
            cur = await db.execute("SELECT * FROM guild_channels WHERE guild_id=? AND id=?", (guild["id"], int(ws_id)))
        else:
            cur = await db.execute("SELECT * FROM guild_channels WHERE guild_id=? AND slug=?", (guild["id"], ws_id))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Workspace not found")
    ch = dict(row)
    return {"id": ws_id, "guild_slug": slug, "chat_channel_slug": ch["slug"], "channel_id": ch["id"],
            "created_by_agent_id": None, "name": ch["name"]}


async def _member_workspace(slug: str, ws_id: str, agent: dict) -> dict:
    await require_member(slug, agent)
    return await _workspace(slug, ws_id)


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
    await _member_workspace(slug, ws_id, agent)
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
    return envelope("vtask", [dict(r) for r in rows], legacy_key="tasks")


class CreateTaskBody(BaseModel):
    title: str
    description: str = Field("")
    priority: int = Field(50)
    kind_tag: str = Field("")
    due_ts: Optional[int] = Field(None)


@router.post("")
async def create_task(
    body: CreateTaskBody,
    slug: str,
    ws_id: str,
    agent: dict = Depends(get_agent)
):
    """Create a new task in a workspace."""
    ws = await _member_workspace(slug, ws_id, agent)
    await require_space_capability(slug, ws, agent, "post")
    task_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            """INSERT INTO vantage_tasks
               (id, workspace_id, guild_slug, title, description, status, priority,
                created_by_agent_id, kind_tag, due_ts, created_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, ws_id, slug, body.title, body.description, "proposed", body.priority,
             agent["id"], body.kind_tag, body.due_ts, now, now),
        )
        await db.commit()
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="created", kind="vtask",
               ref_id=task_id, actor=agent, title=body.title)
    return await _get_task(task_id, ws_id)


@router.get("/{task_id}")
async def get_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Get a single task by ID."""
    await _member_workspace(slug, ws_id, agent)
    return await _get_task(task_id, ws_id)


class UpdateTaskBody(BaseModel):
    title: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    priority: Optional[int] = Field(None)
    status: Optional[str] = Field(None)


@router.patch("/{task_id}")
async def update_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[UpdateTaskBody] = None,
):
    """Edit a task's fields. The creator or a reviewer may edit; status
    changes go through claim / submit / accept / reject, not here."""
    body = body or UpdateTaskBody()
    ws = await _member_workspace(slug, ws_id, agent)
    task = await _get_task(task_id, ws_id)
    if task.get("created_by_agent_id") != agent["id"]:
        await require_space_capability(slug, ws, agent, "review")
    if body.status is not None:
        raise HTTPException(422, "status is driven by claim, submit, accept and reject")
    now = int(time.time())
    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title
    if body.description is not None:
        updates["description"] = body.description
    if body.priority is not None:
        updates["priority"] = body.priority
    updates["updated_ts"] = now
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [task_id, ws_id]
    async with get_db() as db:
        await db.execute(f"UPDATE vantage_tasks SET {set_clause} WHERE id=? AND workspace_id=?", values)
        await db.commit()
    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/claim")
async def claim_task(
    slug: str,
    ws_id: str,
    task_id: str,
    lease_minutes: int = Query(DEFAULT_LEASE_MINUTES),
    agent: dict = Depends(get_agent),
):
    """Take a lease on a proposed task."""
    ws = await _member_workspace(slug, ws_id, agent)
    await require_space_capability(slug, ws, agent, "claim")
    lease = clamped_lease(lease_minutes)
    now = int(time.time())
    claim_id = str(uuid.uuid4())
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            claim_sql(open_status="proposed", claimed_status="claimed", lease_minutes=lease),
            ("claimed", agent["id"], now, task_id, ws_id, slug, agent["id"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            task = await _get_task(task_id, ws_id)
            if task["status"] == "claimed":
                raise HTTPException(409, f"Task is claimed by another agent until {task.get('claim_expires_at')}")
            raise HTTPException(409, "Task is not in 'proposed' state")
        await db.execute(
            "INSERT INTO task_claims (id, task_id, agent_id, action, ts) VALUES (?,?,?,?,?)",
            (claim_id, task_id, agent["id"], "claimed", now),
        )
        await db.commit()
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="claimed", kind="vtask",
               ref_id=task_id, actor=agent)
    return await _get_task(task_id, ws_id)


@router.post("/{task_id}/release")
async def release_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Release a claimed task back to proposed. Only the claimer can release."""
    await _member_workspace(slug, ws_id, agent)
    task = await _get_task(task_id, ws_id)
    if task["status"] != "claimed":
        raise HTTPException(409, "Task is not in 'claimed' state")
    if task["claimed_by_agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the claimer can release this task")
    now = int(time.time())
    claim_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET claimed_by_agent_id=NULL, claim_expires_at=NULL, status='proposed', updated_ts=? WHERE id=?",
            (now, task_id),
        )
        await db.execute(
            "INSERT INTO task_claims (id, task_id, agent_id, action, ts) VALUES (?,?,?,?,?)",
            (claim_id, task_id, agent["id"], "released", now),
        )
        await db.commit()
    return await _get_task(task_id, ws_id)


class SubmitTaskBody(BaseModel):
    artifact_id: str


@router.post("/{task_id}/submit")
async def submit_task(
    body: SubmitTaskBody,
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent)
):
    """Submit an artifact for review, setting task status to 'review'.
    Only the agent holding a live claim can submit."""
    await _member_workspace(slug, ws_id, agent)
    task = await _get_task(task_id, ws_id)
    if task["claimed_by_agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the claimer can submit")
    if task["status"] != "claimed":
        raise HTTPException(409, f"Task is {task['status']!r}, not claimed; nothing to submit against")
    now = int(time.time())
    async with get_db() as db:
        cur = await db.execute(
            submit_sql(claimed_status="claimed", review_status="review"),
            (now, task_id, ws_id, slug, agent["id"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            raise HTTPException(409, "Your claim's lease has expired; claim the task again before submitting")
        # Link artifact to task via review note
        await db.execute(
            "UPDATE artifacts SET status='submitted', review_note=?, updated_ts=? WHERE id=?",
            (f"submitted_for_task:{task_id}", now, body.artifact_id),
        )
        await db.commit()
    ws = await _workspace(slug, ws_id)
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="delivered", kind="vtask",
               ref_id=task_id, actor=agent, title=task["title"], note=f"artifact {body.artifact_id}")
    return await _get_task(task_id, ws_id)


async def _task_for_review(slug: str, ws_id: str, task_id: str, agent: dict) -> dict:
    ws = await _member_workspace(slug, ws_id, agent)
    task = await _get_task(task_id, ws_id)
    require_not_claimant(task, agent)
    await require_space_capability(slug, ws, agent, "review")
    return task


@router.post("/{task_id}/accept")
async def accept_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
):
    """Accept a task in review. Needs `review` on the workspace; never the claimant."""
    task = await _task_for_review(slug, ws_id, task_id, agent)
    if task["status"] != "review":
        raise HTTPException(409, f"Task is {task['status']!r}, not in review; nothing to accept")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='accepted', updated_ts=? WHERE id=?",
            (now, task_id),
        )
        await db.commit()
    ws = await _workspace(slug, ws_id)
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="accepted", kind="vtask",
               ref_id=task_id, actor=agent, title=task["title"])
    return await _get_task(task_id, ws_id)


class RejectTaskBody(BaseModel):
    note: str = Field("")


@router.post("/{task_id}/reject")
async def reject_task(
    slug: str,
    ws_id: str,
    task_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[RejectTaskBody] = None,
):
    """Reject a task in review, sending it back to proposed. Needs `review`."""
    body = body or RejectTaskBody()
    task = await _task_for_review(slug, ws_id, task_id, agent)
    if task["status"] not in ("claimed", "review"):
        raise HTTPException(409, f"Task is {task['status']!r}; only claimed or in-review work can be rejected")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='proposed', claimed_by_agent_id=NULL, claim_expires_at=NULL, updated_ts=? WHERE id=?",
            (now, task_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='rejected', review_note=?, updated_ts=? WHERE review_note=? AND status='submitted'",
            (body.note, now, f"submitted_for_task:{task_id}"),
        )
        await db.commit()
    ws = await _workspace(slug, ws_id)
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="rejected", kind="vtask",
               ref_id=task_id, actor=agent, title=task["title"], note=body.note)
    return await _get_task(task_id, ws_id)
