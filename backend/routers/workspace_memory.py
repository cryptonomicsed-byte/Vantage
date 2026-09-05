"""Workspace memory (key-value store) endpoints."""
import time
import uuid

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException

from ..db import get_db
from ..deps import get_agent

router = APIRouter(
    prefix="/api/guilds/{slug}/workspaces/{ws_id}/memory",
    tags=["workspace-memory"],
)


@router.get("")
async def get_memory(
    slug: str,
    ws_id: str,
    agent: dict = Depends(get_agent),
):
    """Get all memory entries for the calling agent in this workspace."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM workspace_memory WHERE workspace_id=? AND agent_id=? ORDER BY key",
            (ws_id, agent["id"]),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.put("/{key}")
async def upsert_memory(
    slug: str,
    ws_id: str,
    key: str,
    value: str = Form(...),
    visibility: str = Form("agent"),
    agent: dict = Depends(get_agent),
):
    """Upsert a memory entry. visibility: agent|workspace|guild."""
    if visibility not in ("agent", "workspace", "guild"):
        raise HTTPException(400, "visibility must be one of: agent, workspace, guild")
    now = int(time.time())
    entry_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            """INSERT INTO workspace_memory (id, workspace_id, agent_id, key, value, visibility, created_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(workspace_id, agent_id, key)
               DO UPDATE SET value=excluded.value, visibility=excluded.visibility, updated_ts=excluded.updated_ts""",
            (entry_id, ws_id, agent["id"], key, value, visibility, now, now),
        )
        await db.commit()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM workspace_memory WHERE workspace_id=? AND agent_id=? AND key=?",
            (ws_id, agent["id"], key),
        ) as cur:
            row = await cur.fetchone()
    return dict(row)


@router.delete("/{key}")
async def delete_memory(
    slug: str,
    ws_id: str,
    key: str,
    agent: dict = Depends(get_agent),
):
    """Delete a memory entry. Only the owner agent can delete."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM workspace_memory WHERE workspace_id=? AND agent_id=? AND key=?",
            (ws_id, agent["id"], key),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Memory entry not found")
    async with get_db() as db:
        await db.execute(
            "DELETE FROM workspace_memory WHERE workspace_id=? AND agent_id=? AND key=?",
            (ws_id, agent["id"], key),
        )
        await db.commit()
    return {"deleted": True, "key": key}


@router.get("/shared")
async def get_shared_memory(
    slug: str,
    ws_id: str,
    agent: dict = Depends(get_agent),
):
    """Get all entries with visibility='workspace' or 'guild' for this workspace."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM workspace_memory WHERE workspace_id=? AND visibility IN ('workspace','guild') ORDER BY key",
            (ws_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
