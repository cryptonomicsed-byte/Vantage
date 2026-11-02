"""Content Creation Space — dedicated media production collaboration."""
import logging
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds/{slug}/content-spaces", tags=["content-space"])


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


# ── Space CRUD ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_content_spaces(slug: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_spaces WHERE guild_slug=? ORDER BY created_ts DESC", (slug,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("")
async def create_content_space(
    slug: str,
    name: str = Form(...),
    mcp_provider: str = Form("vantage_anvil"),
    series_id: Optional[int] = Form(None),
    agent: dict = Depends(get_agent),
):
    space_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO content_spaces (id, guild_slug, name, mcp_provider, series_id, created_by_agent_id, created_ts) VALUES (?,?,?,?,?,?,?)",
            (space_id, slug, name, mcp_provider, series_id, agent["id"], now),
        )
        await db.commit()
    return {"id": space_id, "name": name, "mcp_provider": mcp_provider}


@router.get("/{space_id}")
async def get_content_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    return await _get_space(space_id, slug)


@router.delete("/{space_id}")
async def delete_content_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _get_space(space_id, slug)
    async with get_db() as db:
        await db.execute("DELETE FROM content_spaces WHERE id=?", (space_id,))
        await db.commit()
    return {"ok": True}


# ── Projects ────────────────────────────────────────────────────────────────────

@router.get("/{space_id}/projects")
async def list_projects(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _get_space(space_id, slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_projects WHERE space_id=? ORDER BY created_ts DESC", (space_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/{space_id}/projects")
async def create_project(
    slug: str, space_id: str,
    title: str = Form(...),
    brief: str = Form(""),
    provider: str = Form(""),
    agent: dict = Depends(get_agent),
):
    await _get_space(space_id, slug)
    project_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO content_projects (id, space_id, title, brief, status, provider, created_by_agent_id, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, space_id, title, brief, "brief_submitted", provider, agent["id"], now, now),
        )
        await db.commit()
    return {"id": project_id, "status": "brief_submitted"}


@router.patch("/{space_id}/projects/{project_id}")
async def update_project(
    slug: str, space_id: str, project_id: str,
    status: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    brief: Optional[str] = Form(None),
    agent: dict = Depends(get_agent),
):
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
    await _get_space(space_id, slug)
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
    return [dict(r) for r in rows]


@router.post("/{space_id}/tasks")
async def create_task(
    slug: str, space_id: str,
    title: str = Form(...),
    description: str = Form(""),
    priority: int = Form(50),
    kind_tag: str = Form(""),
    project_id: str = Form(""),
    agent: dict = Depends(get_agent),
):
    await _get_space(space_id, slug)
    task_id = str(uuid.uuid4())
    now = int(time.time())
    # Store project_id in kind_tag as "project:{id}" prefix if given
    effective_kind = f"project:{project_id}" if project_id else kind_tag
    async with get_db() as db:
        await db.execute(
            "INSERT INTO vantage_tasks (id, workspace_id, guild_slug, title, description, status, priority, created_by_agent_id, kind_tag, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, space_id, slug, title, description, "brief_submitted", priority, agent["id"], effective_kind, now, now),
        )
        await db.commit()
    return {"id": task_id, "status": "brief_submitted"}


@router.post("/{space_id}/tasks/{task_id}/claim")
async def claim_task(slug: str, space_id: str, task_id: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=?", (task_id, space_id)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        now = int(time.time())
        await db.execute(
            "UPDATE vantage_tasks SET status='in_production', claimed_by_agent_id=?, updated_ts=? WHERE id=?",
            (agent["id"], now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": "in_production"}


@router.post("/{space_id}/tasks/{task_id}/submit")
async def submit_task(
    slug: str, space_id: str, task_id: str,
    artifact_kind: str = Form("other"),
    title: str = Form(...),
    content_text: str = Form(""),
    content_uri: str = Form(""),
    agent: dict = Depends(get_agent),
):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=?", (task_id, space_id)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["claimed_by_agent_id"] != agent["id"]:
            raise HTTPException(403, "Only the claimer can submit")
        artifact_id = str(uuid.uuid4())
        now = int(time.time())
        await db.execute(
            "INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, content_uri, status, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, task_id, agent["id"], space_id, artifact_kind, title, content_text, content_uri, "submitted", now, now),
        )
        await db.execute("UPDATE vantage_tasks SET status='in_review', updated_ts=? WHERE id=?", (now, task_id))
        await db.commit()
    return {"ok": True, "artifact_id": artifact_id}


@router.post("/{space_id}/tasks/{task_id}/accept")
async def accept_task(
    slug: str, space_id: str, task_id: str,
    note: str = Form(""),
    agent: dict = Depends(get_agent),
):
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='published', updated_ts=? WHERE id=? AND workspace_id=?",
            (now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='accepted', review_note=?, updated_ts=? WHERE task_id=?",
            (note, now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": "published"}


@router.post("/{space_id}/tasks/{task_id}/reject")
async def reject_task(
    slug: str, space_id: str, task_id: str,
    note: str = Form(""),
    agent: dict = Depends(get_agent),
):
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='brief_submitted', claimed_by_agent_id=NULL, updated_ts=? WHERE id=? AND workspace_id=?",
            (now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='rejected', review_note=?, updated_ts=? WHERE task_id=? AND status='submitted'",
            (note, now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": "brief_submitted"}


# ── Assets ──────────────────────────────────────────────────────────────────────

@router.get("/{space_id}/assets")
async def list_assets(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _get_space(space_id, slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM content_assets WHERE space_id=? ORDER BY created_ts DESC LIMIT 100",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/{space_id}/assets")
async def create_asset(
    slug: str, space_id: str,
    project_id: str = Form(...),
    kind: str = Form("other"),
    title: str = Form(...),
    content_uri: str = Form(""),
    content_hash: str = Form(""),
    provider: str = Form(""),
    agent: dict = Depends(get_agent),
):
    await _get_space(space_id, slug)
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
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM workspace_memory WHERE workspace_id=? AND agent_id=? ORDER BY key",
            (space_id, agent["id"]),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.put("/{space_id}/memory/{key}")
async def upsert_memory(
    slug: str, space_id: str, key: str,
    value: str = Form(...),
    visibility: str = Form("agent"),
    agent: dict = Depends(get_agent),
):
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
    """Publish a task claim event to NIP-29 (best-effort)."""
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        space = await _get_space(space_id, slug)
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("name", space_id)
        await ws_module.publish_task_claim(RELAY_WS_URL, pk, group_id, body.task_id, body.task_desc)
    except Exception as exc:
        logger.warning("NIP-29 claim publish failed (non-fatal): %s", exc)
    return {"ok": True}


@router.post("/{space_id}/publish/status")
async def publish_status(slug: str, space_id: str, body: _StatusPub, agent: dict = Depends(get_agent)):
    """Publish agent status to NIP-29 (best-effort)."""
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        space = await _get_space(space_id, slug)
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("name", space_id)
        await ws_module.publish_status(RELAY_WS_URL, pk, group_id, body.status, body.detail)
    except Exception as exc:
        logger.warning("NIP-29 status publish failed (non-fatal): %s", exc)
    return {"ok": True}
