"""Sovereign agent task lifecycle API — exposed as MCP tools via fastapi-mcp."""
import secrets
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..db import get_db
from ..deps import get_agent

router = APIRouter(prefix="/api/guilds/{guild_slug}/tasks", tags=["tasks"])


async def _get_guild(slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


async def _require_member(guild_id: int, agent_id: int) -> str:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, agent_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(403, "Not a guild member")
    return dict(row)["role"]


@router.get(
    "",
    summary="List guild tasks",
    description="List tasks for a guild. Filter by status (comma-separated). Available to all guild members.",
)
async def list_tasks(
    guild_slug: str,
    status: Optional[str] = Query(None, description="Comma-separated statuses: proposed,claimed,executing,blocked,review,accepted,rejected"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    query = "SELECT * FROM guild_tasks WHERE guild_id=?"
    params: list = [guild["id"]]
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
    query += " ORDER BY priority DESC, created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return {"tasks": [dict(r) for r in rows]}


@router.post(
    "",
    summary="Create a task",
    description="Create a new task in the guild. Agent must be a guild member.",
)
async def create_task(
    guild_slug: str,
    title: str = Form(..., min_length=1, max_length=200),
    description: str = Form("", max_length=4000),
    priority: int = Form(50, ge=0, le=100),
    kind_tag: str = Form("", max_length=80),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    await _require_member(guild["id"], agent["id"])
    task_id = secrets.token_hex(16)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_tasks
               (id, guild_id, guild_slug, title, description, priority, kind_tag, created_by_id, created_by_name)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (task_id, guild["id"], guild_slug, title, description, priority, kind_tag,
             agent["id"], agent["name"]),
        )
        await db.commit()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guild_tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
    return {"task": dict(row)}


@router.get(
    "/{task_id}",
    summary="Get task detail",
    description="Get a task with its full artifact list.",
)
async def get_task(guild_slug: str, task_id: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_id=?", (task_id, guild["id"])
        ) as cur:
            task_row = await cur.fetchone()
        if not task_row:
            raise HTTPException(404, "Task not found")
        async with db.execute(
            "SELECT * FROM guild_artifacts WHERE task_id=? ORDER BY created_at DESC", (task_id,)
        ) as cur:
            artifact_rows = await cur.fetchall()
    return {"task": dict(task_row), "artifacts": [dict(r) for r in artifact_rows]}


@router.post(
    "/{task_id}/claim",
    summary="Claim a task",
    description="Claim a proposed task. Only works when task status is 'proposed'.",
)
async def claim_task(guild_slug: str, task_id: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    await _require_member(guild["id"], agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_id=?", (task_id, guild["id"])
        ) as cur:
            task = await cur.fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        task = dict(task)
        if task["status"] != "proposed":
            raise HTTPException(409, f"Task is not claimable (status={task['status']})")
        await db.execute(
            """UPDATE guild_tasks
               SET status='claimed', claimed_by_id=?, claimed_by_name=?, updated_at=datetime('now')
               WHERE id=?""",
            (agent["id"], agent["name"], task_id),
        )
        claim_id = secrets.token_hex(16)
        await db.execute(
            "INSERT INTO guild_task_claims (id, task_id, agent_id, agent_name, action) VALUES (?,?,?,?,'claimed')",
            (claim_id, task_id, agent["id"], agent["name"]),
        )
        await db.commit()
    return {"status": "claimed", "task_id": task_id}


@router.post(
    "/{task_id}/release",
    summary="Release a task",
    description="Release a claimed task back to proposed status. Only the claiming agent can release.",
)
async def release_task(
    guild_slug: str,
    task_id: str,
    note: str = Form("", max_length=500),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_id=?", (task_id, guild["id"])
        ) as cur:
            task = await cur.fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        task = dict(task)
        if task["claimed_by_id"] != agent["id"]:
            raise HTTPException(403, "You did not claim this task")
        await db.execute(
            """UPDATE guild_tasks
               SET status='proposed', claimed_by_id=NULL, claimed_by_name=NULL, updated_at=datetime('now')
               WHERE id=?""",
            (task_id,),
        )
        claim_id = secrets.token_hex(16)
        await db.execute(
            "INSERT INTO guild_task_claims (id, task_id, agent_id, agent_name, action, note) VALUES (?,?,?,?,'released',?)",
            (claim_id, task_id, agent["id"], agent["name"], note),
        )
        await db.commit()
    return {"status": "released", "task_id": task_id}


@router.post(
    "/{task_id}/submit",
    summary="Submit artifact for task",
    description="Submit an artifact for a claimed task. Moves task to 'review' status.",
)
async def submit_artifact(
    guild_slug: str,
    task_id: str,
    artifact_kind: str = Form("other", description="code|doc|data|eval|tool_output|other"),
    artifact_title: str = Form(..., min_length=1, max_length=200),
    content_text: str = Form("", max_length=100000),
    content_hash: str = Form("", max_length=128, description="BLAKE3 hex of content"),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_id=?", (task_id, guild["id"])
        ) as cur:
            task = await cur.fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        task = dict(task)
        if task["claimed_by_id"] != agent["id"]:
            raise HTTPException(403, "You did not claim this task")
        if task["status"] not in ("claimed", "executing"):
            raise HTTPException(409, f"Task cannot be submitted from status={task['status']}")
        artifact_id = secrets.token_hex(16)
        await db.execute(
            """INSERT INTO guild_artifacts (id, task_id, guild_id, agent_id, agent_name, kind, title, content_text, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (artifact_id, task_id, guild["id"], agent["id"], agent["name"],
             artifact_kind, artifact_title, content_text, content_hash),
        )
        await db.execute(
            "UPDATE guild_tasks SET status='review', updated_at=datetime('now') WHERE id=?",
            (task_id,),
        )
        await db.commit()
    return {"artifact_id": artifact_id, "task_id": task_id, "status": "review"}


@router.post(
    "/{task_id}/accept",
    summary="Accept task submission",
    description="Accept the artifact and mark task as done. Requires founder role or task creator.",
)
async def accept_task(
    guild_slug: str,
    task_id: str,
    review_note: str = Form("", max_length=500),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    role = await _require_member(guild["id"], agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_id=?", (task_id, guild["id"])
        ) as cur:
            task = await cur.fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        task = dict(task)
        if task["created_by_id"] != agent["id"] and role != "founder":
            raise HTTPException(403, "Only task creator or guild founder can accept")
        if task["status"] != "review":
            raise HTTPException(409, "Task is not in review")
        await db.execute(
            """UPDATE guild_artifacts SET status='accepted', review_note=?, updated_at=datetime('now')
               WHERE task_id=? AND status='submitted'""",
            (review_note, task_id),
        )
        await db.execute(
            "UPDATE guild_tasks SET status='accepted', updated_at=datetime('now') WHERE id=?",
            (task_id,),
        )
        claim_id = secrets.token_hex(16)
        await db.execute(
            "INSERT INTO guild_task_claims (id, task_id, agent_id, agent_name, action, note) VALUES (?,?,?,?,'accepted',?)",
            (claim_id, task_id, agent["id"], agent["name"], review_note),
        )
        await db.commit()
    return {"status": "accepted", "task_id": task_id}


@router.post(
    "/{task_id}/reject",
    summary="Reject task submission",
    description="Reject the artifact and re-open task for claiming. Requires founder role or task creator.",
)
async def reject_task(
    guild_slug: str,
    task_id: str,
    review_note: str = Form(..., min_length=1, max_length=500),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    role = await _require_member(guild["id"], agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_tasks WHERE id=? AND guild_id=?", (task_id, guild["id"])
        ) as cur:
            task = await cur.fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        task = dict(task)
        if task["created_by_id"] != agent["id"] and role != "founder":
            raise HTTPException(403, "Only task creator or guild founder can reject")
        if task["status"] != "review":
            raise HTTPException(409, "Task is not in review")
        await db.execute(
            """UPDATE guild_artifacts SET status='rejected', review_note=?, updated_at=datetime('now')
               WHERE task_id=? AND status='submitted'""",
            (review_note, task_id),
        )
        await db.execute(
            """UPDATE guild_tasks
               SET status='proposed', claimed_by_id=NULL, claimed_by_name=NULL, updated_at=datetime('now')
               WHERE id=?""",
            (task_id,),
        )
        claim_id = secrets.token_hex(16)
        await db.execute(
            "INSERT INTO guild_task_claims (id, task_id, agent_id, agent_name, action, note) VALUES (?,?,?,?,'rejected',?)",
            (claim_id, task_id, agent["id"], agent["name"], review_note),
        )
        await db.commit()
    return {"status": "rejected", "task_id": task_id}


@router.post(
    "/{task_id}/receipt",
    summary="Attach execution receipt",
    description="Attach an Omo-Koda2 ActReceipt to the most recent artifact for this task.",
)
async def attach_receipt(
    guild_slug: str,
    task_id: str,
    receipt_body: str = Form(..., description="JSON string of the ActReceipt"),
    omokoda_receipt_id: str = Form("", max_length=128),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_artifacts WHERE task_id=? AND agent_id=? ORDER BY created_at DESC LIMIT 1",
            (task_id, agent["id"]),
        ) as cur:
            artifact = await cur.fetchone()
        if not artifact:
            raise HTTPException(404, "No artifact found for this agent on this task")
        artifact = dict(artifact)
        receipt_id = secrets.token_hex(16)
        omokoda_id = omokoda_receipt_id if omokoda_receipt_id else None
        await db.execute(
            """INSERT OR IGNORE INTO guild_execution_receipts
               (id, artifact_id, task_id, agent_id, omokoda_receipt_id, receipt_body)
               VALUES (?,?,?,?,?,?)""",
            (receipt_id, artifact["id"], task_id, agent["id"], omokoda_id, receipt_body),
        )
        await db.commit()
    return {"receipt_id": receipt_id, "artifact_id": artifact["id"]}
