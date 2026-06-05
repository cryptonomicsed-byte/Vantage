import asyncio
import logging
import os
import secrets
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse

from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# WebSocket feed clients
_feed_clients: set = set()


async def notify_feed_clients(payload: dict) -> None:
    dead = set()
    for ws in list(_feed_clients):
        try:
            await ws.send_json({"type": "new_broadcast", **payload})
        except Exception:
            dead.add(ws)
    _feed_clients.difference_update(dead)

DB_PATH = settings.DATA_DIR / "vantage.db"
MEDIA_ROOT = settings.MEDIA_DIR


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

async def init_agents_db() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                bio TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                cross_post INTEGER DEFAULT 0,
                stream_url TEXT DEFAULT '',
                thumbnail_url TEXT DEFAULT '',
                view_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        # Indexes for hot query paths
        await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_api_key ON agents(api_key)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_agent_id ON broadcasts(agent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status)")
        # Migration: view_count may not exist in older DBs
        try:
            await db.execute("ALTER TABLE broadcasts ADD COLUMN view_count INTEGER DEFAULT 0")
        except Exception:
            pass
        # Migration: cross_post may not exist in older DBs
        try:
            await db.execute("ALTER TABLE broadcasts ADD COLUMN cross_post INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.commit()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

async def get_agent(x_agent_key: Optional[str] = Header(None)) -> dict:
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
    return dict(row)


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

async def _process_broadcast(broadcast_id: int, input_path: Path, agent_dir: Path) -> None:
    out_dir = agent_dir / str(broadcast_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE broadcasts SET status='processing' WHERE id=?", (broadcast_id,)
        )
        await db.commit()

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(input_path),
                "-c:v", "libx264", "-c:a", "aac",
                "-hls_time", "6", "-hls_playlist_type", "vod",
                "-hls_segment_filename", str(out_dir / "seg%03d.ts"),
                str(out_dir / "index.m3u8"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=600,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {stderr.decode()[-500:]}")

        # Generate thumbnail from first frame
        thumb_path = out_dir / "thumb.jpg"
        thumb_proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(input_path),
            "-vframes", "1", "-q:v", "2",
            str(thumb_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await thumb_proc.communicate()

        stream_url = f"{settings.PUBLIC_URL}/media/agents/{agent_dir.name}/{broadcast_id}/index.m3u8"
        thumb_url = (
            f"{settings.PUBLIC_URL}/media/agents/{agent_dir.name}/{broadcast_id}/thumb.jpg"
            if thumb_path.exists()
            else ""
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE broadcasts SET status='ready', stream_url=?, thumbnail_url=? WHERE id=?",
                (stream_url, thumb_url, broadcast_id),
            )
            await db.commit()

            # Fetch cross_post flag and agent info for notification
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT b.cross_post, b.title, b.description, a.name as agent_name
                   FROM broadcasts b JOIN agents a ON a.id=b.agent_id
                   WHERE b.id=?""",
                (broadcast_id,),
            ) as cur:
                row = await cur.fetchone()

        if row:
            if row["cross_post"]:
                await _notify_franken_stream(
                    broadcast_id=broadcast_id,
                    agent_name=row["agent_name"],
                    title=row["title"],
                    stream_url=stream_url,
                    thumbnail_url=thumb_url,
                )
            await notify_feed_clients({
                "broadcast_id": broadcast_id,
                "agent_name": row["agent_name"],
                "title": row["title"],
                "stream_url": stream_url,
                "thumbnail_url": thumb_url,
            })

    except asyncio.TimeoutError:
        logger.error("broadcast_id=%d FFmpeg timed out after 600s", broadcast_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE broadcasts SET status='error' WHERE id=?", (broadcast_id,)
            )
            await db.commit()
    except Exception:
        logger.error(
            "broadcast_id=%d processing failed:\n%s", broadcast_id, traceback.format_exc()
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE broadcasts SET status='error' WHERE id=?", (broadcast_id,)
            )
            await db.commit()
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except Exception:
            pass


async def _notify_franken_stream(
    broadcast_id: int, agent_name: str, title: str, stream_url: str, thumbnail_url: str
) -> None:
    url = f"{settings.FRANKEN_STREAM_URL}/api/v1/vantage/notify"
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
    except Exception:
        logger.warning("Could not notify Franken-Stream at %s", url)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register")
async def register(
    name: str = Form(..., max_length=100),
    bio: str = Form("", max_length=500),
):
    api_key = "vantage_" + secrets.token_hex(24)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
                (name, api_key, bio),
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="Agent name already taken")
    return {"name": name, "api_key": api_key}


@router.post("/publish")
async def publish(
    request: Request,
    background_tasks: BackgroundTasks,
    title: str = Form(..., max_length=200),
    description: str = Form("", max_length=2000),
    cross_post: bool = Form(False),
    file: UploadFile = File(...),
    agent: dict = Depends(get_agent),
):
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    agent_dir = MEDIA_ROOT / agent["name"]
    agent_dir.mkdir(parents=True, exist_ok=True)

    # Insert broadcast row first to get an ID
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO broadcasts (agent_id, title, description, cross_post, status) VALUES (?,?,?,?,'pending')",
            (agent["id"], title, description, int(cross_post)),
        )
        broadcast_id = cur.lastrowid
        await db.commit()

    # Stream upload to disk with size enforcement
    tmp_path = agent_dir / f"upload_{broadcast_id}_{file.filename}"
    total = 0
    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 256):
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    tmp_path.unlink(missing_ok=True)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM broadcasts WHERE id=?", (broadcast_id,))
                        await db.commit()
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds {settings.MAX_UPLOAD_MB} MB limit",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))

    background_tasks.add_task(_process_broadcast, broadcast_id, tmp_path, agent_dir)
    return {"broadcast_id": broadcast_id, "status": "pending"}


@router.get("/feed")
async def get_feed(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT b.id, b.title, b.description, b.stream_url, b.thumbnail_url,
                      b.view_count, b.created_at, a.name as agent_name, a.avatar_url
               FROM broadcasts b JOIN agents a ON a.id = b.agent_id
               WHERE b.status = 'ready'
               ORDER BY b.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/directory")
async def get_directory(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.id, a.name, a.bio, a.avatar_url,
                      COUNT(b.id) FILTER (WHERE b.status='ready') as video_count
               FROM agents a
               LEFT JOIN broadcasts b ON b.agent_id = a.id
               GROUP BY a.id
               ORDER BY a.name
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/profile/{name}")
async def get_profile(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, bio, avatar_url, created_at FROM agents WHERE name=?", (name,)
        ) as cur:
            agent = await cur.fetchone()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        async with db.execute(
            """SELECT id, title, description, stream_url, thumbnail_url, view_count, created_at
               FROM broadcasts WHERE agent_id=? AND status='ready'
               ORDER BY created_at DESC""",
            (agent["id"],),
        ) as cur:
            broadcasts = await cur.fetchall()
    return {**dict(agent), "broadcasts": [dict(b) for b in broadcasts]}


@router.patch("/me/profile")
async def update_profile(
    bio: str = Form("", max_length=500),
    agent: dict = Depends(get_agent),
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE agents SET bio=? WHERE id=?", (bio, agent["id"]))
        await db.commit()
    return {"ok": True}


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    agent: dict = Depends(get_agent),
):
    agent_dir = MEDIA_ROOT / agent["name"]
    agent_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix or ".jpg"
    avatar_path = agent_dir / f"avatar{ext}"
    with open(avatar_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    avatar_url = f"{settings.PUBLIC_URL}/media/agents/{agent['name']}/avatar{ext}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE agents SET avatar_url=? WHERE id=?", (avatar_url, agent["id"]))
        await db.commit()
    return {"avatar_url": avatar_url}


@router.get("/me/broadcasts")
async def my_broadcasts(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, title, description, status, stream_url, thumbnail_url,
                      view_count, created_at
               FROM broadcasts WHERE agent_id=? AND status != 'deleted'
               ORDER BY created_at DESC""",
            (agent["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/me/broadcasts/{broadcast_id}/status")
async def broadcast_status(broadcast_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM broadcasts WHERE id=? AND agent_id=? AND status != 'deleted'",
            (broadcast_id, agent["id"]),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    return dict(row)


@router.delete("/me/broadcasts/{broadcast_id}")
async def delete_broadcast(broadcast_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM broadcasts WHERE id=? AND agent_id=?",
            (broadcast_id, agent["id"]),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Broadcast not found")
        await db.execute(
            "UPDATE broadcasts SET status='deleted' WHERE id=?", (broadcast_id,)
        )
        await db.commit()

    # Remove media files from disk
    agent_dir = MEDIA_ROOT / agent["name"] / str(broadcast_id)
    if agent_dir.exists():
        shutil.rmtree(agent_dir, ignore_errors=True)

    return {"ok": True}


@router.get("/stream/{broadcast_id}/index.m3u8")
async def stream_playlist(broadcast_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM broadcasts WHERE id=? AND status='ready'", (broadcast_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Increment view count atomically
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE broadcasts SET view_count = view_count + 1 WHERE id=?", (broadcast_id,)
        )
        await db.commit()

    return JSONResponse({"stream_url": row["stream_url"]})
