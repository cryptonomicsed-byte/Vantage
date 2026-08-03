"""Buzz <-> Vantage bridge daemon -- Section 0 of the integration
blueprint (~/buzz_vantage_blueprint.md). Everything else in the
blueprint's later phases (P1-P7) hangs off this module: a per-agent
session pool, an idempotency memo so mirrored events never double-post
or loop, and the event-map conventions (kind numbers, tag shapes) that
the rest of the phases reuse.

P0 scope only: outbound text-broadcast mirroring into the shared
MAIN_FEED channel, plus the session pool + heartbeat machinery those
later phases need. Inbound subscription (kind:9/1 -> feed rows),
reactions, threads, and everything else is P2+ -- not built here.
"""
import asyncio
import logging
import time
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex, get_owner_attestation_tag
from .buzz_registration import DEFAULT_CHANNEL_ID, RELAY_WS_URL
from .db import get_db

logger = logging.getLogger(__name__)

SESSION_IDLE_TIMEOUT_SECONDS = 15 * 60
HEARTBEAT_INTERVAL_SECONDS = 60
PRESENCE_KIND = 20001


async def get_main_feed_channel() -> str:
    """buzz_config is a KV table so this can be repointed later without a
    migration; seeded to the existing shared e2e channel (already proven
    live throughout this integration's development) rather than minting a
    new, empty one."""
    async with get_db() as db:
        cur = await db.execute("SELECT value FROM buzz_config WHERE key='MAIN_FEED_CHANNEL'")
        row = await cur.fetchone()
        if row:
            return row[0]
        await db.execute(
            "INSERT OR IGNORE INTO buzz_config (key, value) VALUES ('MAIN_FEED_CHANNEL', ?)",
            (DEFAULT_CHANNEL_ID,),
        )
        await db.commit()
        return DEFAULT_CHANNEL_ID


class _PooledSession:
    def __init__(self, agent_id: int, session: BuzzSession):
        self.agent_id = agent_id
        self.session = session
        self.last_used = time.time()
        self.heartbeat_task: Optional[asyncio.Task] = None


class BuzzBridge:
    """Session pool: dict[agent_id -> _PooledSession]. Lazily connects on
    first event for that agent; an idle-reaper closes sessions unused for
    SESSION_IDLE_TIMEOUT_SECONDS. One bridge instance per process (module-
    level singleton below), matching the "everything rides on this" role
    the blueprint gives it."""

    def __init__(self):
        self._sessions: dict[int, _PooledSession] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None

    def _ensure_reaper(self):
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.ensure_future(self._reap_idle_loop())

    async def _reap_idle_loop(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            async with self._lock:
                stale = [aid for aid, s in self._sessions.items() if now - s.last_used > SESSION_IDLE_TIMEOUT_SECONDS]
                for aid in stale:
                    await self._close_session(aid)

    async def _close_session(self, agent_id: int):
        pooled = self._sessions.pop(agent_id, None)
        if not pooled:
            return
        if pooled.heartbeat_task:
            pooled.heartbeat_task.cancel()
        try:
            await pooled.session.close()
        except Exception:
            pass
        logger.info("buzz_bridge: closed idle session for agent_id=%s", agent_id)

    async def _heartbeat_loop(self, agent_id: int):
        """Presence kind:20001 every 60s (TTL 180s per the blueprint) --
        purely best-effort, a failed heartbeat publish never tears down
        the session itself (the next real publish attempt will surface
        any actual connection problem)."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            pooled = self._sessions.get(agent_id)
            if not pooled:
                return
            try:
                await pooled.session.publish(PRESENCE_KIND, "", tags=[["ttl", "180"]])
            except Exception as e:
                logger.warning("buzz_bridge: heartbeat failed for agent_id=%s: %s", agent_id, e)

    async def get_session(self, agent_id: int) -> BuzzSession:
        async with self._lock:
            self._ensure_reaper()
            pooled = self._sessions.get(agent_id)
            if pooled:
                pooled.last_used = time.time()
                return pooled.session

            pk = await derive_buzz_keypair(agent_id)
            session = BuzzSession(RELAY_WS_URL, pk)
            await session.connect()
            await session.authenticate()
            pooled = _PooledSession(agent_id, session)
            pooled.heartbeat_task = asyncio.ensure_future(self._heartbeat_loop(agent_id))
            self._sessions[agent_id] = pooled
            logger.info("buzz_bridge: opened session for agent_id=%s", agent_id)
            return session

    async def get_buzz_event_id(self, vantage_event_id: str, direction: str = "outbound") -> Optional[str]:
        """Looks up the buzz event id a given vantage object (e.g.
        "broadcast:123") was mirrored as -- used by NIP-10 threading
        (Section 3.3) to find a debate root's buzz event id when mirroring
        a reply."""
        async with get_db() as db:
            cur = await db.execute(
                "SELECT buzz_event_id FROM buzz_event_map WHERE vantage_event_id=? AND direction=?",
                (vantage_event_id, direction),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def _already_mirrored(self, vantage_event_id: str, direction: str) -> bool:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT 1 FROM buzz_event_map WHERE vantage_event_id=? AND direction=?",
                (vantage_event_id, direction),
            )
            return (await cur.fetchone()) is not None

    async def _record_mirror(self, vantage_event_id: str, buzz_event_id: Optional[str], direction: str):
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO buzz_event_map (vantage_event_id, buzz_event_id, direction) VALUES (?,?,?)",
                (vantage_event_id, buzz_event_id, direction),
            )
            await db.commit()

    async def publish_feed(self, agent_id: int, broadcast_id: int, content: str, kind: int = 9, extra_tags: Optional[list] = None) -> Optional[str]:
        """Mirrors a broadcast into the shared MAIN_FEED channel. Defaults
        to kind:9 (text, Section 2); callers doing Section 3 full-fidelity
        mirroring (kind:30023 long-form for graphs, etc) pass `kind` and
        any extra tags (e.g. ["d", ...] for addressable events) explicitly.
        Never raises -- a broken relay/session should degrade the mirror
        silently, not break the broadcast itself (matches _try_omniroute's
        existing fire-and-forget contract elsewhere in this codebase).
        Returns the buzz event id on success, None on any failure or if
        already mirrored."""
        vantage_event_id = f"broadcast:{broadcast_id}"
        if await self._already_mirrored(vantage_event_id, "outbound"):
            return None

        channel = await get_main_feed_channel()
        try:
            pubkey = public_key_xonly_hex(await derive_buzz_keypair(agent_id))
            attestation = await get_owner_attestation_tag(pubkey)
            tags = [["h", channel], ["client", "vantage"], attestation] + (extra_tags or [])
            session = await self.get_session(agent_id)
            result = await session.publish(kind, content, tags=tags)
        except Exception as e:
            logger.warning("buzz_bridge: publish_feed failed for broadcast_id=%s agent_id=%s: %s", broadcast_id, agent_id, e)
            # A dead/expired session shouldn't poison the pool forever --
            # drop it so the next attempt reconnects fresh.
            await self._close_session(agent_id)
            return None

        buzz_event_id = result["event"]["id"]
        await self._record_mirror(vantage_event_id, buzz_event_id, "outbound")
        logger.info("buzz_bridge: mirrored broadcast_id=%s -> buzz event %s", broadcast_id, buzz_event_id)
        return buzz_event_id

    async def publish_video_event(
        self, agent_id: int, broadcast_id: int, title: str, description: str,
        video_url: str, sha256_hex: str, mime_type: str = "video/mp4",
        duration_sec: Optional[int] = None, thumbnail_url: Optional[str] = None,
        external_ids: Optional[list[tuple[str, str]]] = None, short: bool = False,
    ) -> Optional[str]:
        """NIP-71 (Video Events, kind:21 normal / kind:22 short) +
        NIP-73 (External Content IDs) -- a dedicated video-post event
        distinct from publish_feed's kind:9 feed mirror (own dedup key,
        `broadcast:{id}:video71`, since publish_feed's own dedup key is
        the bare `broadcast:{id}` and would otherwise silently no-op this
        as "already mirrored" -- the exact same-key collision bug class
        found repeatedly this session with publish_feed/get_buzz_event_id).
        `external_ids` is a list of (k, i) pairs per NIP-73 (e.g.
        [("web", "https://archive.org/...")]) -- optional, since
        self-published Cinema/Agent.TV content has no canonical external
        ID, only franken-stream's fetched-by-URL content does.
        """
        vantage_event_id = f"broadcast:{broadcast_id}:video71"
        if await self._already_mirrored(vantage_event_id, "outbound"):
            return None

        try:
            pubkey = public_key_xonly_hex(await derive_buzz_keypair(agent_id))
            attestation = await get_owner_attestation_tag(pubkey)
            imeta = ["imeta", f"url {video_url}", f"m {mime_type}", f"x {sha256_hex}"]
            if thumbnail_url:
                imeta.append(f"image {thumbnail_url}")
            tags = [["title", title], imeta, ["client", "vantage"], attestation]
            if duration_sec:
                tags.append(["duration", str(duration_sec)])
            if thumbnail_url:
                tags.append(["thumb", thumbnail_url])
            for k, i in (external_ids or []):
                tags.append(["i", i])
                tags.append(["k", k])
            kind = 22 if short else 21
            session = await self.get_session(agent_id)
            result = await session.publish(kind, description or "", tags=tags)
            ack = result.get("ack") or []
            if not (len(ack) > 2 and ack[2]):
                # BuzzSession.publish doesn't raise on relay rejection --
                # it only sends and returns the client-built event, so a
                # rejection (e.g. "restricted: unknown event kind") looks
                # identical to success unless the ack is checked explicitly.
                reason = ack[3] if len(ack) > 3 else "no ack"
                logger.warning("buzz_bridge: relay rejected video event for broadcast_id=%s: %s", broadcast_id, reason)
                return None
        except Exception as e:
            logger.warning("buzz_bridge: publish_video_event failed for broadcast_id=%s agent_id=%s: %s", broadcast_id, agent_id, e)
            await self._close_session(agent_id)
            return None

        buzz_event_id = result["event"]["id"]
        await self._record_mirror(vantage_event_id, buzz_event_id, "outbound")
        logger.info("buzz_bridge: published NIP-71 video event for broadcast_id=%s -> buzz event %s", broadcast_id, buzz_event_id)
        return buzz_event_id


# Module-level singleton -- "one bridge instance per process" per the
# session-pool design; agents.py's fire-and-forget hook imports this.
bridge = BuzzBridge()
