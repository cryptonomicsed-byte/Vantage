"""Per-agent MCP client hub.

Vantage already speaks MCP as a *server* (agents call Vantage). This is the
other direction: each agent connects its OWN MCP servers, with its own
credentials, so an action executes as that agent's account rather than as one
shared platform identity.

Design decisions that matter:

* **Per-agent rows, not global.** `agent_mcp_servers.agent_id` is the tenancy
  boundary. Two agents can hold the same server with different tokens and the
  tool calls never cross.
* **SSRF guard.** An agent is untrusted input. Without a check, "connect my MCP
  server" is a way to make the platform issue authenticated requests to
  localhost, 169.254.169.254, or any internal service. http/sse URLs are
  resolved and private ranges rejected.
* **stdio is OFF by default.** A stdio MCP server is an arbitrary local process
  (`npx @some/mcp`), i.e. remote code execution for whoever can POST a server.
  It is supported only when MCP_STDIO_ALLOWED=1 AND the agent passes an
  allowlisted command.
* **Lazy schema init.** This app passes an explicit `lifespan=` to FastAPI, so
  `@app.on_event("startup")` never fires; table creation must not depend on it.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from .db import get_db

logger = logging.getLogger(__name__)

_TABLES_READY = False
_TABLE_LOCK = asyncio.Lock()

TRANSPORTS = {"http", "sse", "stdio"}
PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
    )
]

STDIO_ALLOWED = os.environ.get("MCP_STDIO_ALLOWED", "0") == "1"
STDIO_COMMANDS = {"npx", "uvx", "node", "python3", "python"}


async def ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    async with _TABLE_LOCK:
        if _TABLES_READY:
            return
        async with get_db() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_mcp_servers (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id     INTEGER NOT NULL,
                    name         TEXT    NOT NULL,
                    transport    TEXT    NOT NULL DEFAULT 'http',
                    url          TEXT,
                    command      TEXT,
                    args_json    TEXT,
                    headers_enc  TEXT,
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    tools_json   TEXT,
                    tool_count   INTEGER NOT NULL DEFAULT 0,
                    last_ok_at   TEXT,
                    last_error   TEXT,
                    created_at   TEXT DEFAULT (datetime('now')),
                    updated_at   TEXT DEFAULT (datetime('now')),
                    UNIQUE(agent_id, name)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mcp_servers_agent ON agent_mcp_servers(agent_id)"
            )
            await db.commit()
        _TABLES_READY = True


def _host_is_private(url: str) -> Optional[str]:
    """Return a reason string when the URL targets a non-public address."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "unparseable url"
    if parsed.scheme not in ("http", "https"):
        return f"scheme {parsed.scheme!r} not allowed (http/https only)"
    host = parsed.hostname
    if not host:
        return "url has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"host {host!r} does not resolve"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        for net in PRIVATE_NETS:
            if ip in net:
                return f"host resolves to private address {addr}"
    return None



def _unwrap(exc: BaseException) -> BaseException:
    """Dig the real cause out of an ExceptionGroup.

    The MCP SDK runs transports inside anyio task groups, so a plain HTTP 401
    surfaces as "ExceptionGroup: unhandled errors in a TaskGroup (1
    sub-exception)" -- useless to an agent trying to fix its config. Walk to
    the first leaf that is not a group.
    """
    seen = set()
    while True:
        subs = getattr(exc, "exceptions", None)
        if not subs or id(exc) in seen:
            return exc
        seen.add(id(exc))
        exc = subs[0]


def _err_text(exc: BaseException) -> str:
    leaf = _unwrap(exc)
    msg = str(leaf) or repr(leaf)
    return f"{type(leaf).__name__}: {msg[:300]}"


@asynccontextmanager
async def _open_session(server: dict):
    """Yield a live ClientSession for a stored server row."""
    from mcp import ClientSession

    transport = (server.get("transport") or "http").lower()
    url = server.get("url") or ""
    headers = server.get("headers") or {}

    if transport == "http":
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url, headers=headers or None) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    if transport == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(url, headers=headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    if transport == "stdio":
        if not STDIO_ALLOWED:
            raise PermissionError(
                "stdio MCP servers are disabled on this deployment "
                "(they execute local processes). Set MCP_STDIO_ALLOWED=1 to enable."
            )
        cmd = (server.get("command") or "").strip()
        base = os.path.basename(cmd)
        if base not in STDIO_COMMANDS:
            raise PermissionError(f"stdio command {base!r} is not allowlisted")
        from mcp.client.stdio import StdioServerParameters, stdio_client

        args = json.loads(server.get("args_json") or "[]")
        params = StdioServerParameters(command=cmd, args=args, env=None)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    raise ValueError(f"unknown transport {transport!r}")


def _tool_dict(t) -> dict:
    return {
        "name": t.name,
        "description": (t.description or "")[:600],
        "input_schema": getattr(t, "inputSchema", None) or {},
    }


async def probe(agent_id: int, server: dict, timeout: float = 25.0) -> dict:
    """Connect and list tools. Stores the result on the row."""
    await ensure_tables()
    try:
        async def _run():
            async with _open_session(server) as session:
                tools = await session.list_tools()
                return [_tool_dict(t) for t in tools.tools]

        tools = await asyncio.wait_for(_run(), timeout=timeout)
        async with get_db() as db:
            await db.execute(
                "UPDATE agent_mcp_servers SET tools_json=?, tool_count=?, last_ok_at=datetime('now'),"
                " last_error=NULL, updated_at=datetime('now') WHERE id=? AND agent_id=?",
                (json.dumps(tools), len(tools), server["id"], agent_id),
            )
            await db.commit()
        return {"ok": True, "tools": tools, "count": len(tools)}
    except Exception as e:
        msg = _err_text(e)
        async with get_db() as db:
            await db.execute(
                "UPDATE agent_mcp_servers SET last_error=?, updated_at=datetime('now')"
                " WHERE id=? AND agent_id=?",
                (msg, server["id"], agent_id),
            )
            await db.commit()
        return {"ok": False, "error": msg}


async def call_tool(agent_id: int, server: dict, tool: str, arguments: dict,
                    timeout: float = 45.0) -> dict:
    """Execute one tool on one agent's server."""
    await ensure_tables()
    try:
        async def _run():
            async with _open_session(server) as session:
                return await session.call_tool(tool, arguments or {})

        result = await asyncio.wait_for(_run(), timeout=timeout)
        content = []
        for item in (result.content or []):
            text = getattr(item, "text", None)
            content.append({"type": getattr(item, "type", "unknown"),
                            "text": text if text is not None else str(item)[:2000]})
        return {
            "ok": not getattr(result, "isError", False),
            "tool": tool,
            "content": content,
            "structured": getattr(result, "structuredContent", None),
        }
    except Exception as e:
        return {"ok": False, "tool": tool, "error": _err_text(e)}