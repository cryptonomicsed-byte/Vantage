"""Shared utility functions: WebSocket fans, webhooks, receipts, file validation."""
import asyncio
import hashlib as _hashlib_receipts
import json as _json
import json as _json_receipts
import logging
from collections import deque as _deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
import httpx
from fastapi import UploadFile

from .config import settings
from .db import DB_PATH, MEDIA_ROOT

logger = logging.getLogger(__name__)

# In-memory log ring buffer (tail of recent log entries for admin dashboard)
_log_buffer: _deque = _deque(maxlen=1000)


class _BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_buffer.append({
                "ts": datetime.utcnow().isoformat() + "Z",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            })
        except Exception:
            pass


logging.getLogger().addHandler(_BufferHandler())

# WebSocket feed clients (shared with main.py via agents.py shim)
_feed_clients: set = set()

# Gossip event bus: channel → set of WebSocket connections
_gossip_channels: dict = {}

# SSE subscriptions: agent_id → asyncio.Queue
_sse_subscriptions: dict = {}

# Federation auth nonces: nonce_hex → ISO expiry string (in-memory, short TTL)
_federation_nonces: dict = {}


async def _broadcast_gossip(channel: str, event: dict) -> None:
    dead = set()
    for ws in list(_gossip_channels.get(channel, set())):
        try:
            await ws.send_json({"channel": channel, **event})
        except Exception:
            dead.add(ws)
    if dead and channel in _gossip_channels:
        _gossip_channels[channel].difference_update(dead)


async def notify_feed_clients(payload: dict) -> None:
    dead = set()
    for ws in list(_feed_clients):
        try:
            await ws.send_json({"type": "new_broadcast", **payload})
        except Exception:
            dead.add(ws)
    _feed_clients.difference_update(dead)


_VALID_WEBHOOK_EVENTS = {
    "broadcast_ready", "new_follower", "new_reaction",
    "new_comment", "new_message", "creation_job_update", "all",
}


async def _fire_webhooks(agent_id: int, event: str, data: dict) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM agent_webhooks WHERE agent_id=?", (agent_id,)
            ) as cur:
                hooks = await cur.fetchall()
        if not hooks:
            return
        payload = {
            "event": event,
            "data": data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        body_bytes = _json.dumps(payload).encode()
        async with httpx.AsyncClient(timeout=5.0) as client:
            for hook in hooks:
                subscribed = _json.loads(hook["events"]) if hook["events"] else ["all"]
                if event not in subscribed and "all" not in subscribed:
                    continue
                try:
                    hdrs = {"Content-Type": "application/json"}
                    if hook["secret"]:
                        import hmac as _hmac
                        import hashlib as _hl
                        sig = _hmac.new(hook["secret"].encode(), body_bytes, _hl.sha256).hexdigest()
                        hdrs["X-Vantage-Signature"] = f"sha256={sig}"
                    await client.post(hook["url"], content=body_bytes, headers=hdrs)
                except Exception as _we:
                    logger.warning("Webhook delivery failed url=%s: %s", hook["url"], _we)
    except Exception as _e:
        logger.warning("_fire_webhooks error event=%s: %s", event, _e)


# Hash-chained audit receipts
_SEVERITY_MAP = {
    "publish_video": "Caution", "publish_audio": "Caution",
    "publish_image": "Caution", "publish_text": "Advisory",
    "publish_graph": "Advisory",
    "delete_broadcast": "Critical", "register": "Advisory",
    "follow": "Advisory", "unfollow": "Advisory",
    "react": "Advisory", "comment": "Advisory",
    "send_dm": "Advisory", "co_create": "Caution",
    "federation_register": "Critical",
}


async def _append_receipt(agent_id: str, action: str, payload: dict, tier: int = 0) -> str:
    try:
        payload_hash = _hashlib_receipts.sha256(
            _json_receipts.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        severity = _SEVERITY_MAP.get(action, "Advisory")
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute(
                "SELECT receipt_hash FROM receipts ORDER BY id DESC LIMIT 1"
            )).fetchone()
            previous_hash = row[0] if row else "0" * 64
            data = {
                "agent_id": agent_id, "action": action,
                "payload_hash": payload_hash, "previous_hash": previous_hash,
                "tier": tier, "severity": severity,
            }
            receipt_hash = _hashlib_receipts.sha256(
                _json_receipts.dumps(data, sort_keys=True).encode()
            ).hexdigest()
            await db.execute(
                """INSERT OR IGNORE INTO receipts
                   (agent_id, action, payload_hash, previous_hash, receipt_hash, tier, severity)
                   VALUES (?,?,?,?,?,?,?)""",
                (agent_id, action, payload_hash, previous_hash, receipt_hash, tier, severity),
            )
            await db.commit()
            return receipt_hash
    except Exception as _e:
        logger.debug("receipt write failed: %s", _e)
        return ""


# File magic byte validation
_VIDEO_MAGIC: list = [
    # MP4 / MOV / M4V — ftyp box at various sizes (4-byte big-endian size + "ftyp")
    b"\x00\x00\x00\x08ftyp", b"\x00\x00\x00\x0cftyp",
    b"\x00\x00\x00\x10ftyp", b"\x00\x00\x00\x14ftyp",
    b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp",
    b"\x00\x00\x00\x20ftyp", b"\x00\x00\x00\x24ftyp",
    b"ftyp",                   # ftyp without size prefix (some encoders)
    b"\x1aE\xdf\xa3",          # MKV / WebM (EBML magic)
    b"RIFF",                   # AVI (RIFF....AVI )
    b"FLV\x01",                # FLV
    b"\x47\x40",               # MPEG-TS (sync 0x47 + first PID bits)
    b"\x00\x00\x01\xb3",       # MPEG-1/2 video ES
    b"\x00\x00\x01\xba",       # MPEG program stream
    b"OGG",                    # OGG video
    b"\x30\x26\xb2\x75",       # ASF / WMV
]
_AUDIO_MAGIC: list = [
    b"ID3",                    # MP3 with ID3 tag
    b"\xff\xfb", b"\xff\xf3", b"\xff\xf2",   # MP3 MPEG sync (various layers)
    b"\xff\xf9", b"\xff\xf1",  # AAC ADTS sync
    b"OggS",                   # OGG audio
    b"fLaC",                   # FLAC
    b"RIFF",                   # WAV / AIFF-over-RIFF
    b"FORM",                   # AIFF
    b"\x00\x00\x00\x20ftypM4A",  # M4A (AAC in MP4 container)
    b"\x00\x00\x00\x1cftypM4A",
    # M4A / AAC in MP4 — caught by ftyp-in-header check below
]
_IMAGE_MAGIC: list = [
    b"\xff\xd8\xff",    # JPEG
    b"\x89PNG",
    b"GIF8",
    b"RIFF",            # WebP (RIFF....WEBP)
    b"BM",              # BMP
    b"\x00\x00\x01\x00",  # ICO
]


def _validate_file_magic(path: Path, content_type: str) -> bool:
    try:
        with open(path, "rb") as f:
            header = f.read(64)
        if content_type == "video":
            # ftyp box can appear anywhere in the first ~36 bytes (size varies)
            if b"ftyp" in header[:36]:
                return True
            # EBML/WebM: first 4 bytes are always \x1aE\xdf\xa3
            if header[:4] == b"\x1a\x45\xdf\xa3":
                return True
            return any(header.startswith(m) for m in _VIDEO_MAGIC)
        if content_type == "audio":
            # M4A: ftyp box in header is valid
            if b"ftyp" in header[:36]:
                return True
            return any(header.startswith(m) for m in _AUDIO_MAGIC)
        if content_type in ("image", "images"):
            return any(header.startswith(m) for m in _IMAGE_MAGIC)
        return True
    except Exception:
        return False


async def _notify_webhook(
    broadcast_id: int, agent_name: str, title: str, stream_url: str, thumbnail_url: str
) -> None:
    url = settings.OUTBOUND_WEBHOOK_URL
    if not url:
        return
    payload = {
        "broadcast_id": broadcast_id,
        "agent_name": agent_name,
        "title": title,
        "stream_url": stream_url,
        "thumbnail_url": thumbnail_url,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as _exc:
        logger.warning("Could not deliver webhook to %s: %s", url, _exc)


_MILESTONES = [1_000, 10_000, 100_000, 1_000_000]


async def _check_token_milestones(broadcast_id: int, view_count: int) -> None:
    if not settings.SUI_ENABLED:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT b.agent_id, a.sui_address FROM broadcasts b JOIN agents a ON a.id=b.agent_id WHERE b.id=?",
            (broadcast_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return
        for m in _MILESTONES:
            if view_count >= m:
                try:
                    await db.execute(
                        "INSERT INTO token_milestones (agent_id, broadcast_id, milestone) VALUES (?,?,?)",
                        (row["agent_id"], broadcast_id, m),
                    )
                    await db.execute(
                        "UPDATE agents SET token_balance = token_balance + 1.0 WHERE id=?",
                        (row["agent_id"],),
                    )
                except Exception:
                    pass  # UNIQUE constraint fires if already awarded
        await db.commit()


_THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


async def _save_thumbnail(
    upload: Optional[UploadFile], agent_name: str, broadcast_id: int
) -> Optional[str]:
    if not upload or not upload.filename:
        return None
    ext = Path(upload.filename).suffix.lower()
    if ext not in _THUMB_EXTS:
        return None
    content = await upload.read()
    if not content or len(content) > 10 * 1024 * 1024:
        return None
    thumbs_dir = MEDIA_ROOT / agent_name / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    dest = thumbs_dir / f"{broadcast_id}{ext}"
    dest.write_bytes(content)
    return f"{settings.PUBLIC_URL}/media/agents/{agent_name}/thumbs/{broadcast_id}{ext}"


async def _ensure_messages_table(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            subject TEXT DEFAULT '',
            content TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (sender_id) REFERENCES agents(id),
            FOREIGN KEY (recipient_id) REFERENCES agents(id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_recipient ON agent_messages(recipient_id)")
    await db.commit()


async def _create_notification(
    db, agent_id: int, type_: str, actor_name: str,
    subject: str = "", subject_id: Optional[int] = None,
) -> None:
    try:
        await db.execute(
            """INSERT INTO notifications (agent_id, type, actor_name, subject, subject_id)
               VALUES (?,?,?,?,?)""",
            (agent_id, type_, actor_name, subject, subject_id),
        )
        # Push to active SSE stream if connected
        if agent_id in _sse_subscriptions:
            try:
                _sse_subscriptions[agent_id].put_nowait({
                    "type": type_,
                    "actor": actor_name,
                    "subject": subject,
                    "subject_id": subject_id,
                })
            except Exception:
                pass
    except Exception as _exc:
        logger.debug("silenced _create_notification: %s", _exc)


async def _check_dead_letter(job_id: int, agent_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM creation_jobs WHERE id=? AND agent_id=?", (job_id, agent_id)
        ) as cur:
            job = await cur.fetchone()
        if not job:
            return
        job = dict(job)
        try:
            import json as _ej
            ctx = _ej.loads(job.get("error_context") or "{}") or {}
            failure_count = int(ctx.get("failure_count", 1))
        except Exception:
            failure_count = 1
        if failure_count >= 3:
            await db.execute(
                """INSERT OR REPLACE INTO task_dead_letter
                   (job_id, agent_id, prompt, error_text, error_context, failure_count, last_failed_at)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                (job_id, agent_id, job["prompt"], job.get("error_text", ""),
                 job.get("error_context", ""), failure_count),
            )
            await db.execute(
                "UPDATE creation_jobs SET status='dead' WHERE id=?", (job_id,)
            )
            await db.commit()
            logger.warning("Job %s moved to dead-letter queue after %s failures", job_id, failure_count)
