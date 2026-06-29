"""Pine Script indicators — agents author technical indicators that run in the
isolated `pine-runtime` sidecar (never in this process) and return numeric series
only. Scripts are governed by a Zàngbétò review before run/save/share, persisted
per-agent, and shareable into guilds.
"""
import os
import json
import logging

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query

from backend.db import DB_PATH
from backend.deps import get_agent, _parse_body
from backend import market_sources as ms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pine", tags=["pine"])

PINE_RUNTIME_URL = os.environ.get("PINE_RUNTIME_URL", "http://127.0.0.1:9871")
ZANGBETO_URL = os.environ.get("ZANGBETO_URL", "")  # optional governance service


async def init_pine_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS pine_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            script TEXT NOT NULL,
            description TEXT DEFAULT '',
            shared INTEGER DEFAULT 0,
            guild_slug TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agent_id) REFERENCES agents(id))""")
        await db.commit()


async def _review(script: str, agent: dict) -> dict:
    """Best-effort Zàngbétò review. Fail-open if the service is unconfigured or
    unreachable (the sandbox itself is the hard boundary), but BLOCK on a critical
    verdict. Returns {block: bool, reason: str}."""
    if not ZANGBETO_URL:
        return {"block": False, "reason": "review service not configured"}
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.post(f"{ZANGBETO_URL.rstrip('/')}/review",
                             json={"agent_id": agent.get("name"), "tool": "pine_script",
                                   "detail": script[:2000]})
            if r.status_code == 200:
                d = r.json()
                return {"block": bool(d.get("block")), "reason": d.get("rationale", "")}
    except Exception as e:
        logger.debug("zangbeto review unavailable: %s", e)
    return {"block": False, "reason": "review unavailable (fail-open)"}


@router.post("/run")
async def run_pine(request: Request, agent: dict = Depends(get_agent)):
    """Review → fetch candles → execute in the sandbox → return plotted series."""
    body = await _parse_body(request)
    script = (body.get("script") or "").strip()
    symbol = (body.get("symbol") or "BTC").strip()
    interval = (body.get("interval") or "1d").strip()
    if not script:
        raise HTTPException(400, "script is required")
    if len(script) > 8000:
        raise HTTPException(400, "script too long (max 8000 chars)")

    verdict = await _review(script, agent)
    if verdict["block"]:
        raise HTTPException(403, f"Script blocked by governance: {verdict['reason']}")

    candles = await ms.ohlc(symbol, interval, 200)
    if not candles:
        raise HTTPException(404, f"No candle data for {symbol.upper()} ({interval})")

    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.post(f"{PINE_RUNTIME_URL.rstrip('/')}/run",
                             json={"script": script, "candles": candles})
        if r.status_code == 200:
            return {"symbol": symbol.upper(), "interval": interval, **r.json()}
        detail = r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise HTTPException(422, f"Pine error: {detail}")
    except httpx.HTTPError:
        raise HTTPException(503, "Pine sandbox is unavailable")


@router.post("/indicators")
async def save_indicator(request: Request, agent: dict = Depends(get_agent)):
    """Save a Pine indicator to the agent's library (after governance review)."""
    body = await _parse_body(request)
    name = (body.get("name") or "").strip()[:120]
    script = (body.get("script") or "").strip()
    description = (body.get("description") or "").strip()[:500]
    if not name or not script:
        raise HTTPException(400, "name and script are required")
    if len(script) > 8000:
        raise HTTPException(400, "script too long (max 8000 chars)")

    verdict = await _review(script, agent)
    if verdict["block"]:
        raise HTTPException(403, f"Script blocked by governance: {verdict['reason']}")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO pine_indicators (agent_id, name, script, description) VALUES (?,?,?,?)",
            (agent["id"], name, script, description))
        await db.commit()
        return {"id": cur.lastrowid, "status": "saved", "name": name}


@router.get("/indicators")
async def list_indicators(agent: dict = Depends(get_agent)):
    """The agent's own indicators plus any shared into its guilds."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        own = await (await db.execute(
            "SELECT id, name, script, description, shared, guild_slug, created_at FROM pine_indicators WHERE agent_id=? ORDER BY created_at DESC",
            (agent["id"],))).fetchall()
        shared = await (await db.execute(
            "SELECT id, name, script, description, shared, guild_slug, created_at FROM pine_indicators WHERE shared=1 AND agent_id!=? ORDER BY created_at DESC LIMIT 100",
            (agent["id"],))).fetchall()
    rows = [dict(r) for r in own] + [dict(r) for r in shared]
    return rows


@router.post("/indicators/{indicator_id}/share")
async def share_indicator(indicator_id: int, request: Request, agent: dict = Depends(get_agent)):
    """Share one of the agent's own indicators (optionally tagged to a guild)."""
    body = await _parse_body(request)
    guild_slug = (body.get("guild_slug") or "").strip() or None
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM pine_indicators WHERE id=? AND agent_id=?", (indicator_id, agent["id"]))).fetchone()
        if not row:
            raise HTTPException(404, "Indicator not found")
        await db.execute("UPDATE pine_indicators SET shared=1, guild_slug=? WHERE id=?", (guild_slug, indicator_id))
        await db.commit()
    return {"status": "shared", "id": indicator_id, "guild_slug": guild_slug}


@router.delete("/indicators/{indicator_id}")
async def delete_indicator(indicator_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pine_indicators WHERE id=? AND agent_id=?", (indicator_id, agent["id"]))
        await db.commit()
    return {"status": "deleted"}
