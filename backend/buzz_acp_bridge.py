"""buzz-acp bridge: lets a Vantage agent be chatted with over a Buzz/Nostr
channel instead of only HTTP. Listens for kind:9 messages in a channel not
authored by itself, routes the text through the same _dispatch_chat() path
Copilot's HTTP endpoint uses (so cognition_url wiring, the regex intent
parser, everything, works identically), and publishes the reply back as a
signed kind:9 event threaded (NIP-10) to the incoming message.

Not a systemd daemon yet -- this is the shim itself (run_bridge), callable
standalone for testing or wrapped in a service later.
"""
import asyncio
import logging
import time

from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from .buzz_client import BuzzSession
from .db import get_db

logger = logging.getLogger(__name__)


async def _load_agent_row(agent_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = None
        cur = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


async def run_bridge(agent_id: int, relay_ws_url: str, channel_id: str, max_messages: int = 1):
    """Listens for up to max_messages inbound messages (not authored by this
    agent's own pubkey) in the given channel, dispatches each through
    _dispatch_chat, and publishes the reply. max_messages is a test/demo
    knob -- pass None to run forever."""
    from .routers.copilot import _dispatch_chat  # local import: avoid router-import cycle at module load

    agent_row = await _load_agent_row(agent_id)
    pk = await derive_buzz_keypair(agent_id)
    my_pubkey = public_key_xonly_hex(pk)

    sess = BuzzSession(relay_ws_url, pk)
    await sess.connect()
    await sess.authenticate()
    sub = await sess.subscribe([{"kinds": [9], "#h": [channel_id], "since": int(time.time())}])

    handled = 0
    while max_messages is None or handled < max_messages:
        msg = await sess._recv_json()
        if msg[0] != "EVENT" or msg[1] != sub:
            continue
        event = msg[2]
        if event["pubkey"] == my_pubkey:
            continue  # ignore our own messages
        text = event["content"]
        logger.info("buzz-acp inbound from %s: %s", event["pubkey"][:12], text[:80])
        result = await _dispatch_chat(agent_row, text, human_id=f"buzz:{event['pubkey'][:16]}")
        reply_text = result.get("data", {}).get("reply") or f"[{result.get('action')}] {result.get('data')}"
        await sess.publish(
            9,
            reply_text,
            tags=[["h", channel_id], ["e", event["id"], "", "reply"], ["p", event["pubkey"]]],
        )
        logger.info("buzz-acp replied: %s", reply_text[:80])
        handled += 1

    await sess.close()
    return handled
