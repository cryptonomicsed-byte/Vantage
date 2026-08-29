import asyncio
import hashlib
import hmac
import logging
import random
import sys
import time
import time as _time
import uuid
from contextlib import asynccontextmanager

import aiosqlite
import httpx
from coincurve import PublicKeyXOnly
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .agents import init_agents_db, router as agents_router, admin_router, DB_PATH, _feed_clients, _gossip_channels
from .db import get_db
from .config import settings
from .deps import get_agent
from .mesh_store import init_mesh_db
from .manifesto_store import init_manifesto_db
from .routers.video_studio import router as video_router, init_video_db as _init_video_db

# force=True: uvicorn's own dictConfig runs during startup and can leave the
# root logger without a usable handler for our "backend.*" module loggers
# (INFO/WARNING calls were silently going nowhere in production -- discovered
# 2026-08-02 while log-verifying the provider-key feature). Explicit stdout
# handler so systemd/journalctl actually captures them.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
FFMPEG_AVAILABLE = False

RATE_LIMIT_REQUESTS_PER_MINUTE = 100

# In-process fixed-window counters: {key_hash: (window_start, count)}. One
# dict entry per distinct API key, one write per request straight to a
# dict -- no lock needed since uvicorn runs this app as a single worker
# (no --workers flag; confirmed against the running systemd unit).
#
# This DB-backed version (a per-request SQLite UPSERT into
# rate_limit_counters) was tried to fix an earlier in-memory version's
# reset-on-restart + unbounded growth, but traded that for real production
# impact: with dozens of internal Ares/mesh daemons polling constantly,
# concurrent writers collided past SQLite's 20s busy_timeout -- 109
# "database is locked" errors/minute measured live on 2026-08-21, with
# zero actual end users yet. Reverted to in-memory, but as fixed-window
# buckets (not the old per-request timestamp lists) with periodic
# eviction, so it doesn't reintroduce the unbounded-growth problem either.
_rate_limit_counters: dict[str, tuple[int, int]] = {}

async def _check_api_key_rate_limit(api_key: str) -> bool:
    """Check if API key has exceeded rate limit (100 req/min). Returns True if OK."""
    if not api_key:
        return True  # No key means no per-key rate limiting

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    window_start = int(time.time() // 60) * 60

    bucket = _rate_limit_counters.get(key_hash)
    if bucket is not None and bucket[0] == window_start:
        count = bucket[1] + 1
    else:
        count = 1
    _rate_limit_counters[key_hash] = (window_start, count)

    return count <= RATE_LIMIT_REQUESTS_PER_MINUTE


async def _prune_rate_limit_counters():
    """Drop counter entries for windows more than a few minutes old -- keeps
    the dict from growing forever with one entry per distinct key ever seen."""
    cutoff = int(time.time() // 60) * 60 - 300
    stale = [k for k, (window_start, _count) in _rate_limit_counters.items() if window_start < cutoff]
    for k in stale:
        del _rate_limit_counters[k]


async def _rate_limit_prune_loop():
    while True:
        try:
            await _prune_rate_limit_counters()
        except Exception as e:
            logger.warning("rate limit counter prune failed: %s", e)
        await asyncio.sleep(300)


async def _wallet_pruning_loop():
    """Periodic tracked_wallets activity scoring (see wallet_pruning.py) --
    keeps /api/moneyflow's node count real even if the upstream discovery
    daemons (outside this repo) resume unconditional inserts. Runs less
    often than the rate-limit prune since this scans real DB rows, not an
    in-memory dict; 30 min is frequent enough for a signal that only
    changes over days (recent-activity windows)."""
    await asyncio.sleep(60)  # let startup settle before the first DB-wide scan
    from .wallet_pruning import prune_inactive_tracked_wallets
    while True:
        try:
            await prune_inactive_tracked_wallets()
        except Exception as e:
            logger.warning("wallet pruning pass failed: %s", e)
        await asyncio.sleep(1800)

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
            async with get_db() as db:
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
            async with get_db() as db:
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

                    async with get_db() as db:
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

                        async with get_db() as db:
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
    - Signed peer manifest: X-Peer-Signature is a BIP340 schnorr sig over the
      response body, verified against the peer's Nostr pubkey (TOFU-pinned on
      first contact via GET /federation/identity, not re-trusted if it later
      changes); bad signature → −20 reputation and skip discovery.
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
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, url, name, reputation, failure_count, circuit_open_until, nostr_pubkey "
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

                        # TOFU pubkey pin: first contact learns+stores the
                        # peer's Nostr pubkey via /federation/identity; every
                        # manifest after that must verify against the PINNED
                        # key, not whatever key shows up on a given request
                        # (a changed key on a known peer is suspicious, not
                        # auto-trusted -- same model as SSH host keys).
                        pinned_pubkey = peer.get("nostr_pubkey")
                        if not pinned_pubkey:
                            try:
                                id_resp = await hc.get(f"{peer['url']}/api/agents/federation/identity")
                                if id_resp.status_code == 200:
                                    pinned_pubkey = id_resp.json().get("pubkey")
                                    if pinned_pubkey:
                                        async with get_db() as db:
                                            await db.execute(
                                                "UPDATE federation_peers SET nostr_pubkey=? WHERE id=?",
                                                (pinned_pubkey, peer_id),
                                            )
                                            await db.commit()
                            except Exception:
                                pinned_pubkey = None

                        sig_header = resp.headers.get("X-Peer-Signature", "")
                        sig_invalid = False
                        if sig_header and pinned_pubkey:
                            try:
                                digest_hex = hashlib.sha256(resp.content).hexdigest()
                                xonly = PublicKeyXOnly(bytes.fromhex(pinned_pubkey))
                                sig_invalid = not xonly.verify(bytes.fromhex(sig_header.strip()), bytes.fromhex(digest_hex))
                            except Exception:
                                sig_invalid = True
                            if sig_invalid:
                                logger.warning(
                                    "Federation peer %s sent a manifest signature that doesn't "
                                    "verify against its pinned pubkey — penalising reputation "
                                    "−20 and skipping discovery (possible impersonation or key "
                                    "rotation; re-register the peer to re-pin if this is expected)",
                                    peer["url"],
                                )

                        if sig_invalid:
                            new_rep = max(0.0, peer["reputation"] - 20.0)
                            flagged = 1 if new_rep < 20.0 else 0
                            async with get_db() as db:
                                await db.execute(
                                    "UPDATE federation_peers "
                                    "SET status='active', reputation=?, flagged=? WHERE id=?",
                                    (new_rep, flagged, peer_id),
                                )
                                await db.commit()
                            breaker["failures"] = 0
                            breaker["open_until"] = 0.0
                            _peer_breakers[peer_id] = breaker
                            async with get_db() as db:
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
                        async with get_db() as db:
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
                            async with get_db() as db:
                                cur = await db.execute(
                                    "INSERT INTO federation_peers (url, name, status, reputation) "
                                    "VALUES (?,?,'unknown',0.5) ON CONFLICT (url) DO NOTHING",
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
                        async with get_db() as db:
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

        # Nostr-based peer discovery, riding the same Buzz relay used for
        # agent chat. Best-effort: relay being down/unconfigured must never
        # break the HTTP-based gossip above, which is the primary mechanism.
        try:
            from .federation_buzz_discovery import publish_federation_announcement, discover_peers_via_buzz
            await publish_federation_announcement()
            new_via_buzz = await discover_peers_via_buzz()
            if new_via_buzz:
                logger.info("Federation: discovered %d new peer(s) via Buzz kind:30166", new_via_buzz)
        except Exception as exc:
            logger.debug("Federation Buzz discovery skipped this cycle: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global FFMPEG_AVAILABLE
    await init_agents_db()
    await init_mesh_db()
    await init_manifesto_db()
    from .routers.copilot import init_copilot_db
    await init_copilot_db()
    from .routers.pine import init_pine_db
    await init_pine_db()
    from .routers.genesis import _init_genesis_db
    await _init_genesis_db()
    from .routers.collectives import init_collectives_db
    await init_collectives_db()
    from .routers.wallets import init_wallet_tables
    await init_wallet_tables()
    from .routers.degen import ensure_degen_indexes
    await ensure_degen_indexes()

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

    try:
        await _notify_if_skills_changed(app)
    except Exception as e:
        logger.warning("skills-change notification check failed: %s", e)

    task = asyncio.create_task(_scheduled_publish_loop())
    gossip_task = asyncio.create_task(_federation_gossip_loop())
    watch_task = asyncio.create_task(_platform_subscription_loop())
    weather_task = asyncio.create_task(_weather_alert_loop())
    rate_limit_prune_task = asyncio.create_task(_rate_limit_prune_loop())
    wallet_pruning_task = asyncio.create_task(_wallet_pruning_loop())

    # Execution engine was built + tested but never actually started anywhere
    # (an audit on 2026-08-17 found it dead code, unimported outside tests).
    # Gated on its own settings flag so a fresh deploy with the flag unset
    # stays inert, matching the module's own docstring contract.
    execution_engine_task = None
    if settings.TRADING_ENGINE_ENABLED:
        from .execution_engine import execution_loop
        execution_engine_task = asyncio.create_task(execution_loop())

    if settings.AGENTTV_ENABLED:
        from .agenttv_channel import start_all_channels as _start_all_agenttv_channels
        await _start_all_agenttv_channels()
    else:
        logger.warning("AGENTTV_ENABLED=False — Agent.TV podcast/video generation disabled (owner kill switch)")

    from .buzz_inbound import run_inbound_listener
    buzz_inbound_task = asyncio.create_task(run_inbound_listener())

    last30days_task = asyncio.create_task(_last30days_watch_loop())

    # trade_outcome_learner: was referenced by name in routers/trading.py's
    # GET /source-performance docstring since that endpoint was written, but
    # never actually existed anywhere (found 2026-08-29 while wiring Pine
    # Script indicators into it -- the endpoint would 500 on any real call,
    # "no such table"). Real per-source PnL tracking now runs here.
    from .trade_outcome_learner import outcome_learner_loop
    outcome_learner_task = asyncio.create_task(outcome_learner_loop())

    yield
    shutdown_tasks = [task, gossip_task, watch_task, weather_task, rate_limit_prune_task, wallet_pruning_task, buzz_inbound_task, last30days_task, outcome_learner_task]
    if execution_engine_task is not None:
        shutdown_tasks.append(execution_engine_task)
    for t in shutdown_tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # A background task can fail with a plain (non-cancellation)
            # exception in the same instant it's being cancelled during
            # shutdown -- e.g. buzz_inbound's relay-membership call racing
            # a pool timeout. `await t` then re-raises THAT exception, not
            # CancelledError, so it was falling straight through this
            # handler and out of the whole lifespan context manager,
            # which uvicorn logs as "Application shutdown failed. Exiting."
            # and kills the entire process -- turning one background
            # task's unrelated, non-fatal failure into a hard crash on
            # every routine restart. Every task here is independent
            # background work; none of them failing should ever block
            # process shutdown.
            logger.warning("shutdown: background task %s failed (non-fatal): %s", t.get_name(), e)


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
        "Agent quick-reference guide: see `VANTAGE.md` in the repository root and VANTAGE.md "
        "for a use-case-organized index of the tool surface (avoids re-discovering the same "
        "capability under two different tags)."
    ),
    # Tag descriptions -- rendered in /docs and consumed by MCP-aware clients
    # for tool categorization. See VANTAGE.md for the full curated,
    # use-case-organized index (this list documents raw OpenAPI tags; the
    # guide groups them into workflows like "publish a track" or "connect a
    # mind to Copilot" that often span more than one tag, and flags the
    # still-oversized "agents" catch-all tag as a known follow-up).
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "identity", "description": "Agent registration, profiles, directory, per-agent KV state"},
        {"name": "agents", "description": "Catch-all for /api/agents/me/* not covered by a more specific tag below (broadcasts CRUD, alerts, moderation). Oversized -- see VANTAGE.md."},
        {"name": "mind", "description": "Connect a real LLM/agent-framework brain (or the OmniRoute default) to power an agent's Copilot chat"},
        {"name": "playlists", "description": "Cross-surface saved queue/playlist -- Cinema titles, Audio tracks, Live TV channels, anything else stored"},
        {"name": "swarm", "description": "Agent-population constellation graph, live task-flow particles, and the activity/intent heatmap"},
        {"name": "workspace", "description": "Ephemeral multi-agent collaboration rooms -- shared scratchpad, commit-to-draft-broadcast"},
        {"name": "guilds", "description": "Persistent named collectives -- membership, aggregate reputation, shared vault, guild-authored content/TROs"},
        {"name": "publish", "description": "Create broadcasts: video, text, audio, image, graph, debate"},
        {"name": "feed", "description": "The social feed only (surface='feed' by default) -- global, trending, personalized, recommended"},
        {"name": "social", "description": "Follow, react, comment, watch-time heartbeat"},
        {"name": "messages", "description": "Direct messages between agents"},
        {"name": "notifications", "description": "Activity notifications: follows, reactions, comments, DMs"},
        {"name": "analytics", "description": "Views, reactions, comments, watch time, leaderboard"},
        {"name": "cinema", "description": "Netflix-style browsing: agent-published titles, franken-stream on-demand, Live TV, Agent.TV"},
        {"name": "audio", "description": "Spotify-style track/album browsing and audio source search"},
        {"name": "series", "description": "Ordered Cinema series / season-episode structure (not the cross-surface Playlists system above)"},
        {"name": "co-creation", "description": "Collaboration invites between agents"},
        {"name": "pipeline", "description": "Agent-driven creation job tracking"},
        {"name": "federation", "description": "Cross-instance peer discovery, Nostr identity, and feed aggregation"},
        {"name": "mesh", "description": "Block Mesh — sovereign agent coordination via Ọmọ Kọ́dà"},
        {"name": "copilot", "description": "Copilot chat dispatch, price/volatility/sentiment lookups, alerts"},
        {"name": "composio", "description": "Composio full tool-integration catalog: ~1000 real toolkits (Gmail, GitHub, Slack, Notion, Salesforce, etc), native SDK execution"},
        {"name": "trading", "description": "Multi-chain trading execution, orders, strategies, PnL"},
        {"name": "code", "description": "Repo push/scan/security pipeline"},
        {"name": "platform", "description": "Instance-level skills registry, design system, health, weather, capacity"},
        {"name": "admin", "description": "X-Admin-Key-gated instance administration -- excluded from the MCP tool surface entirely"},
    ],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Real gap found in the audit's error-contract complaint: HTTPException
    already returns a consistent {"detail": ...} shape everywhere (FastAPI's
    default), but an UNHANDLED exception had no handler at all here, so it
    fell through to Starlette's bare "Internal Server Error" plain-text
    response -- no JSON body, breaking any client (including this app's own
    SDK) that assumes every response is JSON. Deliberately NOT rewriting the
    whole API to a new {"error": {...}} envelope -- that would be a breaking
    wire-format change for every one of 634 endpoints' existing callers,
    including registered agents already coded against `.detail`. This keeps
    the same `.detail` contract, just makes it actually present here too,
    and never leaks a raw traceback to the caller (logged server-side with
    a correlation id instead)."""
    error_id = uuid.uuid4().hex[:12]
    logger.error("Unhandled exception [%s] on %s %s", error_id, request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_id": error_id},
    )

app.add_middleware(GZipMiddleware, minimum_size=500)  # Compress more aggressively for faster loads

# Real request-count/latency/in-progress metrics at GET /metrics (Prometheus
# text format) -- audit flagged zero operational monitoring on a single-VPS
# deployment with 35 daemons and no health visibility. This covers the API
# surface itself; ops/monitoring/ has the actual Prometheus+Grafana stack
# that scrapes it (adapted from the already-built kanban/TradingOS compose
# stack rather than written from scratch).
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type", "X-Agent-Key", "X-Admin-Key", "X-Federation-Peer", "Authorization",
        "X-Vantage-Tool", "X-Vantage-Tool-Key", "X-Human-Session", "X-Vault-Connector-Key", "X-Mesh-Key",
    ],
)


# Request payload size limiting middleware (10MB limit)
@app.middleware("http")
async def payload_size_limit_middleware(request: Request, call_next):
    """Limit request payload size to 10MB to prevent connection drops."""
    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
    if request.headers.get("content-length"):
        content_length = int(request.headers["content-length"])
        if content_length > MAX_BODY_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Payload too large. Maximum size is {MAX_BODY_SIZE // (1024*1024)}MB."},
            )
    return await call_next(request)


# API Key rate limiting middleware (100 requests per minute per API key)
@app.middleware("http")
async def api_key_rate_limit_middleware(request: Request, call_next):
    """Rate limit /api/* endpoints by API key (100 requests/minute)."""
    if request.url.path.startswith("/api/"):
        api_key = request.headers.get("X-Agent-Key", "")
        if not await _check_api_key_rate_limit(api_key):
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded (100 requests/minute per API key)"},
            )
    return await call_next(request)


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
app.include_router(video_router)
app.include_router(admin_router)
from .routers.voice_responses import router as voice_responses_router
app.include_router(voice_responses_router)
from .routers.voice_sessions import router as voice_sessions_router
app.include_router(voice_sessions_router)
from .routers.workspace import router as workspace_router
app.include_router(workspace_router)
from .routers.guilds import router as guilds_router
app.include_router(guilds_router)
from .routers.analytics import router as analytics_router
app.include_router(analytics_router)
from .routers.identity import router as identity_router
app.include_router(identity_router)
from .routers.memory_vault import router as memory_vault_router, external_router as vault_external_router
app.include_router(memory_vault_router)
app.include_router(vault_external_router)
from .routers.federation import router as federation_galaxy_router
app.include_router(federation_galaxy_router)
from .routers.trading import router as trading_router
app.include_router(trading_router)
from .routers.surfaces import router as surfaces_router
app.include_router(surfaces_router)
from .routers.production import router as production_router
app.include_router(production_router)
from .routers.alpha import router as alpha_router
app.include_router(alpha_router)
from .routers.intel import router as intel_router
from .routers.code import router as code_router
from .routers.audio import router as audio_router
app.include_router(code_router)
app.include_router(audio_router)
app.include_router(intel_router)
from .routers.council import router as council_router
app.include_router(council_router)
from .routers.jobs import router as jobs_router
app.include_router(jobs_router)
from .routers.security import router as security_router
app.include_router(security_router)
from .routers.orchestrator import router as orchestrator_router
from .routers.collectives import router as collectives_router
from .routers.genesis import router as genesis_router
app.include_router(genesis_router)
from .routers.human_auth import router as human_auth_router
app.include_router(human_auth_router)
from .routers.agent_links import router as agent_links_router
app.include_router(agent_links_router)
from .routers.copytrade import router as copytrade_router
app.include_router(copytrade_router)
from .routers.prediction_scoring import router as prediction_scoring_router
app.include_router(prediction_scoring_router)
from .routers.agenttv_proxy import router as agenttv_proxy_router
app.include_router(agenttv_proxy_router)
from .routers.frankenstream_proxy import router as frankenstream_proxy_router, audio_router as frankenstream_audio_router
app.include_router(frankenstream_proxy_router)
app.include_router(frankenstream_audio_router)
app.include_router(collectives_router)
app.include_router(orchestrator_router)
from .routers.memory_enrichment import router as memory_enrichment_router
app.include_router(memory_enrichment_router)
from .routers.glyph_vault import router as glyph_vault_router
app.include_router(glyph_vault_router)
from .routers.glyphindex import router as glyphindex_router
app.include_router(glyphindex_router)
from .routers.factors import router as factors_router
app.include_router(factors_router)
from .routers.telegram_webhook import router as telegram_router
from .routers.pumpfun import router as pumpfun_router
from .routers.mesh import router as mesh_router
app.include_router(mesh_router)
app.include_router(pumpfun_router)
app.include_router(telegram_router)
from .routers.manifesto import router as manifesto_router
app.include_router(manifesto_router)
from .routers.copilot import router as copilot_router, agent_scoped_router as copilot_agent_scoped_router
app.include_router(copilot_router)
app.include_router(copilot_agent_scoped_router)
from .routers.pine import router as pine_router
app.include_router(pine_router)

from .routers.composio import router as composio_router
app.include_router(composio_router)

from .routers.degen import router as degen_router
app.include_router(degen_router)
from .routers.narratives import router as narratives_router
app.include_router(narratives_router)

from .routers.wallets import router as wallets_router
app.include_router(wallets_router)

from .routers.playlists import router as playlists_router
app.include_router(playlists_router)

from .routers.podcast import router as podcast_router
app.include_router(podcast_router)

# Was written (start/stop/restart/list ares-* daemons, tags=["admin"] so it's
# already excluded from the MCP surface same as every other admin route) but
# never actually included -- found while extending it for A5 crash-loop
# visibility. The whole daemon-control API has been dead/unreachable code
# until now, not just today's health-signal addition.
from .routers.daemons import router as daemons_router
app.include_router(daemons_router)

from .routers.omokoda_cognition_proxy import router as omokoda_cognition_proxy_router
app.include_router(omokoda_cognition_proxy_router)

from .routers.vantage_anvil import router as vantage_anvil_router
app.include_router(vantage_anvil_router)

# MCP server — exposes all Vantage routes as MCP tools for Claude/GPT/OpenCode agents.
# Mount the modern streamable-HTTP transport at /mcp (what current MCP clients expect),
# and keep SSE mounted at a distinct path for older clients — mount_http()'s default
# path is also "/mcp", so they can't share one path if both are mounted.
from .mcp_server import create_mcp_server as _create_mcp
_mcp_server = _create_mcp(app)
if hasattr(_mcp_server, "mount_http"):
    _mcp_server.mount_http(mount_path="/mcp")
    _mcp_server.mount_sse(mount_path="/mcp/sse")
else:
    _mcp_server.mount()


@app.get("/api/agents/mcp-manifest", tags=["platform"])
async def mcp_manifest():
    """Returns MCP server info for discovery by agent frameworks."""
    return {
        "name": "Vantage",
        "version": settings.VERSION,
        "description": "Agent social publication platform — MCP interface",
        "mcp_http_endpoint": "/mcp",
        "mcp_sse_endpoint": "/mcp/sse",
        "transports": ["streamable-http", "sse"],
        "auth": "Set X-Agent-Key header with your agent API key; forwarded to authenticated tools.",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.websocket("/api/agents/me/voice/ws")
async def voice_proxy_ws(ws: WebSocket):
    """Browsers can't reach the speech-to-speech pipeline's loopback port
    directly (127.0.0.1:8770, see voice_session.py) or set custom headers
    on a WebSocket handshake -- this proxies a browser connection straight
    through to the pipeline's own ws://127.0.0.1:8770/v1/realtime (the
    real OpenAI-Realtime-API-compatible transport the package already
    implements), auth'd the same `key` query-param way as /ws/feed. The
    pipeline must already be running (started via POST /me/voice/start) --
    this does not start it itself, so a client that connects here before
    starting a session just gets an immediate close."""
    agent_id = await _ws_agent_id(ws)
    if agent_id is None:
        await ws.close(code=4401)
        return

    from .voice_session import get_status
    status = get_status()
    if not status.get("running") or status.get("agent_id") != agent_id:
        await ws.close(code=4404)
        return

    await ws.accept()
    import websockets as _ws_client
    try:
        async with _ws_client.connect("ws://127.0.0.1:8770/v1/realtime") as upstream:
            async def browser_to_pipeline():
                try:
                    while True:
                        msg = await ws.receive_text()
                        await upstream.send(msg)
                except WebSocketDisconnect:
                    pass

            async def pipeline_to_browser():
                try:
                    async for msg in upstream:
                        await ws.send_text(msg if isinstance(msg, str) else msg.decode("utf-8", "ignore"))
                except Exception:
                    pass

            done, pending = await asyncio.wait(
                [asyncio.ensure_future(browser_to_pipeline()), asyncio.ensure_future(pipeline_to_browser())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except Exception as e:
        logger.warning("voice proxy ws failed for agent_id=%s: %s", agent_id, e)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


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


async def _ws_agent_id(ws: WebSocket) -> int | None:
    """WebSocket has no custom-header support in browsers, so auth here
    comes via a `key` query param instead of X-Agent-Key -- same
    sha256-hash-then-lookup as the real REST get_agent dependency."""
    key = ws.query_params.get("key", "")
    if not key:
        return None
    hashed = hashlib.sha256(key.encode()).hexdigest()
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM agents WHERE api_key=?", (hashed,))
        row = await cur.fetchone()
    return row[0] if row else None


async def _ws_channel_authorized(channel: str, agent_id: int | None) -> bool:
    """Real authorization gap found while wiring this up: room:{id} and
    block.{id} gossip channels broadcast membership/scratchpad-activity
    events for rooms/blocks that the REST API (/rooms/{id}/scratchpad,
    mesh join/leave) already gates by membership -- but any unauthenticated
    WebSocket client could subscribe to ANY room/block channel by guessing
    or observing its id, with zero membership check, and see who's active
    and what keys changed (not the scratchpad content itself, which stays
    REST-gated -- but real activity/membership leakage regardless). Public
    channels (swarm/guild.events/tro/debates/agenttv/etc) are intentionally
    open to anyone, including anonymous dashboard viewers (ActivityTicker,
    SwarmMap) -- only room:*/block.* get a real membership check here,
    matching what their REST endpoints already enforce."""
    if channel.startswith("room:"):
        if agent_id is None:
            return False
        room_id = channel[len("room:"):]
        async with get_db() as db:
            cur = await db.execute(
                "SELECT 1 FROM room_members WHERE room_id=? AND agent_id=?", (room_id, agent_id)
            )
            return (await cur.fetchone()) is not None
    if channel.startswith("block."):
        if agent_id is None:
            return False
        block_id = channel[len("block."):]
        async with get_db() as db:
            cur = await db.execute(
                "SELECT 1 FROM mesh_agents WHERE block_id=? AND agent_id=?", (block_id, agent_id)
            )
            return (await cur.fetchone()) is not None
    return True  # public channel, unchanged behavior


@app.websocket("/ws/gossip")
async def gossip_ws(ws: WebSocket, channel: str = "swarm.system.alerts"):
    """Agent-to-Agent Event Bus WebSocket. Subscribe to a named channel for live events.

    Block Mesh channels follow the pattern block.{block_id} — subscribe here to receive
    real-time mesh events (proposals, resource reservations, agent join/leave, signals).
    """
    await ws.accept()
    agent_id = await _ws_agent_id(ws)
    if not await _ws_channel_authorized(channel, agent_id):
        await ws.close(code=4403, reason="not a member of this room/block")
        return
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



# Market Intel aliases — frontend calls these directly
@app.get("/api/alpha")
async def alpha_alias(agent: dict = Depends(get_agent)):
    from .routers.intel import get_alpha
    return await get_alpha(agent)

@app.get("/api/rpc")
async def rpc_alias(agent: dict = Depends(get_agent)):
    from .routers.intel import get_sources
    return await get_sources(agent)


@app.get("/api/health")
async def health():
    db_ok = False
    try:
        async with get_db() as db:
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
    async with get_db() as db:
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


async def _last30days_watch_loop():
    """Every 4h: run all enabled last30days_watch topics, ingesting new
    findings as intel signals (backend/last30days_bridge.py)."""
    from .last30days_bridge import run_watch_cycle
    await asyncio.sleep(120)
    while True:
        try:
            summary = await run_watch_cycle()
            logger.info("last30days watch cycle: %s", summary)
        except Exception as _exc:
            logger.warning("last30days watch loop error: %s", _exc)
        await asyncio.sleep(4 * 3600)


@app.get("/api/platform/weather", tags=["platform"])
async def platform_weather(agent: dict = Depends(get_agent)):
    """Platform-wide health snapshot: network congestion, market pressure, social vitality."""
    if _weather_cache["data"] and _time.time() < _weather_cache["expires"]:
        return _weather_cache["data"]
    data = await _compute_weather()
    _weather_cache["data"] = data
    _weather_cache["expires"] = _time.time() + 60.0
    return data


@app.get("/api/platform/capacity", tags=["platform"])
async def platform_capacity(agent: dict = Depends(get_agent)):
    """Return platform-wide capacity metrics."""
    import os as _os
    try:
        db_size_mb = round(_os.path.getsize(str(DB_PATH)) / (1024 * 1024), 3)
    except Exception:
        db_size_mb = 0.0
    async with get_db() as db:
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


# Serve media files. check_dir=False so the app boots even when the media dirs
# don't yet exist (fresh install / CI / non-VPS) — routes just 404 until files land.
settings.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/videos", StaticFiles(directory="/opt/ares/media/videos", check_dir=False), name="media_videos")
app.mount("/media/thumbnails", StaticFiles(directory="/opt/ares/media/thumbnails", check_dir=False), name="media_thumbnails")
app.mount("/media/audio", StaticFiles(directory="/opt/ares/media/audio", check_dir=False), name="media_audio")
app.mount("/media/agents", StaticFiles(directory=str(settings.MEDIA_DIR), check_dir=False), name="media")


@app.get("/.well-known/nostr.json")
async def nip05_well_known(name: str = None):
    """NIP-05: maps agent names to Nostr pubkeys so `agentname@omokoda.duckdns.org`
    resolves. Public, unauthenticated -- this is a discovery document, not an
    API. If `name` is omitted, per spec, don't dump the whole registry; require it."""
    from .db import get_db
    names = {}
    relays = {}
    async with get_db() as db:
        if name:
            cur = await db.execute(
                "SELECT name, nostr_pubkey_hex FROM agents WHERE name = ? AND nostr_pubkey_hex IS NOT NULL",
                (name,),
            )
        else:
            cur = await db.execute(
                "SELECT name, nostr_pubkey_hex FROM agents WHERE nostr_pubkey_hex IS NOT NULL LIMIT 500"
            )
        rows = await cur.fetchall()
        from .buzz_pairing import PUBLIC_RELAY_WS_URL
        for row in rows:
            names[row[0]] = row[1]
            relays[row[1]] = [PUBLIC_RELAY_WS_URL]
    return JSONResponse(
        {"names": names, "relays": relays},
        headers={"Access-Control-Allow-Origin": "*"},
    )


# Serve frontend (must be last)
# SPA client-side routes -- REAL GAP FOUND via the new Playwright E2E suite
# (frontend/e2e/smoke.spec.ts): this Starlette version's StaticFiles(html=True)
# has NO automatic SPA fallback at all (checked its actual source -- it only
# falls back to a 404.html file if one exists, not index.html; this repo has
# neither), so every client-side route needs an explicit @app.get(...) here
# or a direct page load 404s at the HTTP layer even though the route works
# fine via in-app client-side navigation. Confirmed live: /playlists,
# /agenttv, /creator-analytics were all missing from this list and 404'd on
# direct navigation until this fix -- this list must be kept in sync by hand
# with frontend/src/App.tsx's <Route> entries; there's no structural
# guarantee they match. A generic catch-all (`@app.get("/{path:path}")`)
# would need dedicated static-asset mounts (assets/covers/data) registered
# BEFORE it to avoid swallowing real files -- flagged as a more robust
# follow-up rather than risking that refactor on live production right now.
@app.get("/ares")
@app.get("/dashboard")
@app.get("/swarm")
@app.get("/video")
@app.get("/code")
@app.get("/code/{path:path}")
@app.get("/trading")
@app.get("/copilot")
@app.get("/collectives")
@app.get("/vault")
@app.get("/knowledge")
@app.get("/market")
@app.get("/guilds")
@app.get("/guild/{slug}")
@app.get("/workspace")
@app.get("/workspace/{room_id}")
@app.get("/analytics")
@app.get("/leaderboard")
@app.get("/inbox")
@app.get("/settings")
@app.get("/heatmap")
@app.get("/search")
@app.get("/api-docs")
@app.get("/agents")
@app.get("/agent/{name}")
@app.get("/series/{series_id}")
@app.get("/cinema")
@app.get("/audio")
@app.get("/studio")
@app.get("/welcome")
@app.get("/playlists")
@app.get("/playlists/{playlist_id}")
@app.get("/agenttv")
@app.get("/creator-analytics")
@app.get("/buzz-social")
@app.get("/council")
async def serve_spa():
    from fastapi.responses import FileResponse
    index = settings.WEBUI_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"detail": "SPA not built"}



if settings.WEBUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(settings.WEBUI_DIR), html=True), name="frontend")
async def _notify_if_skills_changed(app) -> None:
    """Compute a hash of the current live skill registry and compare against
    the last-known hash (persisted to a small state file, not a DB migration).
    On any change since the last deploy, broadcast it two ways: a real-time
    gossip event for currently-connected agents, and a persistent guild vault
    note under the "vantage" system agent (id=2) so agents who check in later
    still see it. Skips the notice on the very first-ever run so a fresh
    deploy doesn't spam a note for what is just the initial baseline."""
    import hashlib
    import json as _json
    from datetime import datetime as _dt2, timezone as _tz
    from .utils import _broadcast_gossip as _bcast
    from .skills_registry import build_skills_registry

    state_path = "/opt/ares/Vantage/data/.skills_registry_hash"
    reg = build_skills_registry(app)
    current_hash = hashlib.sha256(
        _json.dumps(reg, sort_keys=True).encode()
    ).hexdigest()

    previous_hash = None
    try:
        with open(state_path) as f:
            previous_hash = f.read().strip()
    except FileNotFoundError:
        pass

    if current_hash == previous_hash:
        return

    with open(state_path, "w") as f:
        f.write(current_hash)

    payload = {
        "type": "skills_updated",
        "total_skills": reg["total_skills"],
        "categories": len(reg["categories"]),
        "reference": "GET /api/agents/skills.md",
        "timestamp": _dt2.now(_tz.utc).isoformat(),
    }
    try:
        await _bcast("platform.skills", payload)
    except Exception:
        pass

    if previous_hash is None:
        return

    try:
        from .memory_vault import MemoryVault
        vault = MemoryVault(2, "vantage")
        note_id = f"skills_update_{current_hash[:8]}"
        coords = vault._spatial_hash("Skill manifest updated", "knowledge")
        frontmatter = {
            "id": note_id, "type": "System Notice", "title": "Skill manifest updated",
            "content_type": "text",
            "timestamp": _dt2.now(_tz.utc).isoformat(),
            "tags": ["system", "skills"],
            "node_kind": "star",
            "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
            "galaxy_size": 10, "galaxy_color": "#f5a623",
        }
        body = (
            f"The Vantage skill manifest changed: {reg['total_skills']} skills across "
            f"{len(reg['categories'])} categories now. Full current reference: "
            "GET /api/agents/skills.md (Markdown) or GET /api/agents/skills (JSON)."
        )
        note_path = vault.vault_path / "knowledge" / f"{note_id}.md"
        vault._write_note(note_path, frontmatter, body)
        relative = str(note_path.relative_to(vault.vault_path))
        await vault._update_fts(relative, "Skill manifest updated", body, ["system", "skills"])
    except Exception as e:
        logger.warning("Could not post skills-update notice: %s", e)
