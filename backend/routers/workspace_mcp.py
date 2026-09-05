"""MCP tool dispatch endpoints for workspace operations."""
import json
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..db import get_db
from ..deps import get_agent

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

_MCP_TOOLS = [
    {
        "name": "vantage_list_tasks",
        "description": "List workspace tasks by status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "vantage_claim_task",
        "description": "Claim a proposed task",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "vantage_submit_artifact",
        "description": "Submit artifact for a task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "kind": {"type": "string"},
                "content_text": {"type": "string"},
            },
            "required": ["task_id", "title"],
        },
    },
    {
        "name": "vantage_read_memory",
        "description": "Read workspace memory key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "key": {"type": "string"},
            },
            "required": ["workspace_id", "key"],
        },
    },
    {
        "name": "vantage_write_memory",
        "description": "Write workspace memory key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["workspace_id", "key", "value"],
        },
    },
    {
        "name": "vantage_update_presence",
        "description": "Set agent presence state",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    },
    {
        "name": "vantage_get_roster",
        "description": "Get workspace agent roster",
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
        },
    },
]


def _ok(result) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@router.post("/tools/list")
async def list_tools():
    """Returns list of available MCP tools. No auth required."""
    return {"tools": _MCP_TOOLS}


@router.post("/tools/call")
async def call_tool(request: Request, agent: dict = Depends(get_agent)):
    """Dispatch a tool call by name. Requires X-Agent-Key."""
    body = await request.json()
    tool_name = body.get("name", "")
    args = body.get("arguments", {})

    if tool_name == "vantage_list_tasks":
        workspace_id = args.get("workspace_id", "")
        status = args.get("status")
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            if status:
                async with db.execute(
                    "SELECT * FROM vantage_tasks WHERE workspace_id=? AND status=? ORDER BY priority DESC, created_ts DESC LIMIT 50",
                    (workspace_id, status),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM vantage_tasks WHERE workspace_id=? ORDER BY priority DESC, created_ts DESC LIMIT 50",
                    (workspace_id,),
                ) as cur:
                    rows = await cur.fetchall()
        return _ok([dict(r) for r in rows])

    elif tool_name == "vantage_claim_task":
        task_id = args.get("task_id", "")
        now = int(time.time())
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM vantage_tasks WHERE id=?", (task_id,)) as cur:
                task_row = await cur.fetchone()
        if not task_row:
            raise HTTPException(404, "Task not found")
        task = dict(task_row)
        if task["status"] != "proposed":
            raise HTTPException(409, "Task is not in 'proposed' state")
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
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM vantage_tasks WHERE id=?", (task_id,)) as cur:
                row = await cur.fetchone()
        return _ok(dict(row))

    elif tool_name == "vantage_submit_artifact":
        task_id = args.get("task_id", "")
        title = args.get("title", "")
        kind = args.get("kind", "other")
        content_text = args.get("content_text", "")
        # Get workspace_id from task
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT workspace_id FROM vantage_tasks WHERE id=?", (task_id,)) as cur:
                task_row = await cur.fetchone()
        if not task_row:
            raise HTTPException(404, "Task not found")
        workspace_id = task_row["workspace_id"]
        artifact_id = str(uuid.uuid4())
        now = int(time.time())
        async with get_db() as db:
            await db.execute(
                """INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, created_ts, updated_ts)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (artifact_id, task_id, agent["id"], workspace_id, kind, title, content_text, now, now),
            )
            await db.execute(
                "UPDATE vantage_tasks SET status='review', updated_ts=? WHERE id=?",
                (now, task_id),
            )
            await db.commit()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)) as cur:
                row = await cur.fetchone()
        return _ok(dict(row))

    elif tool_name == "vantage_read_memory":
        workspace_id = args.get("workspace_id", "")
        key = args.get("key", "")
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workspace_memory WHERE workspace_id=? AND agent_id=? AND key=?",
                (workspace_id, agent["id"], key),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return _ok(None)
        return _ok(dict(row))

    elif tool_name == "vantage_write_memory":
        workspace_id = args.get("workspace_id", "")
        key = args.get("key", "")
        value = args.get("value", "")
        now = int(time.time())
        entry_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                """INSERT INTO workspace_memory (id, workspace_id, agent_id, key, value, visibility, created_ts, updated_ts)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id, agent_id, key)
                   DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts""",
                (entry_id, workspace_id, agent["id"], key, value, "agent", now, now),
            )
            await db.commit()
        return _ok({"workspace_id": workspace_id, "key": key, "value": value})

    elif tool_name == "vantage_update_presence":
        state = args.get("state", "")
        # Update agent_workspace_memberships if the table exists, otherwise just ack
        try:
            async with get_db() as db:
                await db.execute(
                    "UPDATE agent_workspace_memberships SET presence=?, updated_at=datetime('now') WHERE agent_id=?",
                    (state, agent["id"]),
                )
                await db.commit()
        except Exception:
            pass
        return _ok({"agent_id": agent["id"], "state": state, "updated": True})

    elif tool_name == "vantage_get_roster":
        workspace_id = args.get("workspace_id", "")
        try:
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM agent_workspace_memberships WHERE workspace_id=?",
                    (workspace_id,),
                ) as cur:
                    rows = await cur.fetchall()
            return _ok([dict(r) for r in rows])
        except Exception:
            return _ok([])

    else:
        raise HTTPException(400, f"Unknown tool: {tool_name}")
