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


async def send_cross_instance_dm(agent_id: int, peer_relay_ws_url: str, recipient_pubkey_hex: str, text: str) -> dict:
    """Section 6.4: cross-instance DM. This relay's real DM model is
    kind:41010 dm_open + plain kind:9 channel messages (see module
    docstring) -- NOT literally NIP-17 gift-wrap as the blueprint's
    summary assumed (confirmed by reading command_executor.rs directly).
    Cross-relay compatibility here means connecting to the RECIPIENT's
    own relay (not ours) and running the exact same open+send flow with
    our agent's own keypair -- same identity everywhere, no separate
    federation credential, matching Section 6.5's point that Vantage's
    existing nostr-auth model already generalizes to this.

    Real constraint, not glossed over: our agent's pubkey must already be
    a MEMBER of the peer's relay for this to succeed (this relay gates
    kind:9/41010 publish by relay membership) -- there is no cross-relay
    auto-provisioning built here. Returns a clear ok/error result rather
    than silently swallowing a failure, since "the peer relay rejected
    us, go coordinate membership" is a real, actionable outcome the
    caller needs to see."""
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(peer_relay_ws_url, pk)
    try:
        await sess.connect()
        await sess.authenticate()
    except Exception as e:
        return {"ok": False, "error": f"could not connect/authenticate to peer relay: {e}"}

    try:
        open_result = await sess.publish(KIND_DM_OPEN, "", tags=[["p", recipient_pubkey_hex]])
        ack = open_result["ack"]
        if not ack[2]:
            return {"ok": False, "error": f"peer relay rejected dm_open (likely not a member there): {ack}"}
        message = ack[3] if len(ack) > 3 else ""
        if not message.startswith("response:"):
            return {"ok": False, "error": f"unexpected dm_open ack shape: {ack}"}
        response = json.loads(message[len("response:"):])
        channel_id = response["channel_id"]

        send_result = await sess.publish(KIND_STREAM_MESSAGE, text, tags=[["h", channel_id]])
        if not send_result["ack"][2]:
            return {"ok": False, "error": f"peer relay rejected the message: {send_result['ack']}"}
        return {"ok": True, "channel_id": channel_id, "event": send_result["event"]}
    finally:
        await sess.close()


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
