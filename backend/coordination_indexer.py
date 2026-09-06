"""Relay → index listener for guild channel messages.

The counterpart to backend/coordination.py's publish path, and the reason
external agents work at all: an agent that holds its own key publishes
straight to the relay and never touches this backend, so without a listener
its messages would simply not exist as far as Vantage is concerned. This
task subscribes to every channel the instance owns and mirrors what it sees
into channel_messages.

Modeled on buzz_inbound.py: one long-lived listener authenticated as the
deployment's own instance identity, reconnecting with backoff, started as a
background task at app startup. Two differences worth knowing about:

  * It resubscribes when the channel set changes, because channels are
    created at runtime and a NIP-01 filter is fixed at subscription time.
  * Indexing is idempotent on event_id, so a reconnect that replays events
    is harmless — which is what lets `since` be deliberately conservative.
"""
import asyncio
import logging
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_instance_keypair
from .buzz_registration import RELAY_WS_URL
from .coordination import KIND_MESSAGE, index_event
from .db import get_db

logger = logging.getLogger(__name__)

RECONNECT_BACKOFF_SECONDS = 2
MAX_RECONNECT_BACKOFF_SECONDS = 60

# How often to re-check whether channels have been created or removed. A new
# channel becomes live within this window; posting through the API indexes
# immediately regardless, so this only bounds the external-agent path.
CHANNEL_REFRESH_SECONDS = 30

# Relay filters cap out well before this in practice, but an instance with
# thousands of channels would build an unusable filter. Past this we drop the
# `#h` constraint and filter locally instead — index_event already ignores
# events for channels we don't own.
MAX_FILTER_CHANNELS = 400

# Replay window on reconnect. Cheap because indexing is idempotent, and it
# covers events that landed while the socket was down.
REPLAY_SECONDS = 300


async def _known_channel_ids() -> list[str]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT buzz_channel_id FROM guild_channels WHERE buzz_channel_id IS NOT NULL"
        )
        rows = await cur.fetchall()
    return sorted(r[0] for r in rows if r[0])


async def _since_timestamp() -> int:
    """Newest indexed message, minus a replay window."""
    async with get_db() as db:
        cur = await db.execute("SELECT MAX(created_at) FROM channel_messages")
        row = await cur.fetchone()
    newest = (row[0] if row else None) or 0
    return max(0, int(newest) - REPLAY_SECONDS)


def _build_filter(channel_ids: list[str], since: int) -> dict:
    filt: dict = {"kinds": [KIND_MESSAGE]}
    if since:
        filt["since"] = since
    if channel_ids and len(channel_ids) <= MAX_FILTER_CHANNELS:
        filt["#h"] = channel_ids
    return filt


async def _dispatch_guild_mentions(event: dict) -> None:
    """Fire guild_chat mention dispatch for a newly-indexed relay event.

    This is the path for agents that hold their own keys and post straight
    to the relay rather than through the REST API — the REST handler calls
    dispatch_to_mentioned itself, but relay-native posts only reach dispatch
    here, after the indexer has already written the row.

    Never raises: one agent failing to answer must not break the indexer.
    """
    from .coordination import get_channel_by_buzz_id, parse_message_event
    from .guild_chat import dispatch_depth, dispatch_to_mentioned, parse_mentions, resolve_mentions

    content = event.get("content", "")
    mentions = parse_mentions(content)
    if not mentions:
        return

    parsed = parse_message_event(event)
    channel = await get_channel_by_buzz_id(parsed["buzz_channel_id"])
    if channel is None:
        return

    depth = dispatch_depth(event)
    if depth >= 3:
        return

    async with get_db() as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT slug FROM guilds WHERE id=?", (channel["guild_id"],))
        row = await cur.fetchone()
        if not row:
            return
        guild_slug = row["slug"]

        cur = await db.execute(
            "SELECT * FROM principals WHERE pubkey=? LIMIT 1", (event.get("pubkey", ""),)
        )
        row = await cur.fetchone()
        author_principal = dict(row) if row else {
            "id": None, "pubkey": event.get("pubkey", ""), "display_name": "unknown",
        }

    mentioned = await resolve_mentions(channel["guild_id"], mentions)
    if not mentioned:
        return

    logger.info(
        "coordination_indexer: dispatching @mentions %s in %s/%s (depth=%d)",
        mentions, guild_slug, channel["slug"], depth,
    )
    try:
        await dispatch_to_mentioned(
            channel=channel,
            guild_slug=guild_slug,
            content=content,
            author_principal=author_principal,
            mentioned=mentioned,
            depth=depth,
            root_event_id=parsed.get("thread_root_event_id"),
        )
    except Exception as exc:
        logger.warning("coordination_indexer: mention dispatch failed: %s", exc)


async def _consume(sess: BuzzSession, sub_id: str) -> None:
    """Index everything the relay sends until the stream ends."""
    async for event in sess.stream_events(sub_id):
        try:
            row_id = await index_event(event)
            if row_id is not None:
                await _tell_conductor(event)
                await _dispatch_guild_mentions(event)
        except Exception as exc:
            # One malformed or unexpected event must never kill the listener;
            # the next one may be perfectly good.
            logger.warning("coordination_indexer: failed to index event %s: %s",
                           (event or {}).get("id", "?")[:8], exc)


async def _tell_conductor(event: dict) -> None:
    """Hand a newly-indexed event to the Conductor so it can judge the turn.

    This is the observation path the spec drew as Conductor->relay: the
    Conductor cannot subscribe to the relay itself (NIP-42 needs a schnorr
    signature the BEAM cannot produce here), so the tier that already reads
    every event forwards it. No-op when no Conductor is configured, which is
    what keeps pre-Phase-2 deployments unchanged.
    """
    from .coordination import get_channel_by_buzz_id, parse_message_event
    from .routers.conductor import notify_observed

    parsed = parse_message_event(event)
    channel = await get_channel_by_buzz_id(parsed["buzz_channel_id"])
    if channel is None:
        return
    async with get_db() as db:
        cur = await db.execute(
            "SELECT principal_id FROM channel_messages WHERE event_id=?", (parsed["event_id"],)
        )
        row = await cur.fetchone()
    await notify_observed(channel["id"], row[0] if row else None, parsed["msg_type"])


async def _watch_channel_set(initial: list[str]) -> None:
    """Return once the channel set differs from `initial`, so the caller can
    tear down the subscription and build a new filter."""
    while True:
        await asyncio.sleep(CHANNEL_REFRESH_SECONDS)
        try:
            if await _known_channel_ids() != initial:
                return
        except Exception as exc:
            logger.debug("coordination_indexer: channel-set check failed: %s", exc)


_dispatched_event_ids: set[str] = set()
_dispatched_timestamps: dict[str, float] = {}
_DISPATCH_TTL = 600  # seconds; clear tracked IDs after this long


async def run_mention_dispatch_poller() -> None:
    """Fallback: scan channel_messages every 30 s for @mentions without replies.

    This catches messages posted by relay-native agents when the coordination
    indexer's relay connection is down (NIP-42 auth failure). Relay-indexed
    messages are handled in _consume via _dispatch_guild_mentions; this path
    exists so no @mention goes permanently unanswered just because the relay
    was unreachable at the moment the message was indexed.

    Skips messages that were already dispatched this session (tracked by
    event_id) and messages older than 10 minutes (stale enough that a person
    should reply instead).
    """
    import time as _time
    POLL_INTERVAL = 30
    LOOKBACK_SECONDS = 600  # 10 minutes

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            now = _time.time()
            cutoff = int(now) - LOOKBACK_SECONDS

            # Purge old tracked IDs to keep memory bounded.
            stale = [eid for eid, ts in _dispatched_timestamps.items() if now - ts > _DISPATCH_TTL]
            for eid in stale:
                _dispatched_event_ids.discard(eid)
                _dispatched_timestamps.pop(eid, None)

            async with get_db() as db:
                import aiosqlite
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    """SELECT cm.event_id, cm.content, cm.pubkey, cm.channel_id,
                              cm.thread_root_event_id,
                              gc.slug as channel_slug, gc.buzz_channel_id, gc.guild_id,
                              g.slug as guild_slug
                         FROM channel_messages cm
                         JOIN guild_channels gc ON gc.id=cm.channel_id
                         JOIN guilds g ON g.id=gc.guild_id
                        WHERE cm.created_at >= ?
                          AND cm.content LIKE '%@%'
                          AND cm.msg_type = 'say'
                        ORDER BY cm.created_at ASC""",
                    (cutoff,),
                )
                candidates = [dict(r) for r in await cur.fetchall()]

            for row in candidates:
                event_id = row["event_id"]
                if event_id in _dispatched_event_ids:
                    continue

                from .guild_chat import parse_mentions
                mentions = parse_mentions(row["content"])
                if not mentions:
                    _dispatched_event_ids.add(event_id)
                    _dispatched_timestamps[event_id] = now
                    continue

                # Check if a reply already exists for this message.
                async with get_db() as db:
                    cur = await db.execute(
                        "SELECT 1 FROM channel_messages WHERE thread_root_event_id=? LIMIT 1",
                        (event_id,),
                    )
                    already_replied = await cur.fetchone() is not None

                if already_replied:
                    _dispatched_event_ids.add(event_id)
                    _dispatched_timestamps[event_id] = now
                    continue

                # Mark before dispatching so a concurrent reply doesn't double-fire.
                _dispatched_event_ids.add(event_id)
                _dispatched_timestamps[event_id] = now

                from .coordination import get_channel_by_buzz_id
                from .guild_chat import dispatch_to_mentioned, resolve_mentions

                channel = await get_channel_by_buzz_id(row["buzz_channel_id"])
                if channel is None:
                    continue

                async with get_db() as db:
                    import aiosqlite
                    db.row_factory = aiosqlite.Row
                    cur = await db.execute(
                        "SELECT * FROM principals WHERE pubkey=? LIMIT 1", (row["pubkey"],)
                    )
                    prow = await cur.fetchone()
                author_principal = dict(prow) if prow else {
                    "id": None, "pubkey": row["pubkey"], "display_name": "unknown",
                }

                mentioned = await resolve_mentions(row["guild_id"], mentions)
                if not mentioned:
                    continue

                logger.info(
                    "mention_poller: dispatching unanswered @mentions %s in %s/%s",
                    mentions, row["guild_slug"], row["channel_slug"],
                )
                try:
                    await dispatch_to_mentioned(
                        channel=channel,
                        guild_slug=row["guild_slug"],
                        content=row["content"],
                        author_principal=author_principal,
                        mentioned=mentioned,
                        depth=0,
                        root_event_id=row.get("thread_root_event_id"),
                    )
                except Exception as exc:
                    logger.warning("mention_poller: dispatch failed for %s: %s", event_id[:8], exc)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("mention_poller: scan failed: %s", exc)


async def run_coordination_indexer() -> None:
    """Entry point. Runs for the lifetime of the app."""
    backoff = RECONNECT_BACKOFF_SECONDS
    while True:
        sess: Optional[BuzzSession] = None
        try:
            channel_ids = await _known_channel_ids()
            if not channel_ids:
                # Nothing provisioned yet. Idle rather than opening a
                # subscription that would match every kind 9 on the relay.
                await asyncio.sleep(CHANNEL_REFRESH_SECONDS)
                continue

            pk = await derive_instance_keypair()
            sess = BuzzSession(RELAY_WS_URL, pk)
            await sess.connect()
            await sess.authenticate()

            since = await _since_timestamp()
            sub_id = await sess.subscribe([_build_filter(channel_ids, since)])
            logger.info("coordination_indexer: watching %d channel(s) since %d",
                        len(channel_ids), since)
            backoff = RECONNECT_BACKOFF_SECONDS  # a good connection resets the penalty

            consume_task = asyncio.create_task(_consume(sess, sub_id))
            watch_task = asyncio.create_task(_watch_channel_set(channel_ids))
            done, pending = await asyncio.wait(
                {consume_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # Surface a consume-side failure rather than reconnecting blindly.
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("coordination_indexer: %s — reconnecting in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_SECONDS)
        finally:
            if sess is not None:
                try:
                    await sess.close()
                except Exception as exc:
                    logger.debug("silenced indexer session close: %s", exc)
