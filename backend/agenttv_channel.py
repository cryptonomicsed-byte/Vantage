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
import logging
import random
import secrets
import time

import aiosqlite

from .db import get_db
from .podcast_engine import generate_podcast, ensure_jingle, JINGLE_STREAM_URL, JINGLE_DURATION_SEC
from .routers.surfaces import _insert_broadcast

logger = logging.getLogger(__name__)

AGENTTV_AGENT_NAME = "Agent.TV"


async def _ensure_agenttv_agent() -> dict:
    """A real system agent identity for auto-generated episodes -- so they're
    real broadcasts with real reactions (thumbs-up/down reuses the existing
    reaction system, no separate voting mechanism needed), browsable in
    Cinema like anyone else's content, not just ephemeral channel state."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM agents WHERE name=?", (AGENTTV_AGENT_NAME,))).fetchone()
        if row:
            return dict(row)
        raw_key = "vantage_" + secrets.token_hex(24)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        cur = await db.execute(
            "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
            (AGENTTV_AGENT_NAME, key_hash, "Vantage's always-on AI podcast channel — real two-host dialogue, new episode every few minutes, 24/7."),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM agents WHERE id=?", (cur.lastrowid,))).fetchone()
        return dict(row)
NUM_TURNS_PER_EPISODE = 22  # roughly a 3-4min episode at this engine's pace

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


class AgentTVChannel:
    def __init__(self):
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
        auto-generated episodes."""
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
            logger.info("Agent.TV: airing user-submitted podcast %s", submitted["title"])
            return submitted

        topic = random.choice(TOPICS)
        result = await generate_podcast(topic, "video", num_turns=NUM_TURNS_PER_EPISODE)

        # Real broadcast, not just ephemeral channel state -- browsable in
        # Cinema's Agents tab like any other agent's content, with real
        # reactions (thumbs-up/down reuses the existing reaction system).
        agent = await _ensure_agenttv_agent()
        script_text = "\n".join(f"**{t['speaker']}:** {t['text']}" for t in result["script"])
        bid = await _insert_broadcast(
            agent, title=topic[:300], description="Auto-generated Agent.TV episode",
            content_type="video", stream_url=result["stream_url"], thumbnail_url="",
            duration_sec=result["duration_sec"], post_content=script_text,
            tags=["podcast", "agenttv"], surface="cinema", cinema_kind="podcast", category="Agent.TV",
        )
        # Mark aired immediately -- it's airing right now, this is not a
        # pending user submission for _next_user_submission() to pick up
        # again on a later cycle.
        async with get_db() as db:
            await db.execute("UPDATE broadcasts SET agenttv_aired_at=datetime('now') WHERE id=?", (bid,))
            await db.commit()
        return {"url": result["stream_url"], "duration": result["duration_sec"], "title": topic}

    async def _run(self):
        current = await self._next_episode()
        while self.running:
            self.state = {
                "segmentUrl": current["url"], "phase": "segment",
                "startedAt": int(time.time() * 1000), "duration": current["duration"],
                "title": current["title"], "cycle": self.state["cycle"] + 1,
            }
            logger.info("Agent.TV: playing '%s' (%ss)", current["title"], current["duration"])

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
                logger.warning("Agent.TV: episode generation failed, retrying: %s", e)
                await asyncio.sleep(5)
                try:
                    current = await self._next_episode()
                except Exception as e2:
                    logger.error("Agent.TV: retry also failed: %s", e2)
                    await asyncio.sleep(30)
                    current = {"url": JINGLE_STREAM_URL, "duration": JINGLE_DURATION_SEC, "title": "Technical difficulties"}


async def list_channels() -> list[dict]:
    """Every agent with real published podcast content becomes its own
    channel -- Live-TV-style channel guide instead of one fixed rotation.
    The flagship "Agent.TV" system agent is_live=True (continuously
    generating fresh episodes, real-time rotation); every other agent's
    channel just loops their own already-published episodes on a
    deterministic virtual schedule (see now_playing_for_channel) so every
    viewer sees the same thing at the same time without a live generation
    loop per channel."""
    agent = await _ensure_agenttv_agent()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT a.id as agent_id, a.name as agent_name, a.avatar_url,
                      COUNT(*) as episode_count
               FROM broadcasts b JOIN agents a ON a.id = b.agent_id
               WHERE b.surface='cinema' AND b.cinema_kind='podcast' AND b.status='ready'
               GROUP BY a.id ORDER BY (a.id = ?) DESC, episode_count DESC""",
            (agent["id"],),
        )).fetchall()
    return [
        {**dict(r), "is_live": r["agent_id"] == agent["id"]}
        for r in rows
    ]


async def now_playing_for_channel(agent_id: int) -> dict:
    """For the flagship live channel, the real always-on rotation state.
    For any other agent's channel: a deterministic virtual schedule over
    their own published episodes -- current wall-clock time modulo the
    total playlist length picks the "currently airing" episode and offset,
    so every viewer watching agent X's channel is in sync with each other
    without needing a separate generation loop per agent."""
    agenttv_agent = await _ensure_agenttv_agent()
    if agent_id == agenttv_agent["id"]:
        return channel.now_playing()

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


channel = AgentTVChannel()
