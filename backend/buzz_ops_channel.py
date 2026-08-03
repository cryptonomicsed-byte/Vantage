"""Section 36.1 of the buzz_vantage_blueprint extension: error reports ->
a private #ops channel (kind:9007 create-channel, same real mechanism
already proven in buzz_rooms.py/buzz_trading_channel.py) -- one shared
channel for the whole instance (not per-agent, matching "private #ops
channel per community"), owned by the instance identity."""
import logging
import uuid

from .buzz_client import BuzzSession
from .buzz_identity import derive_instance_keypair
from .buzz_registration import RELAY_WS_URL
from .db import get_db

logger = logging.getLogger(__name__)

KIND_CREATE_CHANNEL = 9007
KIND_MESSAGE = 9


async def _get_or_create_ops_channel() -> str:
    async with get_db() as db:
        cur = await db.execute("SELECT value FROM buzz_config WHERE key='OPS_CHANNEL'")
        row = await cur.fetchone()
    if row:
        return row[0]

    channel_id = str(uuid.uuid4())
    pk = await derive_instance_keypair()
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            KIND_CREATE_CHANNEL, "",
            tags=[["h", channel_id], ["name", "ops"], ["visibility", "private"], ["about", "Vantage error reports / operator monitoring"]],
        )
        if not result["ack"][2]:
            raise RuntimeError(f"create_channel rejected: {result['ack']}")
    finally:
        await sess.close()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO buzz_config (key, value) VALUES ('OPS_CHANNEL', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (channel_id,),
        )
        await db.commit()
    return channel_id


async def post_ops_message(text: str) -> None:
    """Never raises -- a failed ops-channel mirror must never block the
    real error_reports write."""
    try:
        channel_id = await _get_or_create_ops_channel()
        pk = await derive_instance_keypair()
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(KIND_MESSAGE, text, tags=[["h", channel_id]])
        finally:
            await sess.close()
    except Exception as e:
        logger.warning("buzz_ops_channel: post failed: %s", e)
