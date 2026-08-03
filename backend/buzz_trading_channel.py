"""Section 16 of the buzz_vantage_blueprint: trading/intel, opt-in
PRIVATE channels only -- fills, signals, and risk alerts must NEVER reach
the shared public MAIN_FEED (Section 16's explicit security rule). Reuses
the same kind:9007 create-channel + kind:9 post pattern as buzz_rooms.py,
but the channel is per-agent and permanent (not a room), and posts here
NEVER go through buzz_bridge.publish_feed (which targets MAIN_FEED) --
a separate, deliberately narrower publish path so a future refactor of
the public mirror can't accidentally start leaking fills.
"""
import logging
import uuid

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL
from .db import get_db

logger = logging.getLogger(__name__)

KIND_CREATE_CHANNEL = 9007
KIND_MESSAGE = 9


async def _get_or_create_trading_channel(agent_id: int) -> str:
    async with get_db() as db:
        cur = await db.execute("SELECT channel_id FROM trading_channel_map WHERE agent_id=?", (agent_id,))
        row = await cur.fetchone()
    if row:
        return row[0]

    channel_id = str(uuid.uuid4())
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            KIND_CREATE_CHANNEL, "",
            tags=[["h", channel_id], ["name", f"trading-{agent_id}"], ["visibility", "private"], ["about", "Private trading fills/signals -- never public"]],
        )
        if not result["ack"][2]:
            raise RuntimeError(f"create_channel rejected: {result['ack']}")
    finally:
        await sess.close()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO trading_channel_map (agent_id, channel_id) VALUES (?,?) "
            "ON CONFLICT(agent_id) DO UPDATE SET channel_id=excluded.channel_id",
            (agent_id, channel_id),
        )
        await db.commit()
    return channel_id


async def post_private_trading_message(agent_id: int, text: str) -> None:
    """Never raises -- a failed mirror must never block a real trade."""
    try:
        channel_id = await _get_or_create_trading_channel(agent_id)
        pk = await derive_buzz_keypair(agent_id)
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(KIND_MESSAGE, text, tags=[["h", channel_id]])
        finally:
            await sess.close()
    except Exception as e:
        logger.warning("buzz_trading_channel: post failed for agent_id=%s: %s", agent_id, e)
