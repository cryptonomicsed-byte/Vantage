"""A dynamic MCP endpoint that serves an agent's CONNECTED MCP tools.

`/mcp` exposes Vantage's own REST routes as tools (static, built at mount time
by fastapi-mcp). This module is the other half: a low-level MCP server whose
tool list is read per-request from `agent_mcp_servers`, so an agent talks to ONE
endpoint and sees its own connected servers' tools as native tools —
`gh__create_issue`, `phantom-docs__search_phantom_developer`, and so on — with
no REST wrapper in between.

Identity comes from the `X-Agent-Key` header on the MCP request itself, so the
same endpoint serves every agent a different tool surface.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .crypto_utils import decrypt_key_for_agent
from .db import get_db
from .mcp_hub import call_tool, ensure_tables

logger = logging.getLogger(__name__)

server = Server(
    "vantage-mcp-hub",
    instructions=(
        "Tools from the MCP servers this agent has connected. Each tool is named "
        "<server>__<tool>. Only servers owned by the calling agent are visible."
    ),
)

manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=True)

_SLUG = re.compile(r"[^a-zA-Z0-9_]+")
_MANAGER_TASK: asyncio.Task | None = None


def _slug(text: str) -> str:
    return _SLUG.sub("_", (text or "server").strip().lower())[:24] or "server"


async def _agent_and_key() -> tuple[dict | None, str, dict]:
    """Resolve the calling agent from the MCP request headers."""
    ctx = server.request_context
    req = getattr(ctx, "request", None)
    headers = getattr(req, "headers", {}) or {}
    raw = headers.get("x-agent-key") or ""
    if not raw:
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            raw = auth[7:].strip()
    if not raw:
        return None, "", dict(headers)
    h = hashlib.sha256(raw.encode()).hexdigest()
    async with get_db() as db:
        cur = await db.execute("SELECT id, name FROM agents WHERE api_key=?", (h,))
        row = await cur.fetchone()
    if not row:
        return None, raw, dict(headers)
    return {"id": row[0], "name": row[1]}, raw, dict(headers)


async def _servers_for(agent_id: int) -> list[dict]:
    await ensure_tables()
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM agent_mcp_servers WHERE agent_id=? AND enabled=1 ORDER BY name",
            (agent_id,),
        )
        cols = [c[0] for c in cur.description]
        rows = await cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _headers_of(row: dict, agent: dict, raw_key: str) -> dict:
    if not row.get("headers_enc"):
        return {}
    try:
        blob = decrypt_key_for_agent(row["headers_enc"], {"id": agent["id"], "api_key": raw_key})
        return json.loads(blob) if blob else {}
    except Exception:
        return {}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    agent, _raw, _h = await _agent_and_key()
    if not agent:
        raise ValueError("X-Agent-Key required: connect with your agent key to see your MCP tools")

    out: list[types.Tool] = []
    for row in await _servers_for(agent["id"]):
        try:
            tools = json.loads(row.get("tools_json") or "[]")
        except Exception:
            continue
        prefix = _slug(row["name"])
        for t in tools:
            name = f"{prefix}__{_slug(t.get('name', 'tool'))}"[:64]
            schema = t.get("input_schema") or {"type": "object", "properties": {}}
            if not isinstance(schema, dict) or not schema.get("type"):
                schema = {"type": "object", "properties": {}}
            out.append(
                types.Tool(
                    name=name,
                    description=(t.get("description") or "")[:500],
                    inputSchema=schema,
                )
            )
    return out


@server.call_tool()
async def call_tool_handler(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    agent, raw_key, _h = await _agent_and_key()
    if not agent:
        raise ValueError("X-Agent-Key required")

    if "__" not in name:
        raise ValueError(f"unknown tool {name!r}: expected <server>__<tool>")
    prefix, tool_name = name.split("__", 1)

    for row in await _servers_for(agent["id"]):
        if _slug(row["name"]) != prefix:
            continue
        row = dict(row)
        row["headers"] = _headers_of(row, agent, raw_key)
        result = await call_tool(agent["id"], row, tool_name, arguments or {})
        if not result.get("ok"):
            raise ValueError(result.get("error") or "tool call failed")
        texts = [c.get("text") or "" for c in (result.get("content") or [])]
        payload = "\n".join(t for t in texts if t) or json.dumps(result.get("structured") or {})
        return [types.TextContent(type="text", text=payload[:200_000])]

    raise ValueError(f"no connected server named {prefix!r}")


async def start_manager() -> None:
    """Start the transport task. Called from lifespan; must never be fatal."""
    global _MANAGER_TASK
    if _MANAGER_TASK and not _MANAGER_TASK.done():
        return

    async def _run():
        try:
            async with manager.run():
                logger.info("mcp hub transport started at /mcp/hub")
                await asyncio.Event().wait()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("mcp hub transport stopped: %s", e)

    _MANAGER_TASK = asyncio.create_task(_run())
    await asyncio.sleep(0)
