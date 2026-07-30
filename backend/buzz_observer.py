"""Buzz Agent Observer Frame mirror (kind:24200, ephemeral). ADDON: mirrors
entries already flowing through Vantage's real Activity Feed (backend/
agents.py's /me/activity-feed, backed by the hash-chained receipts table)
out as a live, encrypted Nostr frame -- the receipts table stays the
authoritative record; this is a one-way broadcast of it, and skipping it
changes nothing about how the Activity Feed itself works.

kind:24200 is in P_GATED_KINDS (buzz-relay's kind.rs): the relay only
delivers it to a reader whose own pubkey matches the event's `#p` tag.
It's ALSO validated at ingest (handlers/event.rs's agent_observer_route,
confirmed live): the `p` tag (owner) and `agent` tag MUST be genuinely
different pubkeys -- a naive self-owned frame (p == agent) satisfies
neither the telemetry direction (needs recipient != agent) nor the
control direction (needs event.pubkey != agent), and gets rejected
outright at publish, not just left unreadable.

Real NIP-OA owner-delegation (a human owner with their own key, distinct
from the agent) isn't built in Vantage yet, so this publishes using a
"shadow owner" -- a second keypair Vantage itself derives and holds for
the same agent (buzz_identity.derive_shadow_owner_keypair), satisfying the
relay's real two-party requirement without inventing a fake human. When a
real owner-key layer exists, only `owner_pubkey_hex` needs to point
elsewhere -- the frame format is unchanged.
"""
import json
import time
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, derive_shadow_owner_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_WS_URL
from .nip44 import encrypt as nip44_encrypt
from .nip44 import get_conversation_key

KIND_AGENT_OBSERVER_FRAME = 24200
OBSERVER_AGENT_TAG = "agent"
OBSERVER_FRAME_TAG = "frame"
OBSERVER_FRAME_TELEMETRY = "telemetry"


async def publish_observer_frame(
    agent_id: int, event_type: str, summary: str, severity: str = "info",
    owner_pubkey_hex: Optional[str] = None,
) -> dict:
    """Mirror one activity-feed entry as a live encrypted observer frame.
    `owner_pubkey_hex` defaults to this agent's shadow-owner pubkey (see
    module docstring) -- pass a real owner pubkey once that layer exists."""
    pk = await derive_buzz_keypair(agent_id)
    agent_pubkey_hex = public_key_xonly_hex(pk)

    if owner_pubkey_hex is None:
        shadow_owner_pk = await derive_shadow_owner_keypair(agent_id)
        owner_pubkey_hex = public_key_xonly_hex(shadow_owner_pk)
    conv_key = get_conversation_key(pk, owner_pubkey_hex)

    payload = {
        "event_type": event_type,
        "summary": summary,
        "severity": severity,
        "timestamp": int(time.time()),
    }
    content = nip44_encrypt(json.dumps(payload), conv_key)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            KIND_AGENT_OBSERVER_FRAME, content,
            tags=[
                ["p", owner_pubkey_hex],
                [OBSERVER_AGENT_TAG, agent_pubkey_hex],
                [OBSERVER_FRAME_TAG, OBSERVER_FRAME_TELEMETRY],
            ],
        )
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected observer frame: {result['ack']}")
    return {"ok": True, "owner_pubkey_hex": owner_pubkey_hex, "event": result["event"]}
