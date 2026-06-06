import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .agents import init_agents_db, router as agents_router, DB_PATH, _feed_clients
from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
FFMPEG_AVAILABLE = False


async def _scheduled_publish_loop():
    """Background loop: publish broadcasts whose publish_at time has passed."""
    from .agents import DB_PATH as _DB_PATH, notify_feed_clients as _notify
    import json as _json
    while True:
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """SELECT b.id, b.title, b.content_type, b.thumbnail_url, b.stream_url,
                              a.name as agent_name
                       FROM broadcasts b JOIN agents a ON a.id = b.agent_id
                       WHERE b.status = 'scheduled'
                         AND b.publish_at <= datetime('now')""",
                ) as cur:
                    due = await cur.fetchall()
                for row in due:
                    await db.execute(
                        "UPDATE broadcasts SET status='ready' WHERE id=?", (row["id"],)
                    )
                    await db.commit()
                    await _notify({
                        "broadcast_id": row["id"],
                        "agent_name": row["agent_name"],
                        "title": row["title"],
                        "content_type": row["content_type"],
                        "thumbnail_url": row["thumbnail_url"] or "",
                        "stream_url": row["stream_url"] or "",
                    })
                    logger.info("Scheduled broadcast %s published", row["id"])
        except Exception as exc:
            logger.warning("Scheduled-publish loop error: %s", exc)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global FFMPEG_AVAILABLE
    await init_agents_db()

    # Check FFmpeg availability on startup
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        FFMPEG_AVAILABLE = proc.returncode == 0
    except FileNotFoundError:
        FFMPEG_AVAILABLE = False

    if not FFMPEG_AVAILABLE:
        logger.warning("FFmpeg not found — video transcoding will fail")
    else:
        logger.info("FFmpeg available")

    task = asyncio.create_task(_scheduled_publish_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request ID + structured logging middleware
@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        '{"request_id":"%s","method":"%s","path":"%s","status":%d,"duration_ms":%.1f}',
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(duration_ms)
    return response


app.include_router(agents_router)


@app.websocket("/ws/feed")
async def feed_ws(ws: WebSocket):
    await ws.accept()
    _feed_clients.add(ws)
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, Exception):
        _feed_clients.discard(ws)


@app.get("/api/health")
async def health():
    db_ok = False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if (db_ok and FFMPEG_AVAILABLE) else "degraded",
        "db": "ok" if db_ok else "error",
        "ffmpeg": "ok" if FFMPEG_AVAILABLE else "missing",
        "version": settings.VERSION,
    }


# Serve media files
settings.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/agents", StaticFiles(directory=str(settings.MEDIA_DIR)), name="media")

# Serve frontend (must be last)
if settings.WEBUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(settings.WEBUI_DIR), html=True), name="frontend")
