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
from typing import Optional, Sequence

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..db import get_db
from ..deps import get_agent
from ..nip19 import naddr, nevent
from ..nostr.registry import primary_relay

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

KIND_LIVE_EVENT = 30311   # NIP-53 Live Streaming Event (addressable)
KIND_LIVE_CHAT = 1311     # NIP-53 live chat message
KIND_REACTION = 7         # NIP-25 reaction
KIND_DELETION = 5         # NIP-09 delete request
KIND_REPORT = 1984        # NIP-56 report
KIND_ZAP_REQUEST = 9734   # NIP-57 zap request (signed by the sender, forwarded to LNURL)
KIND_ZAP_RECEIPT = 9735   # NIP-57 zap receipt (published by the recipient's LNURL server)
KIND_VIDEO = 21           # NIP-71 video event (recorded stream / VOD)

# NIP-53 `status` tag values
STATUS_PLANNED = "planned"
STATUS_LIVE = "live"
STATUS_ENDED = "ended"
# Vantage-internal, never published: the HLS probe found the stream dead.
STATUS_OFFLINE = "offline"

PROBE_FAILURES_BEFORE_OFFLINE = 3

# ── Recordings (NIP-71 VOD) ───────────────────────────────────────────────
# mediamtx writes one fMP4 per publisher session under the stream key; nginx
# exposes that directory read-only at /recordings/. When a camera stream ends we
# attach the newest file as the stream's `recording` and publish a kind-21 video
# event, so the replay survives the HLS window.
RECORDINGS_DIR = os.environ.get("MEDIAMTX_RECORDINGS_DIR", "/var/lib/mediamtx/recordings")
RECORDINGS_URL_BASE = os.environ.get("MEDIAMTX_RECORDINGS_URL", "http://localhost/recordings")


def _recording_for(stream_url: str) -> str:
    """Newest recorded file for this stream's mediamtx path, as a public URL."""
    try:
        key = stream_url.rstrip("/").split("/")[-2] if stream_url.endswith(".m3u8") else ""
        if not key:
            return ""
        directory = os.path.join(RECORDINGS_DIR, key)
        if not os.path.isdir(directory):
            return ""
        files = [f for f in os.listdir(directory) if f.endswith((".mp4", ".m4s", ".ts", ".mkv"))]
        if not files:
            return ""
        newest = max(files, key=lambda f: os.path.getmtime(os.path.join(directory, f)))
        return f"{RECORDINGS_URL_BASE}/{key}/{newest}"
    except Exception:  # noqa: BLE001
        return ""

# mediamtx public endpoints (camera mode). Overridable via env.
MEDIAMTX_HLS_BASE = os.environ.get("MEDIAMTX_HLS_BASE", "http://localhost:8888")
MEDIAMTX_WHIP_BASE = os.environ.get("MEDIAMTX_WHIP_BASE", "http://localhost:8890")

# ── TURN (coturn, use-auth-secret / TURN REST API) ────────────────────────
# Camera publish (WHIP) fails behind symmetric NAT / strict firewalls unless the
# browser can relay through TURN. coturn runs in `use-auth-secret` mode, so we
# mint SHORT-LIVED credentials per request instead of shipping a static user:
#   username   = "<unix_expiry>:<identity>"
#   credential = base64(HMAC-SHA1(static-auth-secret, username))
# Same derivation the coturn docs specify; the secret never reaches the browser.
TURN_HOST = os.environ.get("TURN_HOST", "localhost")  # instance config; never a built-in public IP
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
    # NIP-53: a stream can be announced ahead of time.
    scheduled_at: int = Field(default=0, ge=0, description="unix seconds; future → status=planned")
    hashtags: list[str] = Field(default_factory=list, max_length=10)
    participants: list[str] = Field(default_factory=list, max_length=20,
                                    description="additional participant pubkeys (hex)")


class EndLiveBody(BaseModel):
    stream_id: Optional[int] = None
    d_tag: Optional[str] = None


class ChatBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


class ReactBody(BaseModel):
    emoji: str = Field(default="+", max_length=16)


class ReportBody(BaseModel):
    report_type: str = Field(..., max_length=64,
                             description="NIP-56 report type: nudity|malware|profanity|illegal|spam|impersonation|other")
    reason: str = Field(default="", max_length=1000)
    chat_id: Optional[int] = None


class LightningBody(BaseModel):
    lud16: str = Field(..., max_length=200, description="lightning address, name@domain")


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
        # NIP-09 delete + NIP-53 chat event id (so a deletion can reference it)
        for col, ddl in (("nostr_event_id", "TEXT DEFAULT ''"), ("deleted", "INTEGER DEFAULT 0")):
            cols = [r[1] for r in await (await db.execute("PRAGMA table_info(live_chat)")).fetchall()]
            if col not in cols:
                await db.execute(f"ALTER TABLE live_chat ADD COLUMN {col} {ddl}")
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
        # NIP-53/NIP-71 column additions (idempotent — SQLite has no IF NOT EXISTS for columns)
        for col, ddl in (
            ("hashtags", "TEXT DEFAULT ''"),
            ("scheduled_at", "TEXT"),
            ("recording_url", "TEXT DEFAULT ''"),
            ("participants", "TEXT DEFAULT ''"),
            ("probe_failures", "INTEGER DEFAULT 0"),
            ("offline_since", "TEXT"),
        ):
            cols = [r[1] for r in await (await db.execute("PRAGMA table_info(live_streams)")).fetchall()]
            if col not in cols:
                await db.execute(f"ALTER TABLE live_streams ADD COLUMN {col} {ddl}")

        # NIP-25 reactions on a stream (one row per agent+emoji; toggled off on repeat)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_reactions (
                stream_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                event_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (stream_id, agent_id, emoji)
            )
            """
        )
        # NIP-57 zaps. amount_msat + the receipt event id prove payment happened.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_zaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id INTEGER NOT NULL,
                sender_agent_id INTEGER,
                sender_pubkey TEXT DEFAULT '',
                amount_msat INTEGER NOT NULL,
                comment TEXT DEFAULT '',
                invoice TEXT DEFAULT '',
                receipt_event_id TEXT DEFAULT '',
                paid INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_live_zaps_stream ON live_zaps(stream_id, paid)")
        # NIP-57: lightning addresses (lud16) for zappable hosts. Created here so
        # the /active EXISTS() probe never hits a missing table.
        await db.execute(
            "CREATE TABLE IF NOT EXISTS agent_lightning "
            "(agent_id INTEGER PRIMARY KEY, lud16 TEXT NOT NULL)"
        )
        # NIP-56 reports
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id INTEGER NOT NULL,
                chat_id INTEGER,
                reporter_agent_id INTEGER NOT NULL,
                target_pubkey TEXT DEFAULT '',
                report_type TEXT NOT NULL,
                reason TEXT DEFAULT '',
                event_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
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


async def _publish_event(agent_id: int, kind: int, tags: list, content: str = "") -> Optional[dict]:
    """Publish to the relay and return the accepted event, or None.

    Two failure modes used to pass silently here, and both look identical from
    the caller's side:
      * the relay rejects the event (`["OK", id, false, "restricted: ..."]`)
      * the socket dies mid-publish
    So the ack is inspected explicitly and a rejection is logged as a warning
    with the relay's own reason. The returned dict is the EVENT (not the
    {"event":..., "ack":...} wrapper) because every caller wants `.id`.
    """
    try:
        from ..buzz_identity import derive_buzz_keypair
        from ..nostr.client import NostrSession

        pk = await derive_buzz_keypair(agent_id)
        sess = NostrSession(primary_relay(), pk)
        await sess.connect()
        await sess.authenticate()
        result = await sess.publish(kind, content, tags=tags)
        await sess.close()

        if not isinstance(result, dict):
            logger.warning("nostr publish: unexpected result for kind %s: %r", kind, result)
            return None
        ack = result.get("ack")
        if isinstance(ack, list) and len(ack) > 2 and ack[2] is not True:
            logger.warning("nostr publish REJECTED: kind=%s reason=%s", kind, ack[3] if len(ack) > 3 else ack)
            return None
        event = result.get("event")
        return event if isinstance(event, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("nostr publish failed (non-fatal): kind=%s %s", kind, exc)
        return None


async def _stream_pubkey(agent_id: int) -> str:
    """The pubkey that owns a stream's 30311 address (its `d`-tag namespace)."""
    try:
        from ..buzz_identity import derive_buzz_keypair, public_key_xonly_hex

        pk = await derive_buzz_keypair(agent_id)
        return public_key_xonly_hex(pk)
    except Exception:  # noqa: BLE001
        return ""


async def _stream_address(agent_id: int, d_tag: str) -> str:
    pk = await _stream_pubkey(agent_id)
    return f"{KIND_LIVE_EVENT}:{pk}:{d_tag}" if pk else ""


async def _publish_30311(agent_id: int, d_tag: str, title: str, summary: str,
                         stream_url: str, image_url: str, status: str,
                         starts: Optional[int] = None, ends: Optional[int] = None,
                         hashtags: Sequence[str] = (), participants: Sequence[str] = (),
                         recording_url: str = "", current_participants: Optional[int] = None,
                         stream_id: Optional[int] = None) -> Optional[dict]:
    """Publish the NIP-53 live event with the full tag set.

    NIP-53 tags carried: d, title, summary, image, status, starts, ends,
    streaming (live URL), recording (replay URL), p (host + participants),
    t (hashtags), current_participants.

    When stream_id is given the event id is persisted on the row: reactions and
    deletions need an `e` tag pointing at the newest 30311 event, and this relay
    rejects a reaction whose `e` target it cannot resolve.
    """
    tags: list[list[str]] = [
        ["d", d_tag],
        ["title", title],
        ["status", status],
        ["starts", str(int(starts or time.time()))],
    ]
    if ends:
        tags.append(["ends", str(int(ends))])
    if stream_url:
        tags.append(["streaming", stream_url])
    if recording_url:
        tags.append(["recording", recording_url])
    if summary:
        tags.append(["summary", summary])
    if image_url:
        tags.append(["image", image_url])
    host = await _stream_pubkey(agent_id)
    if host:
        tags.append(["p", host])
    for p in participants:
        if p and p != host:
            tags.append(["p", p])
    for t in hashtags:
        if t:
            tags.append(["t", t.lstrip("#").lower()])
    if current_participants is not None:
        tags.append(["current_participants", str(int(current_participants))])
    ev = await _publish_event(agent_id, KIND_LIVE_EVENT, tags, content=summary or "")
    if stream_id and ev and ev.get("id"):
        try:
            async with get_db() as db:
                await db.execute(
                    "UPDATE live_streams SET nostr_event_id = ? WHERE id = ?",
                    (ev["id"], stream_id),
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not record live event id: %s", exc)
    return ev


async def _publish_video_event(agent_id: int, d_tag: str, title: str, summary: str,
                               recording_url: str, image_url: str, starts: int) -> None:
    """NIP-71 kind 21 — the recording of a finished stream, so it replays as VOD."""
    if not recording_url:
        return
    mime = ("video/mp4" if recording_url.endswith((".mp4", ".m4s", ".mkv"))
            else "video/mp2t" if recording_url.endswith(".ts")
            else "application/vnd.apple.mpegurl")
    tags = [
        ["d", d_tag],
        ["title", title],
        ["url", recording_url],
        ["m", mime],
        ["published_at", str(int(starts))],
        ["alt", f"Recording of live stream: {title}"],
    ]
    if image_url:
        tags.append(["image", image_url])
    if summary:
        tags.append(["summary", summary])
    addr = await _stream_address(agent_id, d_tag)
    if addr:
        tags.append(["a", addr, primary_relay()])
    await _publish_event(agent_id, KIND_VIDEO, tags, content=summary or "")


async def _publish_chat_event(agent_id: int, stream_pubkey: str, d_tag: str, content: str) -> Optional[dict]:
    tags = [[ "a", f"{KIND_LIVE_EVENT}:{stream_pubkey}:{d_tag}", primary_relay()]]
    if stream_pubkey:
        tags.append(["p", stream_pubkey])
    return await _publish_event(agent_id, KIND_LIVE_CHAT, tags, content=content)


async def _publish_chat_and_record(chat_id: int, agent_id: int, stream_pubkey: str,
                                   d_tag: str, content: str) -> None:
    """Publish the NIP-53 chat event, then persist its id on the row so a later
    NIP-09 deletion can reference the actual event (not just the local row)."""
    ev = await _publish_chat_event(agent_id, stream_pubkey, d_tag, content)
    if ev and ev.get("id"):
        try:
            async with get_db() as db:
                await db.execute(
                    "UPDATE live_chat SET nostr_event_id = ? WHERE id = ?", (ev["id"], chat_id)
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not record chat event id: %s", exc)


async def _publish_reaction(agent_id: int, address: str, emoji: str, host_pubkey: str = "",
                            target_event_id: str = "") -> Optional[dict]:
    """NIP-25 reaction, tagged the way NIP-25 asks: `e` for the target event,
    `a` for the addressable root and `p` for the author.

    The `e` tag is not decorative here: the Buzz relay resolves a reaction's
    channel through its `e` target and rejects the event with
    "reaction must reference a target event via e tag" without one.
    """
    tags: list[list[str]] = []
    if target_event_id:
        tags.append(["e", target_event_id, primary_relay()])
    if address:
        tags.append(["a", address, primary_relay()])
    if host_pubkey:
        tags.append(["p", host_pubkey])
    return await _publish_event(agent_id, KIND_REACTION, tags, content=emoji)


async def _publish_deletion(agent_id: int, event_id: str) -> None:
    if event_id:
        await _publish_event(agent_id, KIND_DELETION, "", tags=[["e", event_id]])


async def _publish_report(agent_id: int, address: str, target_pubkey: str,
                          report_type: str, reason: str, chat_event_id: str = "") -> None:
    """NIP-56 report.

    The report TYPE is the third element of the `p`/`e`/`a` tag — that is the
    NIP-56 canonical shape, and this relay enforces it ("report target tag
    missing report type"). The L/l MOD labels are added for label-aware clients.
    """
    tags: list[list[str]] = []
    if chat_event_id:
        tags.append(["e", chat_event_id, report_type])
    elif address:
        tags.append(["a", address, report_type])
    if target_pubkey:
        tags.append(["p", target_pubkey, report_type])
    tags.append(["L", "MOD"])
    tags.append(["l", report_type, "MOD"])
    await _publish_event(agent_id, KIND_REPORT, reason, tags=tags)


async def _row_to_dict(row: aiosqlite.Row) -> dict:
    keys = row.keys()
    agent_id = int(row["agent_id"])
    d_tag = row["d_tag"]
    address = await _stream_address(agent_id, d_tag)
    try:
        naddr_value = naddr(d_tag, address.split(":")[1], KIND_LIVE_EVENT, [primary_relay()]) if address else ""
    except Exception:  # noqa: BLE001
        naddr_value = ""
    hashtags = [t for t in (row["hashtags"] or "").split(",") if t] if "hashtags" in keys else []
    return {
        "id": row["id"],
        "agent_id": agent_id,
        "agent_name": row["agent_name"] if "agent_name" in keys else None,
        "avatar_url": row["avatar_url"] if "avatar_url" in keys else "",
        "d_tag": d_tag,
        "title": row["title"],
        "summary": row["summary"],
        "stream_url": row["stream_url"],
        "image_url": row["image_url"],
        "status": row["status"],
        "viewer_count": row["viewer_count"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        # NIP-53 / NIP-19 additions
        "address": address,
        "naddr": naddr_value,
        "hashtags": hashtags,
        "scheduled_at": row["scheduled_at"] if "scheduled_at" in keys else None,
        "recording_url": row["recording_url"] if "recording_url" in keys else "",
        "participants": [p for p in (row["participants"] or "").split(",") if p] if "participants" in keys else [],
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

    # NIP-53: a future start time means "planned", not "live" — it shows in the
    # feed as upcoming and is not probed for liveness until it starts.
    starts = int(body.scheduled_at or time.time())
    status = STATUS_PLANNED if starts > time.time() + 30 else STATUS_LIVE
    hashtags = ",".join(t.strip().lstrip("#").lower() for t in body.hashtags if t.strip())
    participants = ",".join(p for p in body.participants if p)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO live_streams
                (agent_id, d_tag, title, summary, stream_url, image_url, status,
                 hashtags, participants, scheduled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, d_tag, body.title, body.summary, stream_url, body.image_url, status,
             hashtags, participants, body.scheduled_at or None),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM live_streams WHERE d_tag = ?", (d_tag,))
        row = await cur.fetchone()

    # Awaited, not fire-and-forget: callers immediately react/chat against this
    # stream, and a reaction needs the 30311 event id (`e` tag). A background
    # publish raced that and produced intermittent relay rejections.
    published = await _publish_30311(
            agent_id, d_tag, body.title, body.summary, stream_url, body.image_url, status,
            starts=starts,
            hashtags=[t for t in hashtags.split(",") if t],
            participants=[p for p in participants.split(",") if p],
            stream_id=int(row["id"]),
    )

    result = {"ok": True, "stream": await _row_to_dict(row) if row else None,
              "nostr_event_id": (published or {}).get("id", "")}
    if stream_key:
        result["stream_key"] = stream_key
        result["whip_url"] = whip_url
    return result


@router.post("/{stream_id}/go-live")
async def go_live(stream_id: int, agent: dict = Depends(get_agent)) -> dict:
    """Promote a planned stream to live (NIP-53 status transition)."""
    agent_id = int(agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM live_streams WHERE id = ? AND agent_id = ?", (stream_id, agent_id)
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Live stream not found")
        await db.execute(
            "UPDATE live_streams SET status = ?, scheduled_at = NULL, "
            "started_at = datetime('now'), probe_failures = 0, offline_since = NULL WHERE id = ?",
            (STATUS_LIVE, stream_id),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM live_streams WHERE id = ?", (stream_id,))
        row = await cur.fetchone()

    published = await _publish_30311(
            agent_id, row["d_tag"], row["title"], row["summary"], row["stream_url"],
            row["image_url"], STATUS_LIVE,
            starts=int(time.time()),
            hashtags=[t for t in (row["hashtags"] or "").split(",") if t],
            participants=[p for p in (row["participants"] or "").split(",") if p],
            stream_id=stream_id,
    )
    return {"ok": True, "stream": await _row_to_dict(row),
            "nostr_event_id": (published or {}).get("id", "")}


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
            "UPDATE live_streams SET status = ?, ended_at = datetime('now') WHERE id = ?",
            (STATUS_ENDED, row["id"]),
        )
        await db.commit()

    ended_at = int(time.time())
    recording_url = row["recording_url"] if "recording_url" in row.keys() else ""
    # Camera streams leave an fMP4 behind; attach it so the replay is playable
    # instead of the stream simply vanishing when the HLS window closes.
    if not recording_url:
        recording_url = _recording_for(row["stream_url"])
        if recording_url:
            try:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE live_streams SET recording_url = ? WHERE id = ?",
                        (recording_url, row["id"]),
                    )
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not store recording url: %s", exc)
    await _publish_30311(agent_id, row["d_tag"], row["title"], row["summary"],
                       row["stream_url"], row["image_url"], STATUS_ENDED,
                       ends=ended_at,
                       hashtags=[t for t in (row["hashtags"] or "").split(",") if t],
                       participants=[p for p in (row["participants"] or "").split(",") if p],
                       stream_id=int(row["id"]))
    # NIP-71: a finished stream with a recording is also published as a video
    # event, so it replays as VOD instead of vanishing when the HLS window closes.
    if recording_url:
        asyncio.create_task(
            _publish_video_event(agent_id, row["d_tag"], row["title"], row["summary"],
                                 recording_url, row["image_url"], ended_at)
        )
    return {"ok": True, "stream_id": row["id"], "status": STATUS_ENDED,
            "recording_url": recording_url}


@router.get("/active")
async def active_live(request: Request) -> dict:
    """Public — the home-feed "Live now" row reads this.

    Ordering mirrors Amethyst: NIP-53 status rank first (live=2, planned=1, ended=0),
    then followed hosts, then streams with the most followed participants, then
    audience and recency. Anonymous callers get status/audience/recency only.
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
                              WHERE c.stream_id = ls.id), ls.started_at) AS last_activity,
                   (SELECT COUNT(*) FROM live_zaps z
                     WHERE z.stream_id = ls.id AND z.paid = 1) AS zap_count,
                   COALESCE((SELECT SUM(z.amount_msat) FROM live_zaps z
                              WHERE z.stream_id = ls.id AND z.paid = 1), 0) AS zap_msat,
                   EXISTS(SELECT 1 FROM agent_lightning al WHERE al.agent_id = ls.agent_id) AS can_zap
            FROM live_streams ls
            LEFT JOIN agents a ON a.id = ls.agent_id
            WHERE ls.status IN ('live', 'planned')
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

        # NIP-25 reaction tallies (one query for every stream in the row)
        reaction_rows = await (await db.execute(
            "SELECT stream_id, emoji, COUNT(*) AS n FROM live_reactions GROUP BY stream_id, emoji"
        )).fetchall()
        reactions: dict[int, dict[str, int]] = {}
        for r in reaction_rows:
            reactions.setdefault(int(r["stream_id"]), {})[r["emoji"]] = int(r["n"])

        mine: dict[int, list[str]] = {}
        if viewer_id:
            mr = await db.execute(
                "SELECT stream_id, emoji FROM live_reactions WHERE agent_id = ?", (viewer_id,)
            )
            for r in await mr.fetchall():
                mine.setdefault(int(r["stream_id"]), []).append(r["emoji"])

    status_rank = {STATUS_LIVE: 2, STATUS_PLANNED: 1, STATUS_ENDED: 0}
    streams = []
    for r in rows:
        d = await _row_to_dict(r)
        sid = int(r["id"])
        d["viewer_count"] = int(r["live_viewers"] or 0)
        d["chat_count"] = int(r["chat_count"] or 0)
        d["last_activity"] = r["last_activity"]
        d["following_host"] = bool(viewer_id and int(r["agent_id"]) in follows)
        d["followed_chatters"] = followed_chatters.get(sid, 0)
        d["status_rank"] = status_rank.get(d["status"], 0)
        d["reactions"] = reactions.get(sid, {})
        d["my_reactions"] = mine.get(sid, [])
        d["zap_count"] = int(r["zap_count"] or 0)
        d["zap_msat"] = int(r["zap_msat"] or 0)
        d["can_zap"] = bool(r["can_zap"])
        streams.append(d)

    # Stable two-pass sort: recency first, then the Amethyst precedence.
    streams.sort(key=lambda s: s["last_activity"] or "", reverse=True)
    streams.sort(
        key=lambda s: (
            -s["status_rank"],
            0 if s["following_host"] else 1,
            -s["followed_chatters"],
            -s["viewer_count"],
        )
    )
    return {"streams": streams, "count": len(streams)}


async def probe_live_streams() -> dict:
    """Amethyst's OnlineStatusCache, server-side: a stream that is live in the DB
    but has no playable HLS is a lie in the feed. Probe the playlist; after
    PROBE_FAILURES_BEFORE_OFFLINE consecutive failures, mark it offline and
    publish the NIP-53 `ended` transition."""
    import httpx

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM live_streams WHERE status = ? AND stream_url != ''",
            (STATUS_LIVE,),
        )).fetchall()
        # Planned streams whose start time has arrived get promoted once playable.
        due = await (await db.execute(
            "SELECT * FROM live_streams WHERE status = ? AND scheduled_at IS NOT NULL "
            "AND scheduled_at <= ?",
            (STATUS_PLANNED, int(time.time())),
        )).fetchall()

    checked = offline = promoted = 0
    async with httpx.AsyncClient(timeout=6.0, verify=False) as client:
        for row in list(rows) + list(due):
            sid = int(row["id"])
            url = row["stream_url"]
            checked += 1
            playable = False
            try:
                resp = await client.get(url)
                playable = resp.status_code == 200 and "#EXTM3U" in resp.text
            except Exception:  # noqa: BLE001
                playable = False

            if playable and row["status"] == STATUS_PLANNED:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE live_streams SET status = ?, started_at = datetime('now'), "
                        "probe_failures = 0 WHERE id = ?",
                        (STATUS_LIVE, sid),
                    )
                    await db.commit()
                promoted += 1
                asyncio.create_task(_publish_30311(
                    int(row["agent_id"]), row["d_tag"], row["title"], row["summary"],
                    row["stream_url"], row["image_url"], STATUS_LIVE, stream_id=sid))
                continue

            if playable:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE live_streams SET probe_failures = 0, offline_since = NULL WHERE id = ?",
                        (sid,),
                    )
                    await db.commit()
                continue

            failures = int(row["probe_failures"] or 0) + 1
            async with get_db() as db:
                if failures >= PROBE_FAILURES_BEFORE_OFFLINE:
                    await db.execute(
                        "UPDATE live_streams SET status = ?, probe_failures = ?, "
                        "offline_since = datetime('now'), ended_at = datetime('now') WHERE id = ?",
                        (STATUS_OFFLINE, failures, sid),
                    )
                else:
                    await db.execute(
                        "UPDATE live_streams SET probe_failures = ? WHERE id = ?", (failures, sid)
                    )
                await db.commit()
            if failures >= PROBE_FAILURES_BEFORE_OFFLINE:
                offline += 1
                asyncio.create_task(_publish_30311(
                    int(row["agent_id"]), row["d_tag"], row["title"], row["summary"],
                    row["stream_url"], row["image_url"], STATUS_ENDED,
                    ends=int(time.time()), stream_id=sid))

    return {"checked": checked, "offline": offline, "promoted": promoted}


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
        ins = await db.execute(
            "INSERT INTO live_chat (stream_id, agent_id, agent_name, content) VALUES (?, ?, ?, ?)",
            (stream_id, agent_id, agent.get("name") or "Agent", content),
        )
        chat_id = int(ins.lastrowid or 0)
        await db.commit()

    from ..buzz_identity import public_key_xonly_hex, derive_buzz_keypair
    try:
        pk = await derive_buzz_keypair(int(stream["agent_id"]))
        stream_pubkey = public_key_xonly_hex(pk)
    except Exception:
        stream_pubkey = ""
    if stream_pubkey:
        asyncio.create_task(_publish_chat_and_record(
            chat_id, agent_id, stream_pubkey, stream["d_tag"], content))

    return {"ok": True, "chat_id": chat_id, "agent_name": agent.get("name") or "Agent",
            "content": content}


@router.get("/{stream_id}/chat")
async def get_chat(stream_id: int, limit: int = Query(50, ge=1, le=200)) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, stream_id, agent_id, agent_name, content, created_at
            FROM live_chat
            WHERE stream_id = ? AND COALESCE(deleted, 0) = 0
            ORDER BY id DESC LIMIT ?
            """,
            (stream_id, limit),
        )
        rows = await cur.fetchall()
    messages = [dict(r) for r in reversed(rows)]  # oldest-first within the window
    return {"messages": messages}


async def _load_stream(stream_id: int) -> aiosqlite.Row:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT ls.*, a.name AS agent_name, a.avatar_url AS avatar_url "
            "FROM live_streams ls LEFT JOIN agents a ON a.id = ls.agent_id WHERE ls.id = ?",
            (stream_id,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Live stream not found")
    return row


@router.get("/{stream_id}")
async def stream_detail(stream_id: int) -> dict:
    """Single stream — includes its NIP-19 naddr so any client can resolve it."""
    return {"stream": await _row_to_dict(await _load_stream(stream_id))}


# ── NIP-25 reactions ──────────────────────────────────────────────────────
@router.post("/{stream_id}/react")
async def react(stream_id: int, body: ReactBody, agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])
    emoji = (body.emoji or "+").strip()[:16]
    stream = await _load_stream(stream_id)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT 1 FROM live_reactions WHERE stream_id = ? AND agent_id = ? AND emoji = ?",
            (stream_id, agent_id, emoji),
        )
        existing = await cur.fetchone()
        if existing:
            # toggle off
            await db.execute(
                "DELETE FROM live_reactions WHERE stream_id = ? AND agent_id = ? AND emoji = ?",
                (stream_id, agent_id, emoji),
            )
            active = False
        else:
            await db.execute(
                "INSERT INTO live_reactions (stream_id, agent_id, emoji) VALUES (?, ?, ?)",
                (stream_id, agent_id, emoji),
            )
            active = True
        await db.commit()
        rows = await (await db.execute(
            "SELECT emoji, COUNT(*) AS n FROM live_reactions WHERE stream_id = ? GROUP BY emoji",
            (stream_id,),
        )).fetchall()

    if active:
        address = await _stream_address(int(stream["agent_id"]), stream["d_tag"])
        host = await _stream_pubkey(int(stream["agent_id"]))
        if address:
            target_event_id = stream["nostr_event_id"] if "nostr_event_id" in stream.keys() else ""
            asyncio.create_task(_publish_reaction(agent_id, address, emoji, host, target_event_id))

    return {"ok": True, "active": active,
            "reactions": {r["emoji"]: int(r["n"]) for r in rows}}


# ── NIP-09 deletion (author only) ─────────────────────────────────────────
@router.delete("/{stream_id}/chat/{chat_id}")
async def delete_chat(stream_id: int, chat_id: int, agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM live_chat WHERE id = ? AND stream_id = ?", (chat_id, stream_id)
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        if int(row["agent_id"]) != agent_id:
            raise HTTPException(status_code=403, detail="You can only delete your own messages")
        await db.execute("UPDATE live_chat SET deleted = 1 WHERE id = ?", (chat_id,))
        await db.commit()
    event_id = row["nostr_event_id"] if "nostr_event_id" in row.keys() else ""
    if event_id:
        asyncio.create_task(_publish_deletion(agent_id, event_id))
    return {"ok": True, "deleted": chat_id}


# ── NIP-56 reporting ──────────────────────────────────────────────────────
@router.post("/{stream_id}/report")
async def report(stream_id: int, body: ReportBody, agent: dict = Depends(get_agent)) -> dict:
    agent_id = int(agent["id"])
    stream = await _load_stream(stream_id)
    allowed = {"nudity", "malware", "profanity", "illegal", "spam", "impersonation", "other"}
    rtype = body.report_type.strip().lower()
    if rtype not in allowed:
        raise HTTPException(status_code=422, detail=f"report_type must be one of {sorted(allowed)}")

    target_pubkey = ""
    chat_event_id = ""
    if body.chat_id:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM live_chat WHERE id = ? AND stream_id = ?", (body.chat_id, stream_id)
            )
            chat = await cur.fetchone()
        if not chat:
            raise HTTPException(status_code=404, detail="Message not found")
        target_pubkey = await _stream_pubkey(int(chat["agent_id"]))
        chat_event_id = chat["nostr_event_id"] if "nostr_event_id" in chat.keys() else ""
    else:
        target_pubkey = await _stream_pubkey(int(stream["agent_id"]))

    async with get_db() as db:
        await db.execute(
            "INSERT INTO live_reports (stream_id, chat_id, reporter_agent_id, target_pubkey, "
            "report_type, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (stream_id, body.chat_id, agent_id, target_pubkey, rtype, body.reason[:1000]),
        )
        await db.commit()

    address = await _stream_address(int(stream["agent_id"]), stream["d_tag"])
    if address:
        asyncio.create_task(_publish_report(agent_id, address, target_pubkey, rtype,
                                            body.reason, chat_event_id))
    return {"ok": True, "report_type": rtype, "published": bool(address)}


# ── NIP-57 zaps ───────────────────────────────────────────────────────────
@router.post("/me/lightning")
async def set_lightning(body: LightningBody, agent: dict = Depends(get_agent)) -> dict:
    """Attach a lightning address to the caller so their streams can be zapped."""
    agent_id = int(agent["id"])
    lud16 = body.lud16.strip().lower()
    if "@" not in lud16 or lud16.count("@") != 1:
        raise HTTPException(status_code=422, detail="lud16 must look like name@domain")
    async with get_db() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS agent_lightning (agent_id INTEGER PRIMARY KEY, lud16 TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO agent_lightning (agent_id, lud16) VALUES (?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET lud16 = excluded.lud16",
            (agent_id, lud16),
        )
        await db.commit()
    return {"ok": True, "lud16": lud16}


async def _lud16_of(agent_id: int) -> str:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute("SELECT lud16 FROM agent_lightning WHERE agent_id = ?", (agent_id,))
            row = await cur.fetchone()
            return row["lud16"] if row else ""
        except Exception:  # noqa: BLE001
            return ""


async def _lnurl_pay_params(lud16: str) -> dict:
    """Resolve a lightning address to its LNURL-pay params (NIP-57 step 1)."""
    import httpx

    name, domain = lud16.split("@", 1)
    url = f"https://{domain}/.well-known/lnurlp/{name}"
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        params = r.json()
    if params.get("tag") != "payRequest":
        raise HTTPException(status_code=502, detail="lud16 did not return a payRequest")
    return params


@router.get("/{stream_id}/zap")
async def zap_invoice(stream_id: int, amount_sats: int = Query(21, ge=1, le=1_000_000),
                      comment: str = Query("", max_length=200),
                      agent: dict = Depends(get_agent)) -> dict:
    """Build the NIP-57 zap request and fetch an invoice from the host's LNURL server.

    Requires the host to have set a lightning address (`POST /api/live/me/lightning`).
    Vantage holds no node: the invoice comes from the recipient's LNURL server,
    which also publishes the kind-9735 receipt we later verify.
    """
    import json as _json
    import urllib.parse

    import httpx

    sender_id = int(agent["id"])
    stream = await _load_stream(stream_id)
    host_id = int(stream["agent_id"])
    host_pubkey = await _stream_pubkey(host_id)
    lud16 = await _lud16_of(host_id)
    if not lud16:
        raise HTTPException(status_code=409,
                            detail="Host has no lightning address configured (NIP-57 needs lud16)")
    if not host_pubkey:
        raise HTTPException(status_code=409, detail="Host has no Nostr identity")

    address = await _stream_address(host_id, stream["d_tag"])
    params = await _lnurl_pay_params(lud16)
    amount_msat = amount_sats * 1000
    if amount_msat < int(params.get("minSendable", 1000)):
        raise HTTPException(status_code=422, detail=f"below minSendable ({params['minSendable']} msat)")
    if amount_msat > int(params.get("maxSendable", 10**12)):
        raise HTTPException(status_code=422, detail=f"above maxSendable ({params['maxSendable']} msat)")

    # kind 9734 zap request, signed by the sender (NIP-57)
    from ..buzz_identity import derive_buzz_keypair, public_key_xonly_hex
    from ..nostr.client import build_event

    pk = await derive_buzz_keypair(sender_id)
    relay = primary_relay()
    tags = [
        ["relays", relay],
        ["amount", str(amount_msat)],
        ["lnurl", params.get("callback", "")],
        ["p", host_pubkey],
    ]
    if address:
        tags.append(["a", address, relay])
    zap_request = build_event(pk, KIND_ZAP_REQUEST, comment[:200], tags)

    callback = params["callback"]
    qs = {"amount": str(amount_msat), "nostr": _json.dumps(zap_request, separators=(",", ":"))}
    if comment:
        qs["comment"] = comment[:200]
    sep = "&" if "?" in callback else "?"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.get(f"{callback}{sep}{urllib.parse.urlencode(qs)}")
        r.raise_for_status()
        invoice = r.json()

    if invoice.get("status") == "ERROR" or not invoice.get("pr"):
        raise HTTPException(status_code=502, detail=f"LNURL server refused: {invoice.get('reason', 'no invoice')}")

    async with get_db() as db:
        await db.execute(
            "INSERT INTO live_zaps (stream_id, sender_agent_id, sender_pubkey, amount_msat, "
            "comment, invoice, paid) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (stream_id, sender_id, public_key_xonly_hex(pk), amount_msat, comment[:200], invoice["pr"]),
        )
        await db.commit()

    return {"ok": True, "invoice": invoice["pr"], "amount_msat": amount_msat,
            "zap_request": zap_request, "lud16": lud16, "address": address}


@router.post("/{stream_id}/zap/verify")
async def zap_verify(stream_id: int, invoice: str = Query(..., min_length=10),
                     agent: dict = Depends(get_agent)) -> dict:
    """Mark a zap paid when its NIP-57 receipt (kind 9735) shows up on the relay."""
    from ..nostr.client import NostrSession

    stream = await _load_stream(stream_id)
    address = await _stream_address(int(stream["agent_id"]), stream["d_tag"])
    paid = False
    receipt_id = ""

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM live_zaps WHERE stream_id = ? AND invoice = ?", (stream_id, invoice)
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown invoice for this stream")

    try:
        from ..buzz_identity import derive_buzz_keypair

        pk = await derive_buzz_keypair(int(agent["id"]))
        sess = NostrSession(primary_relay(), pk)
        await sess.connect()
        sub = await sess.subscribe([{"kinds": [KIND_ZAP_RECEIPT], "#a": [address], "limit": 50}])
        events = await sess.recv_until_eose(sub, max_events=50)
        await sess.close()
        for ev in events:
            tags = {t[0]: t[1:] for t in ev.get("tags", []) if t}
            bolt11 = (tags.get("bolt11") or [""])[0]
            if bolt11 and bolt11 == invoice:
                paid = True
                receipt_id = ev.get("id", "")
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("zap verify relay query failed: %s", exc)

    if paid:
        async with get_db() as db:
            await db.execute(
                "UPDATE live_zaps SET paid = 1, receipt_event_id = ? WHERE id = ?",
                (receipt_id, row["id"]),
            )
            await db.commit()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        tot = await (await db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount_msat),0) AS msat FROM live_zaps "
            "WHERE stream_id = ? AND paid = 1", (stream_id,)
        )).fetchone()

    return {"ok": True, "paid": paid, "receipt_event_id": receipt_id,
            "zap_count": int(tot["n"]), "zap_msat": int(tot["msat"])}
