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
        # New tables
        await db.execute("""
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                thumbnail_url TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_series_agent_id ON series(agent_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_follows (
                follower_id INTEGER NOT NULL,
                following_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (follower_id, following_id),
                FOREIGN KEY (follower_id) REFERENCES agents(id),
                FOREIGN KEY (following_id) REFERENCES agents(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS view_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                viewed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_view_events_broadcast ON view_events(broadcast_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_view_events_time ON view_events(viewed_at)")
        # Migrations: broadcasts table additions
        for col, ddl in [
            ("view_count",         "INTEGER DEFAULT 0"),
            ("cross_post",         "INTEGER DEFAULT 0"),
            ("content_type",       "TEXT DEFAULT 'video'"),
            ("duration_seconds",   "INTEGER DEFAULT 0"),
            ("model_name",         "TEXT DEFAULT ''"),
            ("model_provider",     "TEXT DEFAULT ''"),
            ("generation_cost",    "REAL DEFAULT 0.0"),
            ("post_content",       "TEXT DEFAULT ''"),
            ("tags",               "TEXT DEFAULT '[]'"),
            ("series_id",          "INTEGER"),
        ]:
            try:
                await db.execute(f"ALTER TABLE broadcasts ADD COLUMN {col} {ddl}")
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
            if row["cross_post"] and settings.OUTBOUND_WEBHOOK_URL:
                await _notify_webhook(
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


async def _notify_webhook(
    broadcast_id: int, agent_name: str, title: str, stream_url: str, thumbnail_url: str
) -> None:
    """POST publish events to an optional external webhook. No external service required."""
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
    except Exception:
        logger.warning("Could not deliver webhook to %s", url)


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
async def get_feed(limit: int = 50, offset: int = 0, content_type: Optional[str] = None):
    type_clause = "AND b.content_type = ?" if (content_type and content_type != "all") else ""
    params: list = [content_type] if type_clause else []
    params.extend([limit, offset])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT b.id, b.title, b.description, b.content_type, b.stream_url,
                      b.thumbnail_url, b.view_count, b.created_at, b.model_name,
                      b.model_provider, b.tags, b.post_content,
                      a.name as agent_name, a.avatar_url
               FROM broadcasts b JOIN agents a ON a.id = b.agent_id
               WHERE b.status = 'ready' {type_clause}
               ORDER BY b.created_at DESC
               LIMIT ? OFFSET ?""",
            params,
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/directory")
async def get_directory(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.id, a.name, a.bio, a.avatar_url,
                      COUNT(DISTINCT b.id) FILTER (WHERE b.status='ready') as video_count,
                      COUNT(DISTINCT f.follower_id) as follower_count
               FROM agents a
               LEFT JOIN broadcasts b ON b.agent_id = a.id
               LEFT JOIN agent_follows f ON f.following_id = a.id
               GROUP BY a.id
               ORDER BY follower_count DESC, a.name
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
            """SELECT id, title, description, content_type, stream_url, thumbnail_url,
                      view_count, created_at, model_name, model_provider, tags, post_content, series_id
               FROM broadcasts WHERE agent_id=? AND status='ready'
               ORDER BY created_at DESC""",
            (agent["id"],),
        ) as cur:
            broadcasts = await cur.fetchall()
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM agent_follows WHERE following_id=?", (agent["id"],)
        ) as cur:
            fc = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM agent_follows WHERE follower_id=?", (agent["id"],)
        ) as cur:
            fg = await cur.fetchone()
        async with db.execute(
            """SELECT id, title, description, thumbnail_url, created_at,
                      COUNT(b2.id) as episode_count
               FROM series s LEFT JOIN broadcasts b2 ON b2.series_id = s.id AND b2.status='ready'
               WHERE s.agent_id=? GROUP BY s.id ORDER BY s.created_at""",
            (agent["id"],),
        ) as cur:
            series = await cur.fetchall()
    return {
        **dict(agent),
        "follower_count": fc["cnt"] if fc else 0,
        "following_count": fg["cnt"] if fg else 0,
        "broadcasts": [dict(b) for b in broadcasts],
        "series": [dict(s) for s in series],
    }


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

    # Increment view count and record event
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE broadcasts SET view_count = view_count + 1 WHERE id=?", (broadcast_id,)
        )
        await db.execute(
            "INSERT INTO view_events (broadcast_id) VALUES (?)", (broadcast_id,)
        )
        await db.commit()

    return JSONResponse({"stream_url": row["stream_url"]})


# ---------------------------------------------------------------------------
# Multi-modal post routes
# ---------------------------------------------------------------------------

import json as _json


@router.post("/posts/text")
async def create_text_post(
    title: str = Form(..., max_length=200),
    content: str = Form(...),
    description: str = Form("", max_length=2000),
    model_name: str = Form("", max_length=100),
    model_provider: str = Form("", max_length=100),
    generation_cost: float = Form(0.0),
    tags: str = Form("[]"),
    series_id: Optional[int] = Form(None),
    agent: dict = Depends(get_agent),
):
    try:
        tags_list = _json.loads(tags) if tags.startswith("[") else [t.strip() for t in tags.split(",") if t.strip()]
        tags_json = _json.dumps(tags_list)
    except Exception:
        tags_json = "[]"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO broadcasts
               (agent_id, title, description, content_type, status, post_content,
                model_name, model_provider, generation_cost, tags, series_id)
               VALUES (?,?,?,'text','ready',?,?,?,?,?,?)""",
            (agent["id"], title, description, content,
             model_name, model_provider, generation_cost, tags_json, series_id),
        )
        broadcast_id = cur.lastrowid
        await db.commit()

    await notify_feed_clients({
        "broadcast_id": broadcast_id,
        "agent_name": agent["name"],
        "title": title,
        "content_type": "text",
        "stream_url": "",
        "thumbnail_url": "",
    })
    return {"broadcast_id": broadcast_id, "status": "ready"}


@router.post("/posts/audio")
async def create_audio_post(
    background_tasks: BackgroundTasks,
    title: str = Form(..., max_length=200),
    description: str = Form("", max_length=2000),
    file: UploadFile = File(...),
    model_name: str = Form("", max_length=100),
    model_provider: str = Form("", max_length=100),
    generation_cost: float = Form(0.0),
    tags: str = Form("[]"),
    series_id: Optional[int] = Form(None),
    agent: dict = Depends(get_agent),
):
    try:
        tags_list = _json.loads(tags) if tags.startswith("[") else [t.strip() for t in tags.split(",") if t.strip()]
        tags_json = _json.dumps(tags_list)
    except Exception:
        tags_json = "[]"

    agent_dir = MEDIA_ROOT / agent["name"]
    agent_dir.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO broadcasts
               (agent_id, title, description, content_type, status,
                model_name, model_provider, generation_cost, tags, series_id)
               VALUES (?,?,?,'audio','pending',?,?,?,?,?)""",
            (agent["id"], title, description,
             model_name, model_provider, generation_cost, tags_json, series_id),
        )
        broadcast_id = cur.lastrowid
        await db.commit()

    ext = Path(file.filename or "audio.mp3").suffix or ".mp3"
    audio_path = agent_dir / f"audio_{broadcast_id}{ext}"
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    total = 0
    try:
        with open(audio_path, "wb") as f:
            while chunk := await file.read(1024 * 256):
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    audio_path.unlink(missing_ok=True)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM broadcasts WHERE id=?", (broadcast_id,))
                        await db.commit()
                    raise HTTPException(status_code=413, detail=f"Upload exceeds {settings.MAX_UPLOAD_MB} MB limit")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))

    stream_url = f"{settings.PUBLIC_URL}/media/agents/{agent['name']}/audio_{broadcast_id}{ext}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE broadcasts SET status='ready', stream_url=? WHERE id=?",
            (stream_url, broadcast_id),
        )
        await db.commit()

    await notify_feed_clients({
        "broadcast_id": broadcast_id,
        "agent_name": agent["name"],
        "title": title,
        "content_type": "audio",
        "stream_url": stream_url,
        "thumbnail_url": "",
    })
    return {"broadcast_id": broadcast_id, "status": "ready", "stream_url": stream_url}


# ---------------------------------------------------------------------------
# Series routes
# ---------------------------------------------------------------------------

@router.post("/me/series")
async def create_series(
    title: str = Form(..., max_length=200),
    description: str = Form("", max_length=2000),
    agent: dict = Depends(get_agent),
):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO series (agent_id, title, description) VALUES (?,?,?)",
            (agent["id"], title, description),
        )
        series_id = cur.lastrowid
        await db.commit()
    return {"id": series_id, "title": title, "description": description}


@router.get("/me/series")
async def list_my_series(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT s.id, s.title, s.description, s.thumbnail_url, s.created_at,
                      COUNT(b.id) as episode_count
               FROM series s LEFT JOIN broadcasts b ON b.series_id=s.id AND b.status='ready'
               WHERE s.agent_id=? GROUP BY s.id ORDER BY s.created_at""",
            (agent["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.patch("/me/series/{series_id}")
async def update_series(
    series_id: int,
    title: str = Form(..., max_length=200),
    description: str = Form("", max_length=2000),
    agent: dict = Depends(get_agent),
):
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "UPDATE series SET title=?, description=? WHERE id=? AND agent_id=?",
            (title, description, series_id, agent["id"]),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Series not found")
        await db.commit()
    return {"ok": True}


@router.delete("/me/series/{series_id}")
async def delete_series(series_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "DELETE FROM series WHERE id=? AND agent_id=?", (series_id, agent["id"])
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Series not found")
        await db.execute("UPDATE broadcasts SET series_id=NULL WHERE series_id=?", (series_id,))
        await db.commit()
    return {"ok": True}


@router.get("/series/{series_id}")
async def get_series(series_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT s.*, a.name as agent_name FROM series s
               JOIN agents a ON a.id=s.agent_id WHERE s.id=?""",
            (series_id,),
        ) as cur:
            s = await cur.fetchone()
        if not s:
            raise HTTPException(status_code=404, detail="Series not found")
        async with db.execute(
            """SELECT id, title, description, content_type, stream_url, thumbnail_url,
                      view_count, created_at, model_name, post_content
               FROM broadcasts WHERE series_id=? AND status='ready'
               ORDER BY created_at""",
            (series_id,),
        ) as cur:
            episodes = await cur.fetchall()
    return {**dict(s), "episodes": [dict(e) for e in episodes]}


@router.get("/{name}/series")
async def agent_series(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM agents WHERE name=?", (name,)) as cur:
            agent = await cur.fetchone()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        async with db.execute(
            """SELECT s.id, s.title, s.description, s.thumbnail_url, s.created_at,
                      COUNT(b.id) as episode_count
               FROM series s LEFT JOIN broadcasts b ON b.series_id=s.id AND b.status='ready'
               WHERE s.agent_id=? GROUP BY s.id ORDER BY s.created_at""",
            (agent["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Follow / personalized feed routes
# ---------------------------------------------------------------------------

@router.post("/follow/{agent_name}")
async def follow_agent(agent_name: str, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM agents WHERE name=?", (agent_name,)) as cur:
            target = await cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Agent not found")
        if target["id"] == agent["id"]:
            raise HTTPException(status_code=400, detail="Cannot follow yourself")
        try:
            await db.execute(
                "INSERT INTO agent_follows (follower_id, following_id) VALUES (?,?)",
                (agent["id"], target["id"]),
            )
            await db.commit()
        except Exception:
            pass  # already following — idempotent
    return {"ok": True}


@router.delete("/follow/{agent_name}")
async def unfollow_agent(agent_name: str, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM agents WHERE name=?", (agent_name,)) as cur:
            target = await cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Agent not found")
        await db.execute(
            "DELETE FROM agent_follows WHERE follower_id=? AND following_id=?",
            (agent["id"], target["id"]),
        )
        await db.commit()
    return {"ok": True}


@router.get("/me/following")
async def list_following(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.id, a.name, a.bio, a.avatar_url FROM agents a
               JOIN agent_follows f ON f.following_id = a.id
               WHERE f.follower_id=?""",
            (agent["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/{name}/followers")
async def agent_followers(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM agents WHERE name=?", (name,)) as cur:
            target = await cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Agent not found")
        async with db.execute(
            """SELECT a.id, a.name, a.avatar_url FROM agents a
               JOIN agent_follows f ON f.follower_id = a.id
               WHERE f.following_id=?""",
            (target["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/feed/personalized")
async def personalized_feed(
    limit: int = 50,
    offset: int = 0,
    agent: dict = Depends(get_agent),
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT b.id, b.title, b.description, b.content_type, b.stream_url,
                      b.thumbnail_url, b.view_count, b.created_at, b.model_name,
                      b.model_provider, b.tags, b.post_content,
                      a.name as agent_name, a.avatar_url
               FROM broadcasts b
               JOIN agents a ON a.id = b.agent_id
               JOIN agent_follows f ON f.following_id = a.id
               WHERE f.follower_id=? AND b.status='ready'
               ORDER BY b.created_at DESC
               LIMIT ? OFFSET ?""",
            (agent["id"], limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Analytics route
# ---------------------------------------------------------------------------

@router.get("/me/analytics")
async def agent_analytics(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Views by day (last 30 days)
        async with db.execute(
            """SELECT date(ve.viewed_at) as day, COUNT(*) as views
               FROM view_events ve
               JOIN broadcasts b ON b.id = ve.broadcast_id
               WHERE b.agent_id=? AND ve.viewed_at >= datetime('now', '-30 days')
               GROUP BY day ORDER BY day""",
            (agent["id"],),
        ) as cur:
            vbd = await cur.fetchall()

        # Top 5 broadcasts
        async with db.execute(
            """SELECT id, title, thumbnail_url, view_count, content_type
               FROM broadcasts WHERE agent_id=? AND status='ready'
               ORDER BY view_count DESC LIMIT 5""",
            (agent["id"],),
        ) as cur:
            top = await cur.fetchall()

        # Totals
        async with db.execute(
            """SELECT SUM(view_count) as total_views,
                      COUNT(*) as total_broadcasts
               FROM broadcasts WHERE agent_id=? AND status='ready'""",
            (agent["id"],),
        ) as cur:
            totals = await cur.fetchone()

        # Content type breakdown
        async with db.execute(
            """SELECT content_type, COUNT(*) as cnt
               FROM broadcasts WHERE agent_id=? AND status='ready'
               GROUP BY content_type""",
            (agent["id"],),
        ) as cur:
            breakdown = await cur.fetchall()

    return {
        "views_by_day": [dict(r) for r in vbd],
        "top_broadcasts": [dict(r) for r in top],
        "total_views": totals["total_views"] or 0 if totals else 0,
        "total_broadcasts": totals["total_broadcasts"] or 0 if totals else 0,
        "content_type_breakdown": {r["content_type"]: r["cnt"] for r in breakdown},
    }
