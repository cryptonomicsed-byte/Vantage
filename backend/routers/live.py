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
import base64
import hashlib
import hmac
import logging
import os
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
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

# ── TURN (coturn, use-auth-secret / TURN REST API) ────────────────────────
# Camera publish (WHIP) fails behind symmetric NAT / strict firewalls unless the
# browser can relay through TURN. coturn runs in `use-auth-secret` mode, so we
# mint SHORT-LIVED credentials per request instead of shipping a static user:
#   username   = "<unix_expiry>:<identity>"
#   credential = base64(HMAC-SHA1(static-auth-secret, username))
# Same derivation the coturn docs specify; the secret never reaches the browser.
TURN_HOST = os.environ.get("TURN_HOST", "13.140.226.178")
TURN_PORT = int(os.environ.get("TURN_PORT", "3478"))
TURN_TTL = int(os.environ.get("TURN_TTL", "3600"))
TURN_STATIC_SECRET = os.environ.get("TURN_STATIC_SECRET", "")
STUN_URLS = [f"stun:{TURN_HOST}:{TURN_PORT}", "stun:stun.l.google.com:19302"]


def _turn_credentials(identity: str, ttl: Optional[int] = None) -> tuple[str, str]:
    """Mint a time-limited TURN username/credential pair (TURN REST API)."""
    username = f"{int(time.time()) + (ttl or TURN_TTL)}:{identity}"
    digest = hmac.new(TURN_STATIC_SECRET.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


async def _optional_agent(request: Request) -> Optional[dict]:
    """Authentication is optional on the public fans' endpoints: signed-in users
    get feed personalisation (follows), anonymous users still see the live row.

    NOTE: get_agent is a FastAPI dependency, so calling it directly does NOT
    inject its Header(...) defaults — every credential header has to be passed
    through explicitly or the lookup always 401s and personalisation silently
    disappears.
    """
    try:
        return await get_agent(
            request,
            x_agent_key=request.headers.get("x-agent-key"),
            x_voice_exec=request.headers.get("x-voice-exec"),
            authorization=request.headers.get("authorization"),
        )
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        return None


@router.get("/ice")
async def ice_servers(request: Request) -> dict:
    """ICE servers for WebRTC publish. TURN creds are ephemeral (default 1h)."""
    agent = await _optional_agent(request)
    identity = f"agent-{agent['id']}" if agent else "anon"
    servers: list[dict] = [{"urls": STUN_URLS}]
    if TURN_STATIC_SECRET:
        username, credential = _turn_credentials(identity)
        servers.append(
            {
                "urls": [
                    f"turn:{TURN_HOST}:{TURN_PORT}?transport=udp",
                    f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",
                ],
                "username": username,
                "credential": credential,
            }
        )
    return {"iceServers": servers, "ttl": TURN_TTL, "turn": bool(TURN_STATIC_SECRET)}


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
        # Presence heartbeats → real "N watching" instead of a frozen 0.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_viewers (
                stream_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                seen_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (stream_id, agent_id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_live_viewers_seen ON live_viewers(stream_id, seen_at)")
        await db.commit()


STALE_LIVE_SECONDS = 24 * 60 * 60  # streams not ended in 24h are swept to 'ended'


async def sweep_stale_live_streams() -> int:
    """End live streams older than 24h (crashed broadcasters, dropped WebRTC, etc.).
    Publishes a 30311 'ended' event per swept stream (fire-and-forget). Returns count swept."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM live_streams WHERE status = 'live' AND started_at < datetime('now', '-1 day')"
        )
        stale = await cur.fetchall()
        for row in stale:
            await db.execute(
                "UPDATE live_streams SET status = 'ended', ended_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            asyncio.create_task(
                _publish_30311(row["agent_id"], row["d_tag"], row["title"], row["summary"],
                               row["stream_url"], row["image_url"], "ended")
            )
        await db.commit()
    # Viewer presence rows are only meaningful for ~1 min; drop the rest.
    async with get_db() as db:
        await db.execute("DELETE FROM live_viewers WHERE seen_at < datetime('now', '-1 hour')")
        await db.commit()
    return len(stale)


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
async def active_live(request: Request) -> dict:
    """Public — the home-feed "Live now" row reads this.

    Ordering mirrors Amethyst's HomeLiveFilter: followed hosts first, then the
    streams with the most followed people participating, then audience size and
    recency. Anonymous callers fall back to audience/recency only.
    """
    agent = await _optional_agent(request)
    viewer_id = int(agent["id"]) if agent else None

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT ls.*, a.name AS agent_name, a.avatar_url AS avatar_url,
                   (SELECT COUNT(*) FROM live_viewers v
                     WHERE v.stream_id = ls.id
                       AND v.seen_at > datetime('now', '-70 seconds')) AS live_viewers,
                   (SELECT COUNT(*) FROM live_chat c
                     WHERE c.stream_id = ls.id) AS chat_count,
                   COALESCE((SELECT MAX(c.created_at) FROM live_chat c
                              WHERE c.stream_id = ls.id), ls.started_at) AS last_activity
            FROM live_streams ls
            LEFT JOIN agents a ON a.id = ls.agent_id
            WHERE ls.status = 'live'
            ORDER BY ls.started_at DESC
            LIMIT 50
            """
        )
        rows = await cur.fetchall()

        follows: set[int] = set()
        followed_chatters: dict[int, int] = {}
        if viewer_id:
            fc = await db.execute(
                "SELECT following_id FROM agent_follows WHERE follower_id = ?", (viewer_id,)
            )
            follows = {int(r[0]) for r in await fc.fetchall()}
            if follows:
                ph = ",".join("?" * len(follows))
                cc = await db.execute(
                    f"SELECT stream_id, COUNT(DISTINCT agent_id) FROM live_chat "
                    f"WHERE agent_id IN ({ph}) GROUP BY stream_id",
                    tuple(follows),
                )
                followed_chatters = {int(sid): int(n) for sid, n in await cc.fetchall()}

    streams = []
    for r in rows:
        d = await _row_to_dict(r)
        d["viewer_count"] = int(r["live_viewers"] or 0)
        d["chat_count"] = int(r["chat_count"] or 0)
        d["last_activity"] = r["last_activity"]
        d["following_host"] = bool(viewer_id and int(r["agent_id"]) in follows)
        d["followed_chatters"] = followed_chatters.get(int(r["id"]), 0)
        streams.append(d)

    # Stable two-pass sort: recency first, then the Amethyst precedence.
    streams.sort(key=lambda s: s["last_activity"] or "", reverse=True)
    streams.sort(
        key=lambda s: (
            0 if s["following_host"] else 1,
            -s["followed_chatters"],
            -s["viewer_count"],
        )
    )
    return {"streams": streams, "count": len(streams)}


@router.post("/{stream_id}/view")
async def live_view_heartbeat(stream_id: int, agent: dict = Depends(get_agent)) -> dict:
    """Viewer presence heartbeat — powers the live "N watching" count."""
    agent_id = int(agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM live_streams WHERE id = ?", (stream_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Live stream not found")
        await db.execute(
            """
            INSERT INTO live_viewers (stream_id, agent_id, seen_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(stream_id, agent_id) DO UPDATE SET seen_at = datetime('now')
            """,
            (stream_id, agent_id),
        )
        await db.commit()
        c = await db.execute(
            "SELECT COUNT(*) FROM live_viewers WHERE stream_id = ? "
            "AND seen_at > datetime('now', '-70 seconds')",
            (stream_id,),
        )
        viewers = (await c.fetchone())[0]
    return {"ok": True, "viewers": int(viewers)}


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
