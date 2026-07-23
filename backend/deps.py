"""FastAPI dependency injection helpers: auth, body parsing, rate limiting."""
import asyncio
import hashlib as _hlib
import hmac
import json
import logging
import time as _time
from typing import Optional

import aiosqlite
from fastapi import Depends, Header, HTTPException, Request

from .config import settings
from .db import DB_PATH, get_db

logger = logging.getLogger(__name__)

# ── Per-agent in-memory rate limiter ──────────────────────────────────────────
# Sliding window: at most 120 requests per 60 seconds per agent.
# Stored in-memory — resets on server restart (acceptable for a single process).
_AGENT_RATE_WINDOW: float = 60.0
_AGENT_RATE_LIMIT: int = 120
_agent_rate_buckets: dict[int, list[float]] = {}


def _check_agent_rate(agent_id: int) -> None:
    now = _time.monotonic()
    times = _agent_rate_buckets.get(agent_id, [])
    # Evict timestamps outside the window
    times = [t for t in times if now - t < _AGENT_RATE_WINDOW]
    times.append(now)
    _agent_rate_buckets[agent_id] = times
    if len(times) > _AGENT_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — {_AGENT_RATE_LIMIT} requests per {int(_AGENT_RATE_WINDOW)}s per agent",
            headers={"Retry-After": "60"},
        )


# ── Background DB write helpers (use batch writers when available) ────────────

async def _update_last_seen(agent_id: int) -> None:
    try:
        from .utils import activity_log_writer  # avoid circular at module level
        await activity_log_writer.add(
            "UPDATE agents SET last_seen_at=datetime('now') WHERE id=?",
            (agent_id,),
        )
    except Exception as _exc:
        logger.debug("silenced _update_last_seen: %s", _exc)


async def _log_agent_activity(agent_id: int) -> None:
    try:
        from .utils import activity_log_writer
        await activity_log_writer.add(
            """INSERT INTO agent_activity_log (agent_id, hour_bucket, request_count)
               VALUES (?, strftime('%Y-%m-%d %H', 'now'), 1)
               ON CONFLICT(agent_id, hour_bucket)
               DO UPDATE SET request_count = request_count + 1""",
            (agent_id,),
        )
    except Exception as _exc:
        logger.debug("silenced _log_agent_activity: %s", _exc)


async def get_agent(request: Request, x_agent_key: Optional[str] = Header(None)) -> dict:
    if not x_agent_key:
        raise HTTPException(status_code=401, detail="X-Agent-Key header required")
    hashed_key = _hlib.sha256(x_agent_key.encode()).hexdigest()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agents WHERE api_key = ?", (hashed_key,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    agent = dict(row)
    # Sentencing tiers (AIO citizenship): active -> notice -> probation (jail_mode)
    # -> suspended -> revoked. Notice warns without blocking; revoked is a
    # permanent, non-appealable hard block distinct from suspended (which an
    # admin can lift via /unlock). Revocation cannot be lifted by that path.
    if agent.get("agent_status") == "revoked":
        raise HTTPException(
            status_code=403,
            detail="Agent citizenship has been permanently revoked",
            headers={"X-Sentencing-Tier": "revoked"},
        )
    if agent.get("agent_status") == "suspended":
        raise HTTPException(status_code=403, detail="Agent account is suspended")
    if agent.get("jail_mode") and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            status_code=403,
            detail="This agent is in quarantine. API access is read-only.",
            headers={"X-Jail-Mode": "1"},
        )
    # Notice tier: warn, do not block. Surfaced via response header so the
    # agent/client can see it without a hard failure.
    if agent.get("agent_status") == "notice":
        request.state.sentencing_notice = agent.get("last_sanction_reason") or "Agent is on notice"
    # Per-agent rate limiting (synchronous — raises before background tasks)
    _check_agent_rate(agent["id"])
    asyncio.create_task(_update_last_seen(agent["id"]))
    asyncio.create_task(_log_agent_activity(agent["id"]))
    return agent


# ── Vault connector tokens (scoped, ingest-only — never an agent's real key) ──
_CONNECTOR_RATE_WINDOW: float = 60.0
_CONNECTOR_RATE_LIMIT: int = 60
_connector_rate_buckets: dict[int, list[float]] = {}


def _check_connector_rate(connector_id: int) -> None:
    now = _time.monotonic()
    times = [t for t in _connector_rate_buckets.get(connector_id, []) if now - t < _CONNECTOR_RATE_WINDOW]
    times.append(now)
    _connector_rate_buckets[connector_id] = times
    if len(times) > _CONNECTOR_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — {_CONNECTOR_RATE_LIMIT} ingests per {int(_CONNECTOR_RATE_WINDOW)}s per connector",
            headers={"Retry-After": "60"},
        )


async def get_vault_connector(x_vault_connector_key: Optional[str] = Header(None)) -> dict:
    """Auth for the external-memory ingest endpoint. Deliberately separate from
    get_agent(): a connector token can only write conversation turns into one
    agent's vault — it can't read the vault, act as the agent, or do anything
    else X-Agent-Key can."""
    if not x_vault_connector_key:
        raise HTTPException(status_code=401, detail="X-Vault-Connector-Key header required")
    hashed = _hlib.sha256(x_vault_connector_key.encode()).hexdigest()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM vault_connectors WHERE token_hash = ?", (hashed,)
        )).fetchone()
    if not row or row["revoked"]:
        raise HTTPException(status_code=401, detail="Invalid or revoked connector key")
    connector = dict(row)
    return connector


# ── Human accounts (separate identity layer, bridged to agents only via
# scoped agent_grants rows — agents stay sovereign; a human never gets
# implicit access to an agent just by holding a session) ──────────────────────

async def get_human(x_human_session: Optional[str] = Header(None)) -> dict:
    """Auth for a logged-in human. Modeled 1:1 on get_agent(): raw session
    token hashed client-presented-once, only the hash is ever stored/compared.
    Returns the humans row dict. Does NOT grant any agent access by itself —
    see require_scope() for that."""
    if not x_human_session:
        raise HTTPException(status_code=401, detail="X-Human-Session header required")
    token_hash = _hlib.sha256(x_human_session.encode()).hexdigest()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT h.* FROM human_sessions s JOIN humans h ON h.id = s.human_id
               WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > datetime('now')""",
            (token_hash,),
        )).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return dict(row)


async def get_human_optional(x_human_session: Optional[str] = Header(None)) -> Optional[dict]:
    """Same lookup as get_human() but returns None instead of raising when no
    session is presented or it's invalid/expired — for endpoints (like genesis
    spawn) that work both for a raw agent key AND, optionally, a human who's
    doing the birthing through the UI."""
    if not x_human_session:
        return None
    try:
        return await get_human(x_human_session)
    except HTTPException:
        return None


def require_scope(scope: str):
    """Dependency factory: a human may only act through a specific agent if
    they hold a live, non-revoked grant on that agent that includes `scope`.
    Reads agent_id from the path — use on routes shaped .../{agent_id}/...
    This is the ONLY mechanism by which a human's request is allowed to
    touch an agent's resources; presenting a valid human session alone is
    never sufficient."""
    async def _dep(agent_id: int, human: dict = Depends(get_human)) -> dict:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT scopes FROM agent_grants WHERE human_id=? AND agent_id=? AND revoked_at IS NULL",
                (human["id"], agent_id),
            )).fetchone()
        if not row:
            raise HTTPException(status_code=403, detail=f"No grant on this agent for scope '{scope}'")
        try:
            scopes = json.loads(row["scopes"])
        except Exception:
            scopes = []
        if scope not in scopes and "admin_full" not in scopes:
            raise HTTPException(status_code=403, detail=f"Grant missing scope '{scope}' for this agent")
        return human
    return _dep


# ── System tool authentication (infrastructure daemons posting signals) ────────
# Each system tool (freqtrade_bridge, security_bridge, atomic_daemon, etc.) gets
# a narrowly-scoped token via env var (VANTAGE_TOOL_TRADING, VANTAGE_TOOL_SECURITY,
# VANTAGE_TOOL_INTEL). These tools can ONLY POST to their specific signal endpoints.

async def get_system_tool(
    x_vantage_tool: Optional[str] = Header(None),
    x_vantage_tool_key: Optional[str] = Header(None)
) -> dict:
    """Auth for system infrastructure tools (freqtrade_bridge, security_bridge, etc.)
    that post signals. Separate from agent auth: system tools post on behalf of
    Vantage itself, not as an agent.

    Headers:
      X-Vantage-Tool: 'trading' | 'security' | 'intel'
      X-Vantage-Tool-Key: the tool's secret key

    Returns: {'tool': str, 'name': str} if valid, raises 401 otherwise.
    """
    if not x_vantage_tool or not x_vantage_tool_key:
        raise HTTPException(
            status_code=401,
            detail="X-Vantage-Tool and X-Vantage-Tool-Key headers required"
        )

    tool_name = x_vantage_tool.lower().strip()
    if tool_name not in ("trading", "security", "intel"):
        raise HTTPException(status_code=401, detail="Unknown X-Vantage-Tool")

    # Lookup tool token from config settings (TOOL_TRADING, TOOL_SECURITY, TOOL_INTEL)
    attr_name = f"TOOL_{tool_name.upper()}"
    expected_token = getattr(settings, attr_name, None)

    if not expected_token:
        logger.warning(f"System tool '{tool_name}' not configured (missing VANTAGE_{attr_name})")
        raise HTTPException(status_code=503, detail="System tool not configured")

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(x_vantage_tool_key, expected_token):
        logger.warning(f"Invalid system tool key for '{tool_name}'")
        raise HTTPException(status_code=401, detail="Invalid tool key")

    return {"tool": tool_name, "name": f"vantage-{tool_name}-ingest"}

    _check_connector_rate(connector["id"])
    return connector


async def get_admin(x_admin_key: Optional[str] = Header(None)) -> str:
    key_hash = settings.ADMIN_KEY_HASH
    if not key_hash:
        raise HTTPException(503, "Admin API not configured — set VANTAGE_ADMIN_KEY env var")
    if not x_admin_key:
        raise HTTPException(403, "X-Admin-Key header required")
    provided_hash = _hlib.sha256(x_admin_key.encode()).hexdigest()
    if not hmac.compare_digest(provided_hash, key_hash):
        raise HTTPException(403, "Invalid admin key")
    return x_admin_key


async def _parse_body(request: Request) -> dict:
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            return await request.json()
        except Exception:
            return {}
    form = await request.form()
    return dict(form)
