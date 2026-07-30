"""Buzz direct messages. ADDON, purely additive: a new capability, not a
replacement of any existing chat/channel feature. DMs are modeled by the
relay as regular channels under the hood (confirmed against
buzz-relay/crates/buzz-relay/src/handlers/command_executor.rs's
handle_dm_open) -- opening one publishes kind:41010 with p-tags for the
other participant(s); the relay's OK ack message carries
`response:{"channel_id": "...", "created": bool}` embedded as a string
prefix; actual messages are then just plain kind:9 events tagged
`["h", channel_id]`, exactly like a normal channel -- no separate
"DM message" kind exists.
"""
import json
import time
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL

KIND_DM_OPEN = 41010
KIND_STREAM_MESSAGE = 9


async def open_dm(agent_id: int, other_pubkey_hex: str) -> dict:
    """Open (or re-open) a 1:1 DM with `other_pubkey_hex`. Returns the
    channel_id to use for sending/reading messages."""
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(KIND_DM_OPEN, "", tags=[["p", other_pubkey_hex]])
    finally:
        await sess.close()

    ack = result["ack"]
    if not ack[2]:
        raise RuntimeError(f"relay rejected dm_open: {ack}")
    message = ack[3] if len(ack) > 3 else ""
    if not message.startswith("response:"):
        raise RuntimeError(f"unexpected dm_open ack shape (no response: prefix): {ack}")
    response = json.loads(message[len("response:"):])
    return {"channel_id": response["channel_id"], "created": response["created"]}


async def send_dm_message(agent_id: int, channel_id: str, text: str) -> dict:
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(KIND_STREAM_MESSAGE, text, tags=[["h", channel_id]])
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected dm message: {result['ack']}")
    return {"ok": True, "event": result["event"]}


async def list_dm_messages(agent_id: int, channel_id: str, limit: int = 50) -> list:
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        sub_id = await sess.subscribe([
            {"kinds": [KIND_STREAM_MESSAGE], "#h": [channel_id], "limit": limit}
        ])
        events = await sess.recv_until_eose(sub_id, max_events=limit)
    finally:
        await sess.close()
    events.sort(key=lambda e: e["created_at"])
    return [
        {"pubkey": e["pubkey"], "content": e["content"], "created_at": e["created_at"]}
        for e in events
    ]
