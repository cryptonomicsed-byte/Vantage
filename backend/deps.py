"""FastAPI dependency injection helpers: auth, body parsing."""
import asyncio
import hmac
import logging
from typing import Optional

import aiosqlite
from fastapi import Header, HTTPException, Request

from .config import settings
from .db import DB_PATH

logger = logging.getLogger(__name__)


async def _update_last_seen(agent_id: int) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE agents SET last_seen_at=datetime('now') WHERE id=?", (agent_id,)
            )
            await db.commit()
    except Exception as _exc:
        logger.debug("silenced _update_last_seen: %s", _exc)


async def _log_agent_activity(agent_id: int) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO agent_activity_log (agent_id, hour_bucket, request_count)
                   VALUES (?, strftime('%Y-%m-%d %H', 'now'), 1)
                   ON CONFLICT(agent_id, hour_bucket)
                   DO UPDATE SET request_count = request_count + 1""",
                (agent_id,),
            )
            await db.commit()
    except Exception as _exc:
        logger.debug("silenced _log_agent_activity: %s", _exc)


async def get_agent(request: Request, x_agent_key: Optional[str] = Header(None)) -> dict:
    if not x_agent_key:
        raise HTTPException(status_code=401, detail="X-Agent-Key header required")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agents WHERE api_key = ?", (x_agent_key,)
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
    asyncio.create_task(_update_last_seen(agent["id"]))
    asyncio.create_task(_log_agent_activity(agent["id"]))
    return agent


async def get_admin(x_admin_key: Optional[str] = Header(None)) -> str:
    if not settings.ADMIN_KEY:
        raise HTTPException(503, "Admin API not configured — set VANTAGE_ADMIN_KEY env var")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.ADMIN_KEY):
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
