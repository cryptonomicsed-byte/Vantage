"""Content Creation Space — dedicated media production collaboration.

Every route, reads included, requires membership of the guild that owns the
space (G0). Deleting a space takes its owner or guild staff. A claim is a
lease, a submit needs the live claim, and accept or reject takes a reviewer,
never the claimant.
"""
import logging
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import get_db
from ..deps import get_agent
from ..guild_access import require_member, require_not_claimant, require_owner_or_staff
from ..space_tasks import DEFAULT_LEASE_MINUTES, claim_sql, clamped_lease, submit_sql

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds/{slug}/content-spaces", tags=["content-space"])

# Content keeps its own status words for now; G3 maps them onto the shared
# vocabulary. open -> claimed -> review -> accepted / back to open.
S_OPEN, S_CLAIMED, S_REVIEW, S_DONE = "brief_submitted", "in_production", "in_review", "published"


async def _get_space(space_id: str, slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_spaces WHERE id=? AND guild_slug=?", (space_id, slug)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Content space not found")
    return dict(row)


async def _member_space(slug: str, space_id: str, agent: dict) -> dict:
    await require_member(slug, agent)
    return await _get_space(space_id, slug)


async def _get_task(slug: str, space_id: str, task_id: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=? AND guild_slug=?",
            (task_id, space_id, slug),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    return dict(row)


# ── Space CRUD ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_content_spaces(slug: str, agent: dict = Depends(get_agent)):
    await require_member(slug, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_spaces WHERE guild_slug=? ORDER BY created_ts DESC", (slug,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class CreateContentSpaceBody(BaseModel):
    name: str
    mcp_provider: str = Field("vantage_anvil")
    series_id: Optional[int] = Field(None)


@router.post("")
async def create_content_space(
    body: CreateContentSpaceBody,
    slug: str,
    agent: dict = Depends(get_agent)
):
    name = body.name
    mcp_provider = body.mcp_provider
    series_id = body.series_id
    await require_member(slug, agent)
    space_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO content_spaces (id, guild_slug, name, mcp_provider, series_id, created_by_agent_id, created_ts) VALUES (?,?,?,?,?,?,?)",
            (space_id, slug, name, mcp_provider, series_id, agent["id"], now),
        )
        await db.commit()

    # Create backing chat channel for this content space
    _chat_slug = f"cs-{space_id[:8]}"
    try:
        async with get_db() as _cdb:
            _cdb.row_factory = aiosqlite.Row
            async with _cdb.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as _cur:
                _guild_row = await _cur.fetchone()
            if _guild_row:
                _guild_id = _guild_row["id"]
                await _cdb.execute(
                    """INSERT OR IGNORE INTO guild_channels
                       (name, slug, guild_id, channel_kind, collab_type, topic)
                       VALUES (?, ?, ?, 'forum', 'content', ?)""",
                    (name, _chat_slug, _guild_id, f"Content space chat: {name}"),
                )
                await _cdb.execute(
                    "UPDATE content_spaces SET chat_channel_slug=? WHERE id=?",
                    (_chat_slug, space_id),
                )
                await _cdb.commit()
    except Exception as _exc:
        logger.warning("Failed to create backing channel for content space: %s", _exc)

    return {"id": space_id, "name": name, "mcp_provider": mcp_provider, "chat_channel_slug": _chat_slug}


@router.get("/{space_id}")
async def get_content_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    return await _member_space(slug, space_id, agent)


@router.delete("/{space_id}")
async def delete_content_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    space = await _member_space(slug, space_id, agent)
    await require_owner_or_staff(slug, agent, space.get("created_by_agent_id"), "this content space")
    async with get_db() as db:
        await db.execute("DELETE FROM content_spaces WHERE id=?", (space_id,))
        await db.commit()
    return {"ok": True}


# ── Projects ────────────────────────────────────────────────────────────────────

@router.get("/{space_id}/projects")
async def list_projects(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_projects WHERE space_id=? ORDER BY created_ts DESC", (space_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class CreateProjectBody(BaseModel):
    title: str
    brief: str = Field("")
    provider: str = Field("")


@router.post("/{space_id}/projects")
async def create_project(
    body: CreateProjectBody,
    slug: str, space_id: str,
    agent: dict = Depends(get_agent)
):
    title = body.title
    brief = body.brief
    provider = body.provider
    await _member_space(slug, space_id, agent)
    project_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO content_projects (id, space_id, title, brief, status, provider, created_by_agent_id, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, space_id, title, brief, "brief_submitted", provider, agent["id"], now, now),
        )
        await db.commit()
    return {"id": project_id, "status": "brief_submitted"}


class UpdateProjectBody(BaseModel):
    status: Optional[str] = Field(None)
    title: Optional[str] = Field(None)
    brief: Optional[str] = Field(None)


@router.patch("/{space_id}/projects/{project_id}")
async def update_project(
    slug: str, space_id: str, project_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[UpdateProjectBody] = None,
):
    body = body or UpdateProjectBody()
    status = body.status
    title = body.title
    brief = body.brief
    await _member_space(slug, space_id, agent)
    valid_statuses = ("brief_submitted", "in_production", "in_review", "published")
    if status and status not in valid_statuses:
        raise HTTPException(400, f"status must be one of: {', '.join(valid_statuses)}")
    now = int(time.time())
    async with get_db() as db:
        if status:
            await db.execute("UPDATE content_projects SET status=?, updated_ts=? WHERE id=? AND space_id=?", (status, now, project_id, space_id))
        if title:
            await db.execute("UPDATE content_projects SET title=?, updated_ts=? WHERE id=? AND space_id=?", (title, now, project_id, space_id))
        if brief:
            await db.execute("UPDATE content_projects SET brief=?, updated_ts=? WHERE id=? AND space_id=?", (brief, now, project_id, space_id))
        await db.commit()
    return {"ok": True}


# ── Tasks (reuse vantage_tasks with space_id as workspace_id) ───────────────────

@router.get("/{space_id}/tasks")
async def list_tasks(
    slug: str, space_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if status:
            async with db.execute(
                "SELECT * FROM vantage_tasks WHERE workspace_id=? AND status=? ORDER BY priority DESC, created_ts DESC LIMIT ?",
                (space_id, status, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM vantage_tasks WHERE workspace_id=? ORDER BY priority DESC, created_ts DESC LIMIT ?",
                (space_id, limit),
            ) as cur:
                rows = await cur.fetchall()
    items = [dict(r) for r in rows]
    # Envelope: the board (ContentSpaceTasks.tsx) reads .tasks; the bare
    # array it used to get rendered as an empty board forever.
    return {"tasks": items, "total": len(items)}


class CreateTaskBody(BaseModel):
    title: str
    description: str = Field("")
    priority: int = Field(50)
    kind_tag: str = Field("")
    project_id: str = Field("")


@router.post("/{space_id}/tasks")
async def create_task(
    body: CreateTaskBody,
    slug: str, space_id: str,
    agent: dict = Depends(get_agent)
):
    title = body.title
    description = body.description
    priority = body.priority
    kind_tag = body.kind_tag
    project_id = body.project_id
    await _member_space(slug, space_id, agent)
    task_id = str(uuid.uuid4())
    now = int(time.time())
    # Store project_id in kind_tag as "project:{id}" prefix if given
    effective_kind = f"project:{project_id}" if project_id else kind_tag
    async with get_db() as db:
        await db.execute(
            "INSERT INTO vantage_tasks (id, workspace_id, guild_slug, title, description, status, priority, created_by_agent_id, kind_tag, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, space_id, slug, title, description, S_OPEN, priority, agent["id"], effective_kind, now, now),
        )
        await db.commit()
    return {"id": task_id, "status": S_OPEN}


@router.post("/{space_id}/tasks/{task_id}/claim")
async def claim_task(slug: str, space_id: str, task_id: str,
                     lease_minutes: int = Query(DEFAULT_LEASE_MINUTES),
                     agent: dict = Depends(get_agent)):
    """Take a lease on an open task. Same rule as the trading space: one
    atomic UPDATE, so a claim cannot be stolen while it is live."""
    await _member_space(slug, space_id, agent)
    lease = clamped_lease(lease_minutes)
    now = int(time.time())
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            claim_sql(open_status=S_OPEN, claimed_status=S_CLAIMED, lease_minutes=lease),
            (S_CLAIMED, agent["id"], now, task_id, space_id, slug, agent["id"]),
        )
        await db.commit()
        async with db.execute(
            "SELECT status, claimed_by_agent_id, claim_expires_at FROM vantage_tasks "
            "WHERE id=? AND workspace_id=? AND guild_slug=?", (task_id, space_id, slug)) as c:
            row = await c.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    if cur.rowcount == 0:
        if row["status"] == S_CLAIMED:
            raise HTTPException(409, f"Task is claimed by another agent until {row['claim_expires_at']}")
        raise HTTPException(400, "Task is not claimable")
    return {"ok": True, "status": S_CLAIMED, "claim_expires_at": row["claim_expires_at"]}


class SubmitTaskBody(BaseModel):
    artifact_kind: str = Field("other")
    title: str
    content_text: str = Field("")
    content_uri: str = Field("")


@router.post("/{space_id}/tasks/{task_id}/submit")
async def submit_task(
    body: SubmitTaskBody,
    slug: str, space_id: str, task_id: str,
    agent: dict = Depends(get_agent)
):
    artifact_kind = body.artifact_kind
    title = body.title
    content_text = body.content_text
    content_uri = body.content_uri
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=? AND guild_slug=?",
                              (task_id, space_id, slug)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["claimed_by_agent_id"] != agent["id"]:
            raise HTTPException(403, "Only the claimer can submit")
        if row["status"] != S_CLAIMED:
            raise HTTPException(409, f"Task is {row['status']!r}, not in production; nothing to submit against")
        artifact_id = str(uuid.uuid4())
        now = int(time.time())
        cur = await db.execute(
            submit_sql(claimed_status=S_CLAIMED, review_status=S_REVIEW),
            (now, task_id, space_id, slug, agent["id"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            raise HTTPException(409, "Your claim's lease has expired; claim the task again before submitting")
        await db.execute(
            "INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, content_uri, status, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, task_id, agent["id"], space_id, artifact_kind, title, content_text, content_uri, "submitted", now, now),
        )
        await db.commit()
    return {"ok": True, "artifact_id": artifact_id}


async def _task_for_review(slug: str, space_id: str, task_id: str, agent: dict) -> dict:
    """The task, once the caller is confirmed as a reviewer: a member who is
    the space owner or guild staff, and never the claimant."""
    space = await _member_space(slug, space_id, agent)
    task = await _get_task(slug, space_id, task_id)
    require_not_claimant(task, agent)
    await require_owner_or_staff(slug, agent, space.get("created_by_agent_id"), "this content space")
    return task


class AcceptTaskBody(BaseModel):
    note: str = Field("")


@router.post("/{space_id}/tasks/{task_id}/accept")
async def accept_task(
    slug: str, space_id: str, task_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[AcceptTaskBody] = None,
):
    body = body or AcceptTaskBody()
    note = body.note
    task = await _task_for_review(slug, space_id, task_id, agent)
    if task["status"] != S_REVIEW:
        raise HTTPException(409, f"Task is {task['status']!r}, not in review; nothing to accept")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status=?, updated_ts=? WHERE id=? AND workspace_id=?",
            (S_DONE, now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='accepted', review_note=?, updated_ts=? WHERE task_id=?",
            (note, now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": S_DONE}


class RejectTaskBody(BaseModel):
    note: str = Field("")


@router.post("/{space_id}/tasks/{task_id}/reject")
async def reject_task(
    slug: str, space_id: str, task_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[RejectTaskBody] = None,
):
    body = body or RejectTaskBody()
    note = body.note
    task = await _task_for_review(slug, space_id, task_id, agent)
    if task["status"] not in (S_CLAIMED, S_REVIEW):
        raise HTTPException(409, f"Task is {task['status']!r}; only in-production or in-review work can be rejected")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status=?, claimed_by_agent_id=NULL, claim_expires_at=NULL, updated_ts=? WHERE id=? AND workspace_id=?",
            (S_OPEN, now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='rejected', review_note=?, updated_ts=? WHERE task_id=? AND status='submitted'",
            (note, now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": S_OPEN}


# ── Assets ──────────────────────────────────────────────────────────────────────

@router.get("/{space_id}/assets")
async def list_assets(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_assets WHERE space_id=? ORDER BY created_ts DESC LIMIT 100",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class CreateAssetBody(BaseModel):
    project_id: str
    kind: str = Field("other")
    title: str
    content_uri: str = Field("")
    content_hash: str = Field("")
    provider: str = Field("")


@router.post("/{space_id}/assets")
async def create_asset(
    body: CreateAssetBody,
    slug: str, space_id: str,
    agent: dict = Depends(get_agent)
):
    project_id = body.project_id
    kind = body.kind
    title = body.title
    content_uri = body.content_uri
    content_hash = body.content_hash
    provider = body.provider
    await _member_space(slug, space_id, agent)
    asset_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO content_assets (id, project_id, space_id, kind, title, content_uri, content_hash, provider, status, created_by_agent_id, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, project_id, space_id, kind, title, content_uri, content_hash, provider, "draft", agent["id"], now),
        )
        await db.commit()
    return {"id": asset_id, "kind": kind, "status": "draft"}


# ── Memory (reuse workspace_memory) ─────────────────────────────────────────────

@router.get("/{space_id}/memory")
async def get_memory(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    """Your own rows, plus rows other members shared with the space or guild."""
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM workspace_memory
                WHERE workspace_id=? AND (agent_id=? OR visibility IN ('workspace', 'guild'))
                ORDER BY key""",
            (space_id, agent["id"]),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class UpsertMemoryBody(BaseModel):
    value: str
    visibility: str = Field("agent")


@router.put("/{space_id}/memory/{key}")
async def upsert_memory(
    body: UpsertMemoryBody,
    slug: str, space_id: str, key: str,
    agent: dict = Depends(get_agent)
):
    value = body.value
    visibility = body.visibility
    await _member_space(slug, space_id, agent)
    if visibility not in ("agent", "workspace", "guild"):
        raise HTTPException(400, "visibility must be agent|workspace|guild")
    now = int(time.time())
    entry_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO workspace_memory (id, workspace_id, agent_id, key, value, visibility, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id, agent_id, key) DO UPDATE SET value=excluded.value, visibility=excluded.visibility, updated_ts=excluded.updated_ts",
            (entry_id, space_id, agent["id"], key, value, visibility, now, now),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/{space_id}/memory/{key}")
async def delete_memory(slug: str, space_id: str, key: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        await db.execute(
            "DELETE FROM workspace_memory WHERE workspace_id=? AND agent_id=? AND key=?",
            (space_id, agent["id"], key),
        )
        await db.commit()
    return {"ok": True}


# ── Artifacts & Receipts ────────────────────────────────────────────────────────

@router.get("/{space_id}/artifacts")
async def list_artifacts(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM artifacts WHERE workspace_id=? ORDER BY created_ts DESC LIMIT 50",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {"artifacts": [dict(r) for r in rows]}


@router.get("/{space_id}/receipts")
async def list_receipts(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT r.* FROM execution_receipts r JOIN artifacts a ON r.artifact_id=a.id WHERE a.workspace_id=? ORDER BY r.created_ts DESC LIMIT 50",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {"receipts": [dict(r) for r in rows]}


# ── NIP-29 publishing ───────────────────────────────────────────────────────────

from pydantic import BaseModel as _BaseModel


class _ClaimPub(_BaseModel):
    task_id: str
    task_desc: str = ""


class _StatusPub(_BaseModel):
    status: str
    detail: str = ""


@router.post("/{space_id}/publish/claim")
async def publish_claim(slug: str, space_id: str, body: _ClaimPub, agent: dict = Depends(get_agent)):
    """Publish a task claim event to NIP-29. The task must exist here and
    be held by the caller; the relay publish itself is best-effort."""
    space = await _member_space(slug, space_id, agent)
    task = await _get_task(slug, space_id, body.task_id)
    if task["claimed_by_agent_id"] != agent["id"]:
        raise HTTPException(403, "You do not hold a claim on this task")
    published = False
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("chat_channel_slug") or space_id
        await ws_module.publish_task_claim(RELAY_WS_URL, pk, group_id, body.task_id, body.task_desc)
        published = True
    except Exception as exc:
        logger.warning("NIP-29 claim publish failed (non-fatal): %s", exc)
    return {"ok": True, "published": published}


@router.post("/{space_id}/publish/status")
async def publish_status(slug: str, space_id: str, body: _StatusPub, agent: dict = Depends(get_agent)):
    """Publish agent status to NIP-29 (best-effort)."""
    space = await _member_space(slug, space_id, agent)
    published = False
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("chat_channel_slug") or space_id
        await ws_module.publish_status(RELAY_WS_URL, pk, group_id, body.status, body.detail)
        published = True
    except Exception as exc:
        logger.warning("NIP-29 status publish failed (non-fatal): %s", exc)
    return {"ok": True, "published": published}
