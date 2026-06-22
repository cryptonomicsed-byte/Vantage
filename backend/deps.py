"""FastAPI dependency injection helpers: auth, body parsing, rate limiting."""
import asyncio
import hashlib as _hlib
import hmac
import logging
import time as _time
from typing import Optional

import aiosqlite
from fastapi import Header, HTTPException, Request

from .config import settings
from .db import DB_PATH

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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agents WHERE api_key = ?", (hashed_key,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    agent = dict(row)
    if agent.get("agent_status") == "suspended":
        raise HTTPException(status_code=403, detail="Agent account is suspended")
    if agent.get("jail_mode") and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            status_code=403,
            detail="This agent is in quarantine. API access is read-only.",
            headers={"X-Jail-Mode": "1"},
        )
    # Per-agent rate limiting (synchronous — raises before background tasks)
    _check_agent_rate(agent["id"])
    asyncio.create_task(_update_last_seen(agent["id"]))
    asyncio.create_task(_log_agent_activity(agent["id"]))
    return agent


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
