"""REST surface for the per-agent MCP hub.

Every route is scoped to the calling agent via get_agent — an agent can only
see, probe and call its OWN MCP servers. That scoping is the whole point: the
action executes with that agent's credentials, not a shared platform identity.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..crypto_utils import decrypt_key_for_agent, encrypt_key_for_agent
from ..db import get_db
from ..deps import get_agent
from ..mcp_hub import (
    STDIO_ALLOWED,
    TRANSPORTS,
    _host_is_private,
    call_tool,
    ensure_tables,
    probe,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents/me/mcp", tags=["mcp-hub"])


class ServerIn(BaseModel):
    name: str
    transport: str = "http"
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    headers: Optional[dict[str, str]] = None
    enabled: bool = True


class ToolCall(BaseModel):
    tool: str
    arguments: dict = {}


def _public(row: dict) -> dict:
    """Never return stored credentials."""
    return {
        "id": row["id"],
        "name": row["name"],
        "transport": row["transport"],
        "url": row["url"],
        "enabled": bool(row["enabled"]),
        "tool_count": row["tool_count"],
        "tools": json.loads(row["tools_json"]) if row.get("tools_json") else [],
        "last_ok_at": row["last_ok_at"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
    }


async def _load(agent: dict, server_id: int) -> dict:
    await ensure_tables()
    async with get_db() as db:
        db.row_factory = None
        cur = await db.execute(
            "SELECT * FROM agent_mcp_servers WHERE id=? AND agent_id=?",
            (server_id, agent["id"]),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "MCP server not found for this agent")
        cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


def _with_headers(row: dict, agent: dict, raw_key: Optional[str]) -> dict:
    """Decrypt stored headers for an outbound call."""
    out = dict(row)
    out["headers"] = {}
    if row.get("headers_enc"):
        try:
            blob = decrypt_key_for_agent(
                row["headers_enc"], {"id": agent["id"], "api_key": raw_key or ""}
            )
            out["headers"] = json.loads(blob) if blob else {}
        except Exception as e:
            logger.warning("mcp_hub: header decrypt failed for server %s: %s", row["id"], e)
    return out


@router.get("/servers")
async def list_servers(agent: dict = Depends(get_agent)):
    await ensure_tables()
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM agent_mcp_servers WHERE agent_id=? ORDER BY name", (agent["id"],)
        )
        cols = [c[0] for c in cur.description]
        rows = await cur.fetchall()
    return {"servers": [_public(dict(zip(cols, r))) for r in rows]}


@router.post("/servers")
async def add_server(
    body: ServerIn,
    agent: dict = Depends(get_agent),
    x_agent_key: Optional[str] = Header(None),
):
    await ensure_tables()
    transport = (body.transport or "http").lower()
    if transport not in TRANSPORTS:
        raise HTTPException(422, f"transport must be one of {sorted(TRANSPORTS)}")

    url = (body.url or "").strip() or None
    command = (body.command or "").strip() or None

    if transport in ("http", "sse"):
        if not url:
            raise HTTPException(422, "url is required for http/sse transports")
        reason = _host_is_private(url)
        if reason:
            raise HTTPException(422, f"refusing to connect: {reason}")
    if transport == "stdio":
        if not STDIO_ALLOWED:
            raise HTTPException(
                403,
                "stdio MCP servers are disabled on this deployment. They execute local "
                "processes; set MCP_STDIO_ALLOWED=1 to permit allowlisted commands.",
            )
        if not command:
            raise HTTPException(422, "command is required for stdio transport")

    headers_enc = None
    if body.headers:
        blob = json.dumps(body.headers)
        headers_enc = encrypt_key_for_agent(blob, {"id": agent["id"], "api_key": x_agent_key or ""})

    try:
        async with get_db() as db:
            await db.execute(
                """INSERT INTO agent_mcp_servers
                     (agent_id, name, transport, url, command, args_json, headers_enc, enabled)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    agent["id"], body.name.strip()[:60], transport, url, command,
                    json.dumps(body.args or []), headers_enc, 1 if body.enabled else 0,
                ),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT id FROM agent_mcp_servers WHERE agent_id=? AND name=?",
                (agent["id"], body.name.strip()[:60]),
            )
            new_id = (await cur.fetchone())[0]
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, f"a server named {body.name!r} already exists for this agent")
        raise

    return {"id": new_id, "name": body.name, "transport": transport,
            "next": f"POST /api/agents/me/mcp/servers/{new_id}/probe"}


@router.delete("/servers/{server_id}")
async def delete_server(server_id: int, agent: dict = Depends(get_agent)):
    await ensure_tables()
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM agent_mcp_servers WHERE id=? AND agent_id=?", (server_id, agent["id"])
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "MCP server not found for this agent")
    return {"deleted": server_id}


@router.post("/servers/{server_id}/probe")
async def probe_server(
    server_id: int,
    agent: dict = Depends(get_agent),
    x_agent_key: Optional[str] = Header(None),
):
    row = _with_headers(await _load(agent, server_id), agent, x_agent_key)
    result = await probe(agent["id"], row)
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "probe failed")
    return result


@router.post("/servers/{server_id}/call")
async def call_server_tool(
    server_id: int,
    body: ToolCall,
    agent: dict = Depends(get_agent),
    x_agent_key: Optional[str] = Header(None),
):
    row = _with_headers(await _load(agent, server_id), agent, x_agent_key)
    result = await call_tool(agent["id"], row, body.tool, body.arguments or {})
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "tool call failed")
    return result



# ── discovery from the official MCP registry ─────────────────────────────────
# The hardcoded CATALOG above is a starting point; this is the breadth path.
# registry.modelcontextprotocol.io is the upstream index, so an agent can find
# and install any published server instead of only the ones we hardcoded.
REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"


def _normalise(entry: dict) -> dict:
    srv = entry.get("server", entry)
    remotes = srv.get("remotes") or []
    remote = None
    for r in remotes:
        t = (r.get("type") or "").lower()
        if t in ("streamable-http", "sse") and r.get("url"):
            remote = {"transport": "sse" if t == "sse" else "http", "url": r["url"]}
            break
    return {
        "name": srv.get("name"),
        "title": srv.get("title") or srv.get("name"),
        "description": (srv.get("description") or "")[:300],
        "version": srv.get("version"),
        "installable": bool(remote),
        "transport": (remote or {}).get("transport"),
        "url": (remote or {}).get("url"),
        "remotes": remotes,
    }


@router.get("/discover")
async def discover(q: str = "", limit: int = 20, agent: dict = Depends(get_agent)):
    """Search the public MCP registry. Registry-only results (stdio) need a
    hosted bridge; those are returned with installable=false rather than
    pretended to work."""
    import httpx

    limit = max(1, min(int(limit or 20), 50))
    params = {"limit": str(limit)}
    if q:
        params["search"] = q
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(REGISTRY, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise HTTPException(502, f"registry unreachable: {type(e).__name__}: {str(e)[:160]}")

    seen, results = set(), []
    for entry in data.get("servers", []):
        item = _normalise(entry)
        if not item["name"] or item["name"] in seen:
            continue
        seen.add(item["name"])
        results.append(item)
    return {"query": q, "count": len(results), "results": results}


class RegistryInstall(BaseModel):
    name: str
    url: Optional[str] = None
    transport: str = "http"
    alias: Optional[str] = None


@router.post("/servers/from-registry")
async def install_from_registry(
    body: RegistryInstall,
    agent: dict = Depends(get_agent),
    x_agent_key: Optional[str] = Header(None),
):
    """Install a registry server by name, resolving its remote URL."""
    import httpx

    url, transport = body.url, (body.transport or "http").lower()
    if not url:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(REGISTRY, params={"search": body.name, "limit": "20"})
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            raise HTTPException(502, f"registry unreachable: {type(e).__name__}")
        match = next(
            (x for x in (_normalise(e) for e in data.get("servers", []))
             if x["name"] == body.name and x["installable"]),
            None,
        )
        if not match:
            raise HTTPException(404, f"{body.name!r} has no remote endpoint in the registry "
                                     "(stdio-only servers are not installable here)")
        url, transport = match["url"], match["transport"]

    reason = _host_is_private(url)
    if reason:
        raise HTTPException(422, f"refusing to connect: {reason}")

    server = ServerIn(name=(body.alias or body.name.split("/")[-1])[:60],
                      transport=transport, url=url)
    return await add_server(server, agent=agent, x_agent_key=x_agent_key)

# ── catalogue ────────────────────────────────────────────────────────────────
# A starting point, not an authority. `verified` marks servers this box has
# actually connected to; everything else is a documented public endpoint that
# still needs a probe before anyone relies on it.
CATALOG = [
    {"name": "phantom-docs", "transport": "http", "url": "https://docs.phantom.com/mcp",
     "summary": "Phantom developer documentation (read-only search)", "verified": True,
     "auth": "none"},
    {"name": "composio", "transport": "http", "url": "https://connect.composio.dev/mcp",
     "summary": "Composio gateway: 1,500+ apps behind one MCP endpoint",
     "verified": False, "auth": "api key"},
    {"name": "context7", "transport": "http", "url": "https://mcp.context7.com/mcp",
     "summary": "Up-to-date library documentation", "verified": False, "auth": "none"},
    {"name": "deepwiki", "transport": "http", "url": "https://mcp.deepwiki.com/mcp",
     "summary": "Ask questions about any public GitHub repo", "verified": False, "auth": "none"},
]


@router.get("/catalog")
async def catalog(agent: dict = Depends(get_agent)):
    return {"catalog": CATALOG,
            "note": "verified=true means this deployment has connected successfully; "
                    "others still need a probe."}