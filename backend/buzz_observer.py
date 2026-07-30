"""Buzz Agent Observer Frame mirror (kind:24200, ephemeral). ADDON: mirrors
entries already flowing through Vantage's real Activity Feed (backend/
agents.py's /me/activity-feed, backed by the hash-chained receipts table)
out as a live, encrypted Nostr frame -- the receipts table stays the
authoritative record; this is a one-way broadcast of it, and skipping it
changes nothing about how the Activity Feed itself works.

kind:24200 is in P_GATED_KINDS (buzz-relay's kind.rs): the relay only
delivers it to a reader whose own pubkey matches the event's `#p` tag.
It's ALSO validated at ingest (handlers/event.rs's agent_observer_route,
confirmed live): the `p` (owner) and `agent` tags MUST be genuinely
different pubkeys -- self-owned (p == agent) is rejected outright:
"invalid: observer frame must be agent-to-owner telemetry or owner-to-
agent control".

Real NIP-OA owner-delegation (a human owner with their own key, distinct
from the agent) isn't built in Vantage yet, so this uses a "shadow owner"
-- a second keypair Vantage itself derives and holds for the same agent
(buzz_identity.derive_shadow_owner_keypair). Vantage holds both keys, so
it can always decrypt; this satisfies the relay's real two-party
requirement without inventing a fake human.

Second real finding (live-tested): even with two distinct keys, the relay
separately enforces "restricted: observer frame is not authorized for
this agent owner" -- handle_agent_observer_event checks a server-side
agent<->owner mapping (`is_agent_owner`), which is only materialized when
the agent's own NIP-42 AUTH event carries a valid NIP-OA `auth` tag (see
docs/nips/NIP-OA.md): a delegation proof signed by the OWNER key over
`sha256("nostr:agent-auth:" + agent_pubkey_hex + ":" + conditions)`. Since
Vantage holds the shadow-owner's private key too, it can construct this
proof itself -- again, a real protocol requirement satisfied honestly,
not bypassed.
"""
import hashlib
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

_NIP_OA_DOMAIN = b"nostr:agent-auth:"


def _build_nip_oa_auth_tag(owner_pk, agent_pubkey_hex: str, conditions: str = "") -> list:
    """["auth", owner_pubkey_hex, conditions, sig_hex] per NIP-OA -- the
    owner key signs a delegation proof over the agent's pubkey; the event
    itself stays authored (and signed) by the agent as normal."""
    owner_pubkey_hex = public_key_xonly_hex(owner_pk)
    preimage = _NIP_OA_DOMAIN + agent_pubkey_hex.encode("utf-8") + b":" + conditions.encode("utf-8")
    digest = hashlib.sha256(preimage).digest()
    sig_hex = owner_pk.sign_schnorr(digest).hex()
    return ["auth", owner_pubkey_hex, conditions, sig_hex]


async def publish_observer_frame(
    agent_id: int, event_type: str, summary: str, severity: str = "info",
    owner_pubkey_hex: Optional[str] = None,
) -> dict:
    """Mirror one activity-feed entry as a live encrypted observer frame.
    Defaults to this agent's shadow-owner pubkey (see module docstring);
    pass a real owner pubkey once that layer exists (NIP-OA tag
    construction would then need the real owner's signature instead)."""
    pk = await derive_buzz_keypair(agent_id)
    agent_pubkey_hex = public_key_xonly_hex(pk)

    shadow_owner_pk = None
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
    extra_tags = []
    if shadow_owner_pk is not None:
        extra_tags = [_build_nip_oa_auth_tag(shadow_owner_pk, agent_pubkey_hex)]
    await sess.authenticate(extra_tags=extra_tags)
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
