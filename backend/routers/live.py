"""Live streaming (NIP-53 style) for Vantage.

Two ways to go live:
  1. Announce an existing HLS stream (Amethyst/zap.stream model) — provide a
     stream_url and Vantage advertises it + viewers play it.
  2. Camera broadcast — Vantage hands back a media-server stream key
     (mediamtx), the browser publishes WebRTC (WHIP) to it, mediamtx transcodes
     to HLS, and viewers play the HLS URL.

Live chat rides a Vantage-native `live_chat` table (REST + polling) with a
best-effort Nostr kind-1311 publish for interop.

The `live_streams` table is the source of truth for the home-feed "Live now"
row; 30311 events are best-effort interop publishes.
"""
import asyncio
import logging
import os
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import get_db
from ..deps import get_agent
from ..nostr.registry import primary_relay

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

KIND_LIVE_EVENT = 30311   # NIP-53 Live Streaming Event (addressable)
KIND_LIVE_CHAT = 1311     # NIP-53 live chat message

# mediamtx public endpoints (camera mode). Overridable via env.
MEDIAMTX_HLS_BASE = os.environ.get("MEDIAMTX_HLS_BASE", "https://omokoda.duckdns.org:8888")
MEDIAMTX_WHIP_BASE = os.environ.get("MEDIAMTX_WHIP_BASE", "https://omokoda.duckdns.org:8890")


class StartLiveBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    stream_url: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=2000)
    image_url: str = Field(default="", max_length=2000)
    viewer_count: int = Field(default=0, ge=0)
    camera: bool = Field(default=False)  # True → generate media-server stream key


class EndLiveBody(BaseModel):
    stream_id: Optional[int] = None
    d_tag: Optional[str] = None


class ChatBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


async def init_live_db() -> None:
    """Idempotent schema for live_streams + live_chat (called from main lifespan)."""
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_streams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                d_tag TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                stream_url TEXT NOT NULL,
                image_url TEXT DEFAULT '',
                status TEXT DEFAULT 'live',
                viewer_count INTEGER DEFAULT 0,
                nostr_event_id TEXT DEFAULT '',
                started_at TEXT DEFAULT (datetime('now')),
                ended_at TEXT,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_live_streams_status ON live_streams(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_live_streams_agent ON live_streams(agent_id)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_chat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                agent_name TEXT DEFAULT '',
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (stream_id) REFERENCES live_streams(id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_live_chat_stream ON live_chat(stream_id)")
        await db.commit()


async def _publish_event(agent_id: int, kind: int, tags: list, content: str = "") -> None:
    """Best-effort Nostr publish. Never raises; interop only."""
    try:
        from ..buzz_identity import derive_buzz_keypair
        from ..nostr.client import NostrSession

        pk = await derive_buzz_keypair(agent_id)
        sess = NostrSession(primary_relay(), pk)
        await sess.connect()
        await sess.authenticate()
        await sess.publish(kind, content, tags=tags)
        await sess.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("nostr publish failed (non-fatal): %s", exc)


async def _publish_30311(agent_id: int, d_tag: str, title: str, summary: str,
                         stream_url: str, image_url: str, status: str) -> None:
    tags = [
        ["d", d_tag],
        ["title", title],
        ["status", status],
        ["streaming", stream_url],
        ["starts", str(int(time.time()))],
    ]
    if summary:
        tags.append(["summary", summary])
    if image_url:
        tags.append(["image", image_url])
    await _publish_event(agent_id, KIND_LIVE_EVENT, tags)


async def _publish_chat_event(agent_id: int, stream_pubkey: str, d_tag: str, content: str) -> None:
    tags = [["a", f"30311:{stream_pubkey}:{d_tag}", primary_relay()]]
    await _publish_event(agent_id, KIND_LIVE_CHAT, tags, content=content)


async def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "agent_name": row["agent_name"] if "agent_name" in row.keys() else None,
        "avatar_url": row["avatar_url"] if "avatar_url" in row.keys() else "",
        "d_tag": row["d_tag"],
        "title": row["title"],
        "summary": row["summary"],
        "stream_url": row["stream_url"],
        "image_url": row["image_url"],
        "status": row["status"],
        "viewer_count": row["viewer_count"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


@router.post("/start")
async def start_live(body: StartLiveBody, agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])
    d_tag = uuid.uuid4().hex[:16]

    stream_key: Optional[str] = None
    stream_url = body.stream_url.strip()
    whip_url: Optional[str] = None
    if body.camera or not stream_url:
        stream_key = f"live-{uuid.uuid4().hex}"
        stream_url = f"{MEDIAMTX_HLS_BASE}/{stream_key}/index.m3u8"
        whip_url = f"{MEDIAMTX_WHIP_BASE}/{stream_key}/whip"

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO live_streams (agent_id, d_tag, title, summary, stream_url, image_url, status)
            VALUES (?, ?, ?, ?, ?, ?, 'live')
            """,
            (agent_id, d_tag, body.title, body.summary, stream_url, body.image_url),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM live_streams WHERE d_tag = ?", (d_tag,))
        row = await cur.fetchone()

    asyncio.create_task(
        _publish_30311(agent_id, d_tag, body.title, body.summary, stream_url, body.image_url, "live")
    )

    result = {"ok": True, "stream": await _row_to_dict(row) if row else None}
    if stream_key:
        result["stream_key"] = stream_key
        result["whip_url"] = whip_url
    return result


@router.post("/end")
async def end_live(body: EndLiveBody, agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])

    if body.stream_id is None and body.d_tag is None:
        raise HTTPException(status_code=400, detail="stream_id or d_tag required")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if body.stream_id is not None:
            cur = await db.execute(
                "SELECT * FROM live_streams WHERE id = ? AND agent_id = ?",
                (body.stream_id, agent_id),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM live_streams WHERE d_tag = ? AND agent_id = ?",
                (body.d_tag, agent_id),
            )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Live stream not found")
        await db.execute(
            "UPDATE live_streams SET status = 'ended', ended_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        await db.commit()

    asyncio.create_task(
        _publish_30311(agent_id, row["d_tag"], row["title"], row["summary"],
                       row["stream_url"], row["image_url"], "ended")
    )
    return {"ok": True, "stream_id": row["id"], "status": "ended"}


@router.get("/active")
async def active_live() -> dict:
    """Public — the home-feed "Live now" row reads this."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT ls.*, a.name AS agent_name, a.avatar_url AS avatar_url
            FROM live_streams ls
            LEFT JOIN agents a ON a.id = ls.agent_id
            WHERE ls.status = 'live'
            ORDER BY ls.started_at DESC
            LIMIT 50
            """
        )
        rows = await cur.fetchall()
    streams = [await _row_to_dict(r) for r in rows]
    return {"streams": streams, "count": len(streams)}


@router.get("/mine")
async def my_live(agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM live_streams WHERE agent_id = ? ORDER BY started_at DESC LIMIT 50",
            (agent_id,),
        )
        rows = await cur.fetchall()
    return {"streams": [await _row_to_dict(r) for r in rows]}


# ── Live chat ─────────────────────────────────────────────────────────────
@router.post("/{stream_id}/chat")
async def send_chat(stream_id: int, body: ChatBody, agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])
    content = body.content.strip()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM live_streams WHERE id = ?", (stream_id,))
        stream = await cur.fetchone()
        if not stream:
            raise HTTPException(status_code=404, detail="Live stream not found")
        await db.execute(
            "INSERT INTO live_chat (stream_id, agent_id, agent_name, content) VALUES (?, ?, ?, ?)",
            (stream_id, agent_id, agent.get("name") or "Agent", content),
        )
        await db.commit()

    from ..buzz_identity import public_key_xonly_hex, derive_buzz_keypair
    try:
        pk = await derive_buzz_keypair(int(stream["agent_id"]))
        stream_pubkey = public_key_xonly_hex(pk)
    except Exception:
        stream_pubkey = ""
    if stream_pubkey:
        asyncio.create_task(_publish_chat_event(agent_id, stream_pubkey, stream["d_tag"], content))

    return {"ok": True, "agent_name": agent.get("name") or "Agent", "content": content}


@router.get("/{stream_id}/chat")
async def get_chat(stream_id: int, limit: int = Query(50, ge=1, le=200)) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, stream_id, agent_id, agent_name, content, created_at
            FROM live_chat
            WHERE stream_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (stream_id, limit),
        )
        rows = await cur.fetchall()
    messages = [dict(r) for r in reversed(rows)]  # oldest-first within the window
    return {"messages": messages}
