"""Section 4 of the buzz_vantage_blueprint: rooms <-> channels + canvases.

kind:9007 (create-channel) and kind:40100 (set-canvas) confirmed real by
reading buzz-sdk/src/builders.rs directly, not assumed from the
blueprint's summary. Canvas is latest-wins per channel (NOT NIP-33
addressable -- 40100 is outside the 30000-39999 parameterized-replaceable
range), so Section 4.2's "canvas content = JSON {key: value}" is
implemented by always publishing the FULL current scratchpad dict, not a
diff -- matches this relay's actual single-doc-per-channel model.
"""
import json
import logging

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL
from .db import get_db

logger = logging.getLogger(__name__)

KIND_CREATE_CHANNEL = 9007
KIND_SET_CANVAS = 40100


async def create_room_channel(agent_id: int, room_id: str, room_name: str) -> str:
    """Section 4.1: create a real private buzz channel for a Vantage room.
    Returns the buzz channel_id (a UUID string) and stores the mapping.
    Never raises -- a failed mirror shouldn't block room creation itself."""
    import uuid
    channel_id = str(uuid.uuid4())
    try:
        pk = await derive_buzz_keypair(agent_id)
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            result = await sess.publish(
                KIND_CREATE_CHANNEL, "",
                tags=[["h", channel_id], ["name", f"room-{room_id}"], ["visibility", "private"], ["about", room_name]],
            )
            if not result["ack"][2]:
                logger.warning("buzz_rooms: create_channel rejected for room %s: %s", room_id, result["ack"])
                return ""
        finally:
            await sess.close()

        async with get_db() as db:
            await db.execute(
                "INSERT INTO room_channel_map (room_id, channel_id) VALUES (?,?) "
                "ON CONFLICT(room_id) DO UPDATE SET channel_id=excluded.channel_id",
                (room_id, channel_id),
            )
            await db.commit()
        return channel_id
    except Exception as e:
        logger.warning("buzz_rooms: create_room_channel failed for room %s: %s", room_id, e)
        return ""


async def sync_scratchpad_to_canvas(agent_id: int, room_id: str) -> None:
    """Section 4.2: mirrors the room's ENTIRE current scratchpad (all
    keys) as one kind:40100 canvas update -- this relay's canvas is
    latest-wins per channel, not a keyed document, so a partial/diff
    update would silently lose other keys."""
    async with get_db() as db:
        cur = await db.execute("SELECT channel_id FROM room_channel_map WHERE room_id=?", (room_id,))
        row = await cur.fetchone()
    if not row:
        return
    channel_id = row[0]

    async with get_db() as db:
        cur = await db.execute("SELECT host_id FROM agent_rooms WHERE id=?", (room_id,))
        host_row = await cur.fetchone()
    if not host_row:
        return
    host_id = host_row[0]

    prefix = f"room:{room_id}:"
    async with get_db() as db:
        cur = await db.execute(
            "SELECT key, value FROM agent_state WHERE agent_id=? AND key LIKE ?",
            (host_id, prefix + "%"),
        )
        rows = await cur.fetchall()
    scratchpad = {k[len(prefix):]: v for k, v in rows}

    try:
        pk = await derive_buzz_keypair(agent_id)
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(KIND_SET_CANVAS, json.dumps(scratchpad), tags=[["h", channel_id]])
        finally:
            await sess.close()
    except Exception as e:
        logger.warning("buzz_rooms: sync_scratchpad_to_canvas failed for room %s: %s", room_id, e)


async def sync_snapshot_to_canvas(agent_id: int, room_id: str, snapshot_label: str, snapshot_content: str) -> None:
    """Section 4.3: workspace snapshot commit -> another canvas update.
    Not addressable/versioned server-side (see module docstring), but the
    relay keeps every kind:40100 event in its own event history, so a
    real version trail exists for free via that history even though the
    channel's "current" canvas is always just the latest one."""
    async with get_db() as db:
        cur = await db.execute("SELECT channel_id FROM room_channel_map WHERE room_id=?", (room_id,))
        row = await cur.fetchone()
    if not row:
        return
    channel_id = row[0]
    content = json.dumps({"snapshot": snapshot_label, "content": snapshot_content})
    try:
        pk = await derive_buzz_keypair(agent_id)
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(KIND_SET_CANVAS, content, tags=[["h", channel_id]])
        finally:
            await sess.close()
    except Exception as e:
        logger.warning("buzz_rooms: sync_snapshot_to_canvas failed for room %s: %s", room_id, e)
