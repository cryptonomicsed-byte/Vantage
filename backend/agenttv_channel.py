"""Agent.TV -- the always-on 24/7 channel. Real two-host podcast episodes
(same engine as Collab's "Create Podcast"), not a separate generic
AI-news-reader loop -- this replaces the old Seemplify/Piper single-voice
ChannelLoop entirely, removing the redundancy between "Create Podcast" and
"Agent.TV" the owner flagged.

Cycle: play an episode for its real full duration -> play the fixed 30s
house jingle (looping it if the next episode isn't ready yet -- never cuts
to a not-ready segment) while the NEXT episode renders in the background ->
repeat forever. User-submitted podcasts (published via Collab's "Create
Podcast", kind=video) get folded into the rotation ahead of freshly
auto-generated ones, oldest first, each airing exactly once -- the same
fixed jingle plays for those too, per the owner's call ("even if any user
creates a podcast it uses our custom commercial").

Runs as a single asyncio background task started from main.py's lifespan.
No cross-service proxy -- episodes are generated straight into Vantage's
own /media/videos (already mounted, gets Range/seek support for free via
StaticFiles, unlike the old httpx-proxy approach that broke seeking).
"""
import asyncio
import hashlib
import inspect
import logging
import random
import secrets
import time

import aiosqlite

from .db import get_db
from .podcast_engine import generate_podcast, ensure_jingle, JINGLE_STREAM_URL, JINGLE_DURATION_SEC, VIDEO_OUT_DIR
from .podcast_scanners import ai_news_topic, crypto_tier_topic, crypto_degen_topic
from .routers.surfaces import _insert_broadcast

logger = logging.getLogger(__name__)

AGENTTV_AGENT_NAME = "Agent.TV"


async def _ensure_agent(name: str, bio: str) -> dict:
    """A real system agent identity for a channel's auto-generated episodes
    -- so they're real broadcasts with real reactions (thumbs-up/down
    reuses the existing reaction system, no separate voting mechanism
    needed), browsable in Cinema like anyone else's content, not just
    ephemeral channel state. Shared by every persona channel, not just the
    flagship Agent.TV."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM agents WHERE name=?", (name,))).fetchone()
        if row:
            return dict(row)
        raw_key = "vantage_" + secrets.token_hex(24)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        cur = await db.execute(
            "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
            (name, key_hash, bio),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM agents WHERE id=?", (cur.lastrowid,))).fetchone()
        return dict(row)


NUM_TURNS_PER_EPISODE = 22  # roughly a 3-4min episode at this engine's pace
NUM_TURNS_PER_SEGMENT = 12  # ~3min segment for the scanner-driven persona channels

RETENTION_KEEP_PER_CHANNEL = 20  # aired episodes kept on disk per persona channel; older ones get pruned so /opt/ares/media/videos doesn't grow forever (VPS disk hit 85% full)


async def _prune_aired_episodes(agent_id: int, keep: int = RETENTION_KEEP_PER_CHANNEL) -> None:
    """Delete the media file for auto-generated episodes of one persona channel
    once more than `keep` of them have already aired, oldest first, and mark
    those broadcast rows status='archived' (same gate every browse query
    already uses, so they just quietly drop out of Cinema/guide listings).
    Only ever touches broadcasts owned by a persona agent's own generation
    loop -- never user-submitted podcasts (those belong to the submitter's
    agent, not agent_id here) and never episodes that haven't aired yet."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, stream_url FROM broadcasts
               WHERE agent_id=? AND surface='cinema' AND cinema_kind='podcast'
                 AND status='ready' AND agenttv_aired_at IS NOT NULL
               ORDER BY agenttv_aired_at DESC""",
            (agent_id,),
        )).fetchall()
    stale = rows[keep:]
    if not stale:
        return
    for row in stale:
        filename = row["stream_url"].rsplit("/", 1)[-1]
        try:
            (VIDEO_OUT_DIR / filename).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("agenttv retention: failed to delete %s: %s", filename, e)
    ids = [row["id"] for row in stale]
    async with get_db() as db:
        await db.execute(
            f"UPDATE broadcasts SET status='archived' WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        await db.commit()

TOPICS = [
    "the strangest things found in deep ocean trenches",
    "why some ancient civilizations vanished without a clear cause",
    "how memory actually works in the human brain",
    "the economics of asteroid mining",
    "why cats and dogs see the world so differently",
    "the history of cryptography before computers existed",
    "what makes a joke funny, from a linguistics angle",
    "the physics of black holes for a curious non-scientist",
    "how ancient sailors navigated without modern instruments",
    "the weirdest unsolved mysteries in mathematics",
    "how language evolves and why slang spreads so fast",
    "the science of why we dream",
    "the most consequential accidental inventions in history",
    "how ecosystems recover after a disaster",
    "the psychology of why we procrastinate",
]


class PersonaChannel:
    """A generic always-on channel: fixed identity + fixed voice pair +
    a pluggable topic source. The flagship Agent.TV instance below uses a
    random static topic list (free-ranging AI-hosted talk show); the
    scanner-driven persona channels (AI Daily Wire, Crypto Tier One, Degen
    Frequency) each pass a topic_fn that pulls from real, already-running
    Ares scan data instead -- same engine, same jingle, different real
    material and different voice/personality per show."""

    def __init__(
        self, agent_name: str, bio: str, topic_fn, *,
        voices: dict | None = None, num_turns: int = NUM_TURNS_PER_EPISODE,
        category: str | None = None, accepts_user_submissions: bool = False,
    ):
        self.agent_name = agent_name
        self.bio = bio
        self.topic_fn = topic_fn
        self.voices = voices
        self.num_turns = num_turns
        self.category = category or agent_name
        self.accepts_user_submissions = accepts_user_submissions
        self.state = {
            "segmentUrl": None,
            "phase": "segment",  # 'segment' | 'filler'
            "startedAt": None,
            "duration": 0,
            "title": None,
            "cycle": 0,
        }
        self.running = False

    def now_playing(self) -> dict:
        return {**self.state, "now": int(time.time() * 1000)}

    async def start(self):
        if self.running:
            return
        self.running = True
        await ensure_jingle()
        asyncio.create_task(self._run())

    async def _next_user_submission(self) -> dict | None:
        """Oldest not-yet-aired user-submitted podcast (published via Collab's
        Create Podcast, kind=video), if any -- these air ahead of freshly
        auto-generated episodes. Only the flagship Agent.TV channel accepts
        these; persona channels are single-topic shows."""
        if not self.accepts_user_submissions:
            return None
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                """SELECT b.id, b.title, b.stream_url, b.duration_seconds
                   FROM broadcasts b
                   WHERE b.surface='cinema' AND b.cinema_kind='podcast'
                     AND b.status='ready' AND b.agenttv_aired_at IS NULL
                   ORDER BY b.created_at ASC LIMIT 1"""
            )).fetchone()
        if not row:
            return None
        async with get_db() as db:
            await db.execute("UPDATE broadcasts SET agenttv_aired_at=datetime('now') WHERE id=?", (row["id"],))
            await db.commit()
        return {"url": row["stream_url"], "duration": row["duration_seconds"] or 0, "title": row["title"]}

    async def _next_episode(self) -> dict:
        submitted = await self._next_user_submission()
        if submitted:
            logger.info("%s: airing user-submitted podcast %s", self.agent_name, submitted["title"])
            return submitted

        topic = await self.topic_fn() if inspect.iscoroutinefunction(self.topic_fn) else self.topic_fn()
        result = await generate_podcast(topic, "video", num_turns=self.num_turns, voices=self.voices)

        # Real broadcast, not just ephemeral channel state -- browsable in
        # Cinema's Agents tab like any other agent's content, with real
        # reactions (thumbs-up/down reuses the existing reaction system).
        agent = await _ensure_agent(self.agent_name, self.bio)
        script_text = "\n".join(f"**{t['speaker']}:** {t['text']}" for t in result["script"])
        bid = await _insert_broadcast(
            agent, title=topic[:300], description=f"Auto-generated {self.agent_name} episode",
            content_type="video", stream_url=result["stream_url"], thumbnail_url="",
            duration_sec=result["duration_sec"], post_content=script_text,
            tags=["podcast", "agenttv"], surface="cinema", cinema_kind="podcast", category=self.category,
        )
        # Mark aired immediately -- it's airing right now, this is not a
        # pending user submission for _next_user_submission() to pick up
        # again on a later cycle.
        async with get_db() as db:
            await db.execute("UPDATE broadcasts SET agenttv_aired_at=datetime('now') WHERE id=?", (bid,))
            await db.commit()
        try:
            await _prune_aired_episodes(agent["id"])
        except Exception as e:
            logger.warning("%s: retention prune failed: %s", self.agent_name, e)
        return {"url": result["stream_url"], "duration": result["duration_sec"], "title": topic}

    async def _run(self):
        # Retry loop for the very first episode too -- a bad voice ID or a
        # transient scan/generation failure here used to kill this whole
        # asyncio task silently forever (never surfaced as a Task exception
        # until GC, which could take a very long time), leaving the channel
        # permanently absent from the guide with zero error visible.
        current = None
        while current is None and self.running:
            try:
                current = await self._next_episode()
            except Exception as e:
                logger.error("%s: initial episode generation failed, retrying in 15s: %s", self.agent_name, e)
                await asyncio.sleep(15)
        if current is None:
            return
        while self.running:
            self.state = {
                "segmentUrl": current["url"], "phase": "segment",
                "startedAt": int(time.time() * 1000), "duration": current["duration"],
                "title": current["title"], "cycle": self.state["cycle"] + 1,
            }
            logger.info("%s: playing '%s' (%ss)", self.agent_name, current["title"], current["duration"])

            next_task = asyncio.create_task(self._next_episode())
            await asyncio.sleep(max(1, current["duration"]))

            # Jingle while we wait -- loop it if generation isn't done yet,
            # never cut to a not-ready segment.
            while self.running:
                self.state = {
                    **self.state, "segmentUrl": JINGLE_STREAM_URL, "phase": "filler",
                    "startedAt": int(time.time() * 1000), "duration": JINGLE_DURATION_SEC,
                }
                done, _ = await asyncio.wait({next_task}, timeout=JINGLE_DURATION_SEC)
                if next_task in done:
                    break

            try:
                current = next_task.result()
            except Exception as e:
                logger.warning("%s: episode generation failed, retrying: %s", self.agent_name, e)
                await asyncio.sleep(5)
                try:
                    current = await self._next_episode()
                except Exception as e2:
                    logger.error("%s: retry also failed: %s", self.agent_name, e2)
                    await asyncio.sleep(30)
                    current = {"url": JINGLE_STREAM_URL, "duration": JINGLE_DURATION_SEC, "title": "Technical difficulties"}


async def _live_agent_ids() -> dict[int, PersonaChannel]:
    """agent_id -> PersonaChannel for every always-on persona channel
    (flagship Agent.TV plus the scanner-driven shows) -- these all get the
    LIVE badge and a real generation loop instead of a virtual schedule."""
    result = {}
    for ch in ALL_CHANNELS:
        agent = await _ensure_agent(ch.agent_name, ch.bio)
        result[agent["id"]] = ch
    return result


async def list_channels() -> list[dict]:
    """Every agent with real published podcast content becomes its own
    channel -- Live-TV-style channel guide instead of one fixed rotation.
    Persona channels (flagship Agent.TV + the scanner-driven shows) are
    is_live=True (continuously generating fresh episodes, real-time
    rotation); every other agent's channel just loops their own
    already-published episodes on a deterministic virtual schedule (see
    now_playing_for_channel) so every viewer sees the same thing at the
    same time without a live generation loop per channel."""
    live_ids = await _live_agent_ids()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT a.id as agent_id, a.name as agent_name, a.avatar_url,
                      COUNT(*) as episode_count,
                      (SELECT b2.thumbnail_url FROM broadcasts b2
                         WHERE b2.agent_id = a.id AND b2.surface='cinema'
                           AND b2.cinema_kind='podcast' AND b2.status='ready'
                         ORDER BY b2.id DESC LIMIT 1) as cover_url
               FROM broadcasts b JOIN agents a ON a.id = b.agent_id
               WHERE b.surface='cinema' AND b.cinema_kind='podcast' AND b.status='ready'
               GROUP BY a.id ORDER BY (a.id IN (%s)) DESC, episode_count DESC""" % (
                ",".join(str(i) for i in live_ids) or "-1"
            )
        )).fetchall()
    return [
        {**dict(r), "is_live": r["agent_id"] in live_ids}
        for r in rows
    ]


async def now_playing_for_channel(agent_id: int) -> dict:
    """For any always-on persona channel, the real live rotation state.
    For any other agent's channel: a deterministic virtual schedule over
    their own published episodes -- current wall-clock time modulo the
    total playlist length picks the "currently airing" episode and offset,
    so every viewer watching agent X's channel is in sync with each other
    without needing a separate generation loop per agent."""
    live_ids = await _live_agent_ids()
    if agent_id in live_ids:
        return live_ids[agent_id].now_playing()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, title, stream_url, duration_seconds FROM broadcasts
               WHERE agent_id=? AND surface='cinema' AND cinema_kind='podcast' AND status='ready'
               ORDER BY id ASC""",
            (agent_id,),
        )).fetchall()
    if not rows:
        return {"segmentUrl": None, "phase": "segment", "startedAt": None, "duration": 0, "title": None, "cycle": 0, "now": int(time.time() * 1000)}

    total = sum(max(1, r["duration_seconds"] or 1) for r in rows)
    now_s = int(time.time())
    offset = now_s % total
    acc = 0
    for i, r in enumerate(rows):
        dur = max(1, r["duration_seconds"] or 1)
        if offset < acc + dur:
            started_at = (now_s - (offset - acc)) * 1000
            return {
                "segmentUrl": r["stream_url"], "phase": "segment", "startedAt": started_at,
                "duration": dur, "title": r["title"], "cycle": i, "now": int(time.time() * 1000),
            }
        acc += dur
    # Shouldn't happen (offset < total by construction), but stay safe.
    r = rows[0]
    return {
        "segmentUrl": r["stream_url"], "phase": "segment", "startedAt": now_s * 1000,
        "duration": r["duration_seconds"] or 0, "title": r["title"], "cycle": 0, "now": int(time.time() * 1000),
    }


channel = PersonaChannel(
    AGENTTV_AGENT_NAME,
    "Vantage's always-on AI podcast channel — real two-host dialogue, new episode every few minutes, 24/7.",
    lambda: random.choice(TOPICS),
    accepts_user_submissions=True,
)

ai_news_channel = PersonaChannel(
    "AI Daily Wire",
    "Vantage's 24/7 AI news show — two hosts react to real, current AI headlines every episode, not a fixed script.",
    ai_news_topic,
    voices={"A": "en-US-EricNeural", "B": "en-US-AriaNeural"},
    num_turns=NUM_TURNS_PER_SEGMENT,
    category="AI News",
)

crypto_tier_channel = PersonaChannel(
    "Crypto Tier One",
    "Vantage's 24/7 institutional-grade crypto market briefing — real chain health, price consensus, and arbitrage data every episode.",
    crypto_tier_topic,
    voices={"A": "en-US-ChristopherNeural", "B": "en-US-MichelleNeural"},
    num_turns=NUM_TURNS_PER_SEGMENT,
    category="Crypto Tier One",
)

degen_channel = PersonaChannel(
    "Degen Frequency",
    "Vantage's 24/7 degen crypto show — real trending on-chain tokens, hyped up every episode.",
    crypto_degen_topic,
    voices={"A": "en-US-RogerNeural", "B": "en-US-EmmaNeural"},
    num_turns=NUM_TURNS_PER_SEGMENT,
    category="Degen Frequency",
)

ALL_CHANNELS = [channel, ai_news_channel, crypto_tier_channel, degen_channel]


async def start_all_channels():
    for ch in ALL_CHANNELS:
        await ch.start()
