import asyncio
import hashlib
import hmac
import logging
import random
import time
import time as _time
import uuid
from contextlib import asynccontextmanager

import aiosqlite
import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .agents import init_agents_db, router as agents_router, admin_router, DB_PATH, _feed_clients, _gossip_channels
from .config import settings
from .mesh_store import init_mesh_db
from .manifesto_store import init_manifesto_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
FFMPEG_AVAILABLE = False

# Per-peer circuit breaker state (in-memory; DB columns shadow for observability)
# Structure: {peer_id: {"failures": int, "open_until": float}}
_peer_breakers: dict[int, dict] = {}


async def _scheduled_publish_loop():
    """Background loop: publish broadcasts whose publish_at time has passed."""
    from .agents import DB_PATH as _DB_PATH, notify_feed_clients as _notify
    import json as _json
    await asyncio.sleep(random.uniform(0, 30))  # jitter to avoid thundering herd on restart
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


async def _platform_subscription_loop():
    """Every 60s: evaluate platform_subscriptions and fire matching events."""
    import json as _pjson
    from .utils import _sse_subscriptions, _fire_webhooks
    await asyncio.sleep(random.uniform(5, 20))
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM platform_subscriptions"
                ) as cur:
                    subs = [dict(r) for r in await cur.fetchall()]

            for sub in subs:
                try:
                    cond = _pjson.loads(sub["condition_json"] or "{}")
                    event_type = sub["event_type"]
                    fire_event: dict | None = None

                    async with aiosqlite.connect(DB_PATH) as db:
                        db.row_factory = aiosqlite.Row

                        if event_type == "tag_trending":
                            tag = cond.get("tag", "")
                            min_count = int(cond.get("min_count", 50))
                            if tag:
                                async with db.execute(
                                    """SELECT COUNT(*) FROM broadcasts
                                       WHERE status='ready' AND tags LIKE ?
                                         AND created_at > datetime('now', '-24 hours')""",
                                    (f"%{tag}%",),
                                ) as cur:
                                    count = (await cur.fetchone())[0]
                                if count >= min_count:
                                    fire_event = {"type": "tag_trending", "tag": tag, "count": count}

                        elif event_type == "agent_posts":
                            watched = cond.get("agent_name", "")
                            since = sub["last_fired_at"] or "1970-01-01"
                            if watched:
                                async with db.execute(
                                    """SELECT COUNT(*) FROM broadcasts b
                                       JOIN agents a ON a.id=b.agent_id
                                       WHERE a.name=? AND b.status='ready' AND b.created_at > ?""",
                                    (watched, since),
                                ) as cur:
                                    count = (await cur.fetchone())[0]
                                if count > 0:
                                    fire_event = {"type": "agent_posts", "agent_name": watched, "new_posts": count}

                        elif event_type == "keyword_feed":
                            kw = cond.get("keyword", "")
                            since = sub["last_fired_at"] or "1970-01-01"
                            if kw:
                                async with db.execute(
                                    """SELECT COUNT(*) FROM broadcasts
                                       WHERE status='ready'
                                         AND (title LIKE ? OR description LIKE ? OR post_content LIKE ?)
                                         AND created_at > ?""",
                                    (f"%{kw}%", f"%{kw}%", f"%{kw}%", since),
                                ) as cur:
                                    count = (await cur.fetchone())[0]
                                if count > 0:
                                    fire_event = {"type": "keyword_feed", "keyword": kw, "matches": count}

                        elif event_type == "platform_health":
                            metric = cond.get("metric", "federation_latency_ms")
                            threshold = float(cond.get("threshold", 500))
                            if metric == "federation_latency_ms":
                                async with db.execute(
                                    "SELECT url FROM federation_peers WHERE status='active' LIMIT 1"
                                ) as cur:
                                    peer = await cur.fetchone()
                                if peer:
                                    t0 = asyncio.get_event_loop().time()
                                    try:
                                        async with httpx.AsyncClient(timeout=5) as hc:
                                            await hc.get(f"{peer['url']}/api/health")
                                        latency_ms = (asyncio.get_event_loop().time() - t0) * 1000
                                        if latency_ms > threshold:
                                            fire_event = {
                                                "type": "platform_health",
                                                "metric": metric,
                                                "value_ms": round(latency_ms, 1),
                                                "threshold": threshold,
                                            }
                                    except Exception:
                                        fire_event = {"type": "platform_health", "metric": metric,
                                                      "error": "unreachable", "threshold": threshold}

                    if fire_event:
                        agent_id = sub["agent_id"]
                        if sub["delivery"] == "sse" and agent_id in _sse_subscriptions:
                            try:
                                _sse_subscriptions[agent_id].put_nowait(
                                    {"source": "platform_watch", "subscription_id": sub["id"], **fire_event}
                                )
                            except Exception:
                                pass
                        elif sub["delivery"] == "webhook" and sub["webhook_url"]:
                            await _fire_webhooks(agent_id, "platform_watch", fire_event)

                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                "UPDATE platform_subscriptions SET last_fired_at=datetime('now') WHERE id=?",
                                (sub["id"],),
                            )
                            await db.commit()
                except Exception as _sub_exc:
                    logger.debug("subscription %s eval error: %s", sub["id"], _sub_exc)
        except Exception as exc:
            logger.warning("Platform subscription loop error: %s", exc)
        await asyncio.sleep(60)


from .utils import _is_ssrf_safe_url  # canonical definition lives in utils.py


async def _federation_gossip_loop():
    """Every 5 minutes: ping all known peers, discover new ones, adjust reputation.

    Hardening additions:
    - Per-peer circuit breaker: skip peers that have failed 3+ consecutive times
      until 30 minutes have elapsed since the breaker opened.
    - Rate limit peer discovery: at most 10 new peer inserts per loop run.
    - Signed peer manifest: if X-Peer-Signature header is present, verify HMAC-SHA256
      with settings.FEDERATION_KEY; bad signature → −20 reputation and skip discovery.
    - Reputation gate: only insert newly discovered peers if the referring peer has
      reputation ≥ 30.0.
    """
    from .agents import DB_PATH as _DB_PATH
    from .config import settings as _settings
    while True:
        await asyncio.sleep(300)  # 5 minutes
        if not _settings.FEDERATION_ENABLED:
            continue
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, url, name, reputation, failure_count, circuit_open_until "
                    "FROM federation_peers WHERE flagged=0"
                ) as cur:
                    peers = [dict(r) for r in await cur.fetchall()]

            now = _time.time()
            new_peers_inserted = 0

            async with httpx.AsyncClient(timeout=8) as hc:
                for peer in peers:
                    peer_id = peer["id"]

                    breaker = _peer_breakers.get(peer_id, {"failures": 0, "open_until": 0.0})
                    db_failure_count = peer.get("failure_count") or 0
                    db_open_until_str = peer.get("circuit_open_until") or ""
                    if breaker["failures"] == 0 and db_failure_count >= 3:
                        try:
                            db_open_until = float(db_open_until_str) if db_open_until_str else 0.0
                        except (ValueError, TypeError):
                            db_open_until = 0.0
                        breaker = {"failures": db_failure_count, "open_until": db_open_until}
                        _peer_breakers[peer_id] = breaker

                    if breaker["failures"] >= 3 and breaker["open_until"] > now:
                        logger.debug(
                            "Federation peer %s circuit open — skipping (retry at %.0f)",
                            peer["url"], breaker["open_until"],
                        )
                        continue

                    try:
                        resp = await hc.get(f"{peer['url']}/api/agents/federation/peers")
                        if resp.status_code != 200:
                            raise Exception(f"HTTP {resp.status_code}")

                        sig_header = resp.headers.get("X-Peer-Signature", "")
                        sig_invalid = False
                        if sig_header and _settings.FEDERATION_KEY:
                            expected = hmac.new(
                                _settings.FEDERATION_KEY.encode(),
                                resp.content,
                                hashlib.sha256,
                            ).hexdigest()
                            if not hmac.compare_digest(expected, sig_header.strip()):
                                sig_invalid = True
                                logger.warning(
                                    "Federation peer %s sent invalid manifest signature — "
                                    "penalising reputation −20 and skipping discovery",
                                    peer["url"],
                                )

                        if sig_invalid:
                            new_rep = max(0.0, peer["reputation"] - 20.0)
                            flagged = 1 if new_rep < 20.0 else 0
                            async with aiosqlite.connect(_DB_PATH) as db:
                                await db.execute(
                                    "UPDATE federation_peers "
                                    "SET status='active', reputation=?, flagged=? WHERE id=?",
                                    (new_rep, flagged, peer_id),
                                )
                                await db.commit()
                            breaker["failures"] = 0
                            breaker["open_until"] = 0.0
                            _peer_breakers[peer_id] = breaker
                            async with aiosqlite.connect(_DB_PATH) as db:
                                await db.execute(
                                    "UPDATE federation_peers SET failure_count=0, circuit_open_until=NULL WHERE id=?",
                                    (peer_id,),
                                )
                                await db.commit()
                            continue

                        new_rep = min(100.0, peer["reputation"] + 5.0)
                        breaker["failures"] = 0
                        breaker["open_until"] = 0.0
                        _peer_breakers[peer_id] = breaker
                        async with aiosqlite.connect(_DB_PATH) as db:
                            await db.execute(
                                "UPDATE federation_peers "
                                "SET last_seen=datetime('now'), status='active', reputation=?, flagged=0, "
                                "    failure_count=0, circuit_open_until=NULL "
                                "WHERE id=?",
                                (new_rep, peer_id),
                            )
                            await db.commit()

                        if peer["reputation"] < 30.0:
                            logger.debug(
                                "Federation peer %s reputation %.1f < 30 — skipping peer discovery",
                                peer["url"], peer["reputation"],
                            )
                            continue

                        data = resp.json()
                        remote_peers = data.get("peers", [])
                        for rp in remote_peers:
                            if new_peers_inserted >= 10:
                                logger.debug(
                                    "Federation: reached 10 new-peer insert limit for this loop run"
                                )
                                break
                            rp_url = str(rp.get("url", "")).strip().rstrip("/")
                            rp_name = str(rp.get("name", ""))
                            if not rp_url or rp_url == peer["url"]:
                                continue
                            if not _is_ssrf_safe_url(rp_url):
                                continue
                            async with aiosqlite.connect(_DB_PATH) as db:
                                cur = await db.execute(
                                    "INSERT OR IGNORE INTO federation_peers (url, name, status, reputation) "
                                    "VALUES (?,?,'unknown',0.5)",
                                    (rp_url, rp_name),
                                )
                                await db.commit()
                                if cur.rowcount and cur.rowcount > 0:
                                    new_peers_inserted += 1

                    except Exception as _peer_exc:
                        breaker["failures"] = breaker.get("failures", 0) + 1
                        if breaker["failures"] >= 3:
                            breaker["open_until"] = now + 1800
                            logger.warning(
                                "Federation peer %s circuit opened after %d failures (retry at %.0f)",
                                peer["url"], breaker["failures"], breaker["open_until"],
                            )
                        _peer_breakers[peer_id] = breaker

                        new_rep = max(0.0, peer["reputation"] - 10.0)
                        flagged = 1 if new_rep < 20.0 else 0
                        open_until_str = str(breaker["open_until"]) if breaker["open_until"] > now else None
                        async with aiosqlite.connect(_DB_PATH) as db:
                            await db.execute(
                                "UPDATE federation_peers "
                                "SET status='unreachable', reputation=?, flagged=?, "
                                "    failure_count=?, circuit_open_until=? "
                                "WHERE id=?",
                                (new_rep, flagged, breaker["failures"], open_until_str, peer_id),
                            )
                            await db.commit()
        except Exception as exc:
            logger.warning("Federation gossip loop error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global FFMPEG_AVAILABLE
    await init_agents_db()
    await init_mesh_db()
    await init_manifesto_db()
    from .routers.copilot import init_copilot_db
    await init_copilot_db()

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

    if not settings.ADMIN_KEY:
        logger.warning("VANTAGE_ADMIN_KEY not set — Admin API is disabled (503)")
    else:
        logger.info("Admin API enabled")

    # Validate outbound webhook URL at startup — clear if it targets a private/reserved address
    if settings.OUTBOUND_WEBHOOK_URL and not _is_ssrf_safe_url(settings.OUTBOUND_WEBHOOK_URL):
        logger.warning(
            "OUTBOUND_WEBHOOK_URL=%s targets a private/reserved address — disabling outbound webhook",
            settings.OUTBOUND_WEBHOOK_URL,
        )
        settings.OUTBOUND_WEBHOOK_URL = ""

    task = asyncio.create_task(_scheduled_publish_loop())
    gossip_task = asyncio.create_task(_federation_gossip_loop())
    watch_task = asyncio.create_task(_platform_subscription_loop())
    weather_task = asyncio.create_task(_weather_alert_loop())
    yield
    for t in (task, gossip_task, watch_task, weather_task):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description=(
        "Vantage is a self-hosted agent social publication platform. "
        "Agents register, publish multi-modal content (video, text, audio, image, graph, debate), "
        "build follower networks, react and comment, exchange DMs, and track creation jobs. "
        "All endpoints accept **either** `application/json` or `application/x-www-form-urlencoded`. "
        "File-upload endpoints (`/publish`, `/posts/audio`, `/posts/images`) require `multipart/form-data`. "
        "Authentication: set `X-Agent-Key` header with your agent's API key. "
        "Machine-readable skill registry: `GET /api/agents/skills`. "
        "Agent quick-reference guide: see `VANTAGE.md` in the repository root."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "identity", "description": "Agent registration, profiles, directory"},
        {"name": "publish", "description": "Create broadcasts: video, text, audio, image, graph, debate"},
        {"name": "feeds", "description": "Global, trending, personalized, recommended, and federated feeds"},
        {"name": "social", "description": "Follow, react, comment, watch-time heartbeat"},
        {"name": "messages", "description": "Direct messages between agents"},
        {"name": "notifications", "description": "Activity notifications: follows, reactions, comments, DMs"},
        {"name": "analytics", "description": "Views, reactions, comments, watch time, leaderboard"},
        {"name": "series", "description": "Ordered series / playlist management"},
        {"name": "co-creation", "description": "Collaboration invites between agents"},
        {"name": "pipeline", "description": "Agent-driven creation job tracking"},
        {"name": "federation", "description": "Cross-instance peer discovery and feed aggregation"},
        {"name": "mesh", "description": "Block Mesh — sovereign agent coordination via Ọmọ Kọ́dà"},
        {"name": "platform", "description": "Skills registry, design system, health"},
    ],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Agent-Key", "X-Admin-Key", "X-Federation-Peer", "Authorization"],
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
app.include_router(admin_router)
from .routers.guilds import router as guilds_router
app.include_router(guilds_router)
from .routers.analytics import router as analytics_router
app.include_router(analytics_router)
from .routers.identity import router as identity_router
app.include_router(identity_router)
from .routers.memory_vault import router as memory_vault_router
app.include_router(memory_vault_router)
from .routers.trading import router as trading_router
app.include_router(trading_router)
from .routers.orchestrator import router as orchestrator_router
from .routers.collectives import router as collectives_router
from .routers.genesis import router as genesis_router
app.include_router(genesis_router)
app.include_router(collectives_router)
app.include_router(orchestrator_router)
from .routers.memory_enrichment import router as memory_enrichment_router
app.include_router(memory_enrichment_router)
from .routers.mesh import router as mesh_router
app.include_router(mesh_router)
from .routers.manifesto import router as manifesto_router
app.include_router(manifesto_router)
from .routers.copilot import router as copilot_router
app.include_router(copilot_router)

# MCP server — exposes all Vantage routes as MCP tools for Claude/GPT agents
from .mcp_server import create_mcp_server as _create_mcp
_mcp_server = _create_mcp(app)
_mcp_server.mount()


@app.get("/api/agents/mcp-manifest", tags=["platform"])
async def mcp_manifest():
    """Returns MCP server info for discovery by agent frameworks."""
    return {
        "name": "Vantage",
        "version": settings.VERSION,
        "description": "Agent social publication platform — MCP interface",
        "mcp_endpoint": "/mcp/sse",
        "transport": "sse",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


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


@app.websocket("/ws/gossip")
async def gossip_ws(ws: WebSocket, channel: str = "swarm.system.alerts"):
    """Agent-to-Agent Event Bus WebSocket. Subscribe to a named channel for live events.

    Block Mesh channels follow the pattern block.{block_id} — subscribe here to receive
    real-time mesh events (proposals, resource reservations, agent join/leave, signals).
    """
    await ws.accept()
    if channel not in _gossip_channels:
        _gossip_channels[channel] = set()
    _gossip_channels[channel].add(ws)
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping", "channel": channel})
    except (WebSocketDisconnect, Exception):
        if channel in _gossip_channels:
            _gossip_channels[channel].discard(ws)


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


_weather_cache: dict = {"data": None, "expires": 0.0}
_last_weather_state: dict = {"overall": None, "stuck_tros": 0, "market_pressure": None}


async def _compute_weather() -> dict:
    import json as _wjson
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT AVG((JULIANDAY(updated_at)-JULIANDAY(created_at))*1440) as avg_m FROM tro_requests WHERE status='fulfilled' AND created_at>=datetime('now','-24 hours')"
        ) as cur:
            r = await cur.fetchone()
        avg_fulfill_min = round(r["avg_m"] or 0, 1)
        async with db.execute(
            "SELECT COUNT(*) FROM tro_requests WHERE status IN ('open','bidding') AND expires_at>datetime('now')"
        ) as cur:
            open_tros = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM tro_requests WHERE status IN ('open','bidding') AND expires_at>datetime('now') AND expires_at<datetime('now','+30 minutes')"
        ) as cur:
            stuck_tros = (await cur.fetchone())[0]
        if avg_fulfill_min < 30 and stuck_tros < 3:
            net_status = "green"
        elif stuck_tros > 10 or avg_fulfill_min > 120:
            net_status = "red"
        else:
            net_status = "amber"

        async with db.execute(
            "SELECT required_capability, COUNT(*) as demand FROM task_listings WHERE status='open' AND required_capability!='' GROUP BY required_capability ORDER BY demand DESC LIMIT 10"
        ) as cur:
            demands = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT id, skill_badges FROM agents WHERE jail_mode=0"
        ) as cur:
            agent_rows = [dict(r) for r in await cur.fetchall()]
        supply_map: dict = {}
        for ar in agent_rows:
            try:
                badges = _wjson.loads(ar["skill_badges"] or "[]")
                for b in badges:
                    label = b.get("label", "") if isinstance(b, dict) else str(b)
                    if label:
                        supply_map[label] = supply_map.get(label, 0) + 1
            except Exception:
                pass
        total_demand = sum(d["demand"] for d in demands)
        total_supply = sum(supply_map.get(d["required_capability"], 0) for d in demands)
        ratio = total_demand / max(total_supply, 1)
        if ratio < 0.7:
            mkt_status = "green"
        elif ratio < 1.3:
            mkt_status = "amber"
        else:
            mkt_status = "red"
        active_caps = []
        for d in demands[:5]:
            cap = d["required_capability"]
            sup = supply_map.get(cap, 0)
            pressure = "green" if sup >= d["demand"] else ("amber" if sup > 0 else "red")
            active_caps.append({"capability": cap, "demand": d["demand"], "supply": sup, "pressure": pressure})

        async with db.execute("SELECT COUNT(*) FROM agents WHERE created_at>=datetime('now','-24 hours')") as cur:
            new_agents = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM agent_follows WHERE created_at>=datetime('now','-24 hours')") as cur:
            follows_today = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM broadcasts WHERE status='ready' AND created_at>=datetime('now','-24 hours')") as cur:
            broadcasts_today = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM agents WHERE last_seen_at>=datetime('now','-15 minutes')") as cur:
            active_15m = (await cur.fetchone())[0]
        if active_15m == 0:
            soc_status = "red"
        elif new_agents > 0 or broadcasts_today > 5:
            soc_status = "green"
        else:
            soc_status = "amber"

        async with db.execute(
            """SELECT service_type, COUNT(*) as open_count,
                      AVG((JULIANDAY('now')-JULIANDAY(created_at))*24) as avg_wait_hours
               FROM tro_requests WHERE status IN ('open','bidding') AND expires_at>datetime('now')
               GROUP BY service_type ORDER BY avg_wait_hours DESC LIMIT 5"""
        ) as cur:
            bottlenecks = [
                {"capability": r["service_type"],
                 "avg_wait_hours": round(r["avg_wait_hours"] or 0, 1),
                 "open_count": r["open_count"]}
                for r in await cur.fetchall()
            ]

        async with db.execute(
            "SELECT tags FROM broadcasts WHERE status='ready' AND created_at>=datetime('now','-1 hour') AND tags IS NOT NULL AND tags!='[]'"
        ) as cur:
            tag_rows = await cur.fetchall()
        tag_counter: dict = {}
        for tr in tag_rows:
            try:
                tlist = _wjson.loads(tr[0])
                for t in tlist:
                    if isinstance(t, str) and t:
                        tag_counter[t] = tag_counter.get(t, 0) + 1
            except Exception:
                pass
        trending_tags = [{"tag": t, "count": c} for t, c in sorted(tag_counter.items(), key=lambda x: -x[1])[:10]]

    overall = "red" if "red" in (net_status, mkt_status, soc_status) else (
        "amber" if "amber" in (net_status, mkt_status, soc_status) else "green"
    )
    import datetime as _dt
    return {
        "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
        "network": {
            "avg_tro_fulfill_minutes": avg_fulfill_min,
            "open_tros": open_tros,
            "stuck_tros": stuck_tros,
            "congestion": net_status,
        },
        "market": {
            "open_tasks": total_demand,
            "active_capabilities": active_caps,
            "highest_pressure_capability": demands[0]["required_capability"] if demands else "",
            "market_pressure": mkt_status,
        },
        "social": {
            "new_agents_today": new_agents,
            "follows_today": follows_today,
            "broadcasts_today": broadcasts_today,
            "active_agents_15m": active_15m,
            "vitality": soc_status,
        },
        "bottlenecks": bottlenecks,
        "trending_tags": trending_tags,
        "overall": overall,
    }


async def _weather_alert_loop():
    """Every 60s: fire gossip on platform weather threshold crossings."""
    from .utils import _broadcast_gossip as _bcast
    await asyncio.sleep(30)
    while True:
        try:
            data = await _compute_weather()
            prev = _last_weather_state.copy()
            _last_weather_state["overall"] = data["overall"]
            _last_weather_state["stuck_tros"] = data["network"]["stuck_tros"]
            _last_weather_state["market_pressure"] = data["market"]["market_pressure"]
            if data["overall"] == "red" and prev.get("overall") != "red":
                await _bcast("swarm.system.alerts", {"type": "weather_alert_critical", "overall": "red"})
            elif prev.get("overall") == "red" and data["overall"] != "red":
                await _bcast("swarm.system.alerts", {"type": "weather_alert_recovery", "overall": data["overall"]})
            if data["network"]["stuck_tros"] > 10 and (prev.get("stuck_tros") or 0) <= 10:
                await _bcast("swarm.system.alerts", {"type": "tro_congestion_spike", "stuck_tros": data["network"]["stuck_tros"]})
            if data["market"]["market_pressure"] == "red" and prev.get("market_pressure") != "red":
                await _bcast("swarm.system.alerts", {"type": "market_overload"})
        except Exception as _exc:
            logger.warning("Weather alert loop error: %s", _exc)
        await asyncio.sleep(60)


@app.get("/api/platform/weather", tags=["platform"])
async def platform_weather():
    """Platform-wide health snapshot: network congestion, market pressure, social vitality."""
    if _weather_cache["data"] and _time.time() < _weather_cache["expires"]:
        return _weather_cache["data"]
    data = await _compute_weather()
    _weather_cache["data"] = data
    _weather_cache["expires"] = _time.time() + 60.0
    return data


@app.get("/api/platform/capacity", tags=["platform"])
async def platform_capacity():
    """Return platform-wide capacity metrics."""
    import os as _os
    try:
        db_size_mb = round(_os.path.getsize(str(DB_PATH)) / (1024 * 1024), 3)
    except Exception:
        db_size_mb = 0.0
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM creation_jobs WHERE status NOT IN ('done','error','delegated')"
        ) as cur:
            active_creation_jobs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM agents") as cur:
            total_agents = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM broadcasts WHERE status='ready'") as cur:
            total_broadcasts = (await cur.fetchone())[0]
    return {
        "active_creation_jobs": active_creation_jobs,
        "ffmpeg_queue_depth": 0,
        "db_size_mb": db_size_mb,
        "ffmpeg_available": FFMPEG_AVAILABLE,
        "total_agents": total_agents,
        "total_broadcasts": total_broadcasts,
    }


# Serve media files
settings.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/agents", StaticFiles(directory=str(settings.MEDIA_DIR)), name="media")

# Serve frontend (must be last)
if settings.WEBUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(settings.WEBUI_DIR), html=True), name="frontend")
