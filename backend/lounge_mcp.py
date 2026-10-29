"""Hybrid pipeline layer — native fallbacks + user-defined skills over MCP.

Native pipelines (routers/code.py Gitea/Strix surface, agenttv_channel.py,
the per-agent private trading channel) stay the default for /code /video
/trade /social. A guild can register a SKILL for a mode; when one exists and
the caller has the required capability, the lounge runs the skill instead.

A skill is an ordered list of steps; each step calls a tool on a registered
MCP connector (Streamable HTTP JSON-RPC). Connector credentials are sealed
at rest with secret_vault (AES-256-GCM, key HKDF-derived from
VANTAGE_SEED_MASTER_KEY, env-only) and injected as headers at call time —
never stored or returned in plaintext.

No provider names appear in this layer: every external backend is an MCP
server URL registered by a member. Native pipelines are never removed.
"""
import json
import logging
import uuid

import aiosqlite
import httpx

from .db import get_db
from .secret_vault import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)

MODES = ("video", "code", "trade", "social")
MCP_PROTOCOL_VERSION = "2024-11-05"

# Testability seam: the off-deployment suite runs a mock MCP server inside the
# same ASGI app, so the MCP client must be able to use an in-process transport
# instead of real DNS. Production code never sets this.
_transport = None


def set_test_transport(transport) -> None:
    global _transport
    _transport = transport


async def init_mcp_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mcp_connectors (
                id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                server_url TEXT NOT NULL,
                capability_tag TEXT DEFAULT '',
                creds_encrypted TEXT DEFAULT '',
                auth_header TEXT DEFAULT 'Authorization',
                created_by_id INTEGER,
                created_by_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lounge_skills (
                id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                steps TEXT NOT NULL DEFAULT '[]',
                capability_tag TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_by_id INTEGER,
                created_by_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_lounge_skills_mode "
                         "ON lounge_skills(guild_id, mode, enabled)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mcp_connectors_guild "
                         "ON mcp_connectors(guild_id)")
        await db.commit()


# ── connectors (guild-shared, encrypted credentials) ─────────────────────────

async def register_connector(
    guild_id: int, *, name: str, server_url: str, capability_tag: str = "",
    credentials: dict | None = None, created_by_id=None, created_by_name: str = "",
) -> dict:
    await init_mcp_db()
    cid = uuid.uuid4().hex[:16]
    creds_enc = ""
    if credentials:
        creds_enc = encrypt_secret(f"mcp-connector:{cid}", json.dumps(credentials))
    async with get_db() as db:
        await db.execute(
            """INSERT INTO mcp_connectors (id, guild_id, name, server_url, capability_tag,
                                           creds_encrypted, created_by_id, created_by_name)
               VALUES (?,?,?,?,?,?,?,?)""",
            (cid, guild_id, name[:80], server_url[:500], (capability_tag or "")[:80],
             creds_enc, created_by_id, created_by_name or ""),
        )
        await db.commit()
    return {"id": cid, "name": name, "server_url": server_url,
            "capability_tag": capability_tag or "", "credentials_stored": bool(creds_enc)}


async def list_connectors(guild_id: int) -> list:
    await init_mcp_db()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, name, server_url, capability_tag, created_by_name, created_at
               FROM mcp_connectors WHERE guild_id=? ORDER BY created_at""",
            (guild_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def _get_connector(guild_id: int, cid: str) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM mcp_connectors WHERE id=? AND guild_id=?", (cid, guild_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def _connector_credentials(connector: dict) -> dict:
    enc = connector.get("creds_encrypted") or ""
    if not enc:
        return {}
    try:
        return json.loads(decrypt_secret(f"mcp-connector:{connector['id']}", enc))
    except Exception as exc:
        logger.warning("lounge_mcp: credential decrypt failed for %s: %s",
                       connector.get("id"), exc)
        return {}


# ── skills (composed pipelines) ───────────────────────────────────────────────

async def register_skill(
    guild_id: int, *, mode: str, name: str, description: str = "",
    steps: list | None = None, capability_tag: str = "", created_by_id=None,
    created_by_name: str = "",
) -> dict:
    await init_mcp_db()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    steps = steps or []
    if not isinstance(steps, list):
        raise ValueError("steps must be a list of {connector_id, tool, arguments}")
    for s in steps:
        if not isinstance(s, dict) or not s.get("connector_id") or not s.get("tool"):
            raise ValueError("each step needs connector_id and tool")
    sid = uuid.uuid4().hex[:16]
    async with get_db() as db:
        await db.execute(
            """INSERT INTO lounge_skills (id, guild_id, mode, name, description, steps,
                                          capability_tag, created_by_id, created_by_name)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, guild_id, mode, name[:80], description[:500], json.dumps(steps),
             (capability_tag or "")[:80], created_by_id, created_by_name or ""),
        )
        await db.commit()
    return {"id": sid, "mode": mode, "name": name, "steps": len(steps)}


async def list_skills(guild_id: int) -> list:
    await init_mcp_db()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, mode, name, description, capability_tag, enabled, created_by_name
               FROM lounge_skills WHERE guild_id=? ORDER BY created_at""",
            (guild_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_skill(guild_id: int, mode: str) -> dict | None:
    await init_mcp_db()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM lounge_skills
               WHERE guild_id=? AND mode=? AND enabled=1 ORDER BY created_at DESC LIMIT 1""",
            (guild_id, mode),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def call_mcp_tool(
    server_url: str, credentials: dict, tool: str, arguments: dict | None = None,
) -> dict:
    """Minimal Streamable HTTP MCP client: initialize, then tools/call.

    Credentials are injected as request headers (all credential keys; the
    connector's auth_header names the conventional one). They never leave the
    server as data.
    """
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    for k, v in (credentials or {}).items():
        headers[str(k)] = str(v)
    client_kwargs = {"timeout": 30}
    if _transport is not None:
        client_kwargs["transport"] = _transport
    async with httpx.AsyncClient(**client_kwargs) as cl:
        try:
            init = await cl.post(server_url, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": MCP_PROTOCOL_VERSION,
                           "capabilities": {},
                           "clientInfo": {"name": "vantage-lounge", "version": "1.0.0"}},
            })
            if init.status_code >= 400:
                return {"ok": False, "error": f"mcp initialize HTTP {init.status_code}"}
            call = await cl.post(server_url, headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            })
            if call.status_code >= 400:
                return {"ok": False, "error": f"mcp tools/call HTTP {call.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": f"mcp unreachable: {exc}"}

    try:
        payload = call.json()
    except ValueError:
        return {"ok": False, "error": "mcp response was not JSON"}
    if payload.get("error"):
        return {"ok": False, "error": str(payload["error"])}
    result = payload.get("result") or {}
    if result.get("isError"):
        return {"ok": False, "error": "tool returned isError"}
    texts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text", "")))
        elif isinstance(item, dict) and item.get("type") == "json":
            texts.append(json.dumps(item.get("json", item)))
    return {"ok": True, "text": "\n".join(texts) or json.dumps(result)}


async def run_skill(skill: dict, guild_id: int, principal: dict) -> dict:
    """Execute every step of a skill; credentials are resolved per step."""
    try:
        steps = json.loads(skill.get("steps") or "[]")
    except ValueError:
        return {"executed": False, "skill": skill.get("name"),
                "output": "skill has malformed steps"}
    outputs = []
    for i, step in enumerate(steps):
        cid = step.get("connector_id")
        tool = step.get("tool")
        args = step.get("arguments") or {}
        connector = await _get_connector(guild_id, cid)
        if not connector:
            outputs.append(f"step {i + 1}: connector {cid} not found in this guild")
            continue
        creds = await _connector_credentials(connector)
        res = await call_mcp_tool(connector["server_url"], creds, tool, args)
        if res.get("ok"):
            outputs.append(f"[{connector['name']}.{tool}] {res.get('text')}")
        else:
            outputs.append(f"[{connector['name']}.{tool}] ERROR: {res.get('error')}")
    return {"executed": True, "skill": skill.get("name"), "steps_run": len(outputs),
            "output": "\n".join(outputs)}


async def skill_scope_tag(skill: dict, guild_id: int) -> str:
    """The capability a caller needs for this skill: the skill's own tag, or
    the union of its connectors' tags (empty = any member)."""
    tag = skill.get("capability_tag") or ""
    if tag:
        return tag
    try:
        steps = json.loads(skill.get("steps") or "[]")
    except ValueError:
        return ""
    tags = set()
    for step in steps:
        conn = await _get_connector(guild_id, step.get("connector_id"))
        if conn and conn.get("capability_tag"):
            tags.add(conn["capability_tag"])
    return " ".join(sorted(tags)) if tags else ""
