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


async def _consume(sess: BuzzSession, sub_id: str) -> None:
    """Index everything the relay sends until the stream ends."""
    async for event in sess.stream_events(sub_id):
        try:
            await index_event(event)
        except Exception as exc:
            # One malformed or unexpected event must never kill the listener;
            # the next one may be perfectly good.
            logger.warning("coordination_indexer: failed to index event %s: %s",
                           (event or {}).get("id", "?")[:8], exc)


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
