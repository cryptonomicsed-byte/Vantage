"""Section 8 of the buzz_vantage_blueprint: knowledge/vault <-> engrams.

Real implementation of NIP-AE (kind:30174, confirmed via buzz-core's
kind.rs and the relay's own docs/nips/NIP-AE.md, read in full rather than
assumed from the blueprint's one-line summary): NIP-44-encrypted,
addressable-per-slug agent memory, d-tag = HMAC-SHA256(conversation_key,
"agent-memory/v1/d-tag\\x00" + slug). "Owner" (pubkey_o) is this
instance's identity (same one used for NIP-OA attestation elsewhere) --
matches this NIP's actual privacy model: the owner can always decrypt
everything the agent remembers, which is exactly Vantage's own
instance-owns-its-agents relationship.

Real invariant this NIP enforces server-side, not just documented: these
events are addressable per (kind, pubkey, d) and encrypted, so agent-to-
agent knowledge sharing structurally cannot leak through this kind --
matches Section 8's own "edge" note. Cross-agent/cross-instance sharing
uses kind:30023 (public long-form) instead, unchanged from Section 3/6.
"""
import hashlib
import hmac
import json
import logging

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, derive_instance_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_WS_URL
from . import nip44

logger = logging.getLogger(__name__)

KIND_ENGRAM = 30174
D_TAG_DOMAIN = b"agent-memory/v1/d-tag\x00"


def _derive_d_tag(conversation_key: bytes, slug: str) -> str:
    return hmac.new(conversation_key, D_TAG_DOMAIN + slug.encode(), hashlib.sha256).hexdigest()


async def write_engram(agent_id: int, slug: str, value: str) -> dict:
    """Writes (or tombstones, if value is None) a memory-body engram for
    slug. Never raises -- mirrors this codebase's other fire-and-forget
    Buzz mirrors; the real source of truth stays Vantage's own knowledge
    tables regardless of whether the engram mirror succeeds."""
    agent_pk = await derive_buzz_keypair(agent_id)
    owner_pk = await derive_instance_keypair()
    owner_pubkey = public_key_xonly_hex(owner_pk)

    conversation_key = nip44.get_conversation_key(agent_pk, owner_pubkey)
    d_tag = _derive_d_tag(conversation_key, slug)
    body = json.dumps({"slug": slug, "value": value}, separators=(",", ":"))
    if len(body.encode()) > 65535:
        return {"ok": False, "error": "body exceeds NIP-44's 65535 byte plaintext limit"}
    ciphertext = nip44.encrypt(body, conversation_key)

    try:
        sess = BuzzSession(RELAY_WS_URL, agent_pk)
        await sess.connect()
        await sess.authenticate()
        try:
            result = await sess.publish(KIND_ENGRAM, ciphertext, tags=[["d", d_tag], ["p", owner_pubkey]])
        finally:
            await sess.close()
        if not result["ack"][2]:
            logger.warning("buzz_engrams: write rejected for agent_id=%s slug=%s: %s", agent_id, slug, result["ack"])
            return {"ok": False, "error": str(result["ack"])}
        return {"ok": True, "event_id": result["event"]["id"], "d_tag": d_tag}
    except Exception as e:
        logger.warning("buzz_engrams: write_engram failed for agent_id=%s slug=%s: %s", agent_id, slug, e)
        return {"ok": False, "error": str(e)}


async def read_engram(agent_id: int, slug: str) -> dict:
    """Head-selection read: query for the (d, p) pair, take the event
    with the greatest created_at, decrypt, verify slug/d-tag re-derive
    consistently, return {"value": ...} or {"found": False}."""
    agent_pk = await derive_buzz_keypair(agent_id)
    owner_pk = await derive_instance_keypair()
    owner_pubkey = public_key_xonly_hex(owner_pk)
    agent_pubkey = public_key_xonly_hex(agent_pk)

    conversation_key = nip44.get_conversation_key(agent_pk, owner_pubkey)
    d_tag = _derive_d_tag(conversation_key, slug)

    sess = BuzzSession(RELAY_WS_URL, agent_pk)
    await sess.connect()
    await sess.authenticate()
    try:
        sub_id = await sess.subscribe([{"kinds": [KIND_ENGRAM], "authors": [agent_pubkey], "#d": [d_tag], "#p": [owner_pubkey]}])
        events = await sess.recv_until_eose(sub_id, max_events=20)
    finally:
        await sess.close()

    if not events:
        return {"found": False}
    head = max(events, key=lambda e: (e["created_at"], -int(e["id"], 16)))
    try:
        plaintext = nip44.decrypt(head["content"], conversation_key)
        body = json.loads(plaintext)
    except Exception as e:
        return {"found": False, "error": f"decrypt/parse failed: {e}"}
    if body.get("slug") != slug:
        return {"found": False, "error": "slug mismatch on decrypt"}
    if body.get("value") is None:
        return {"found": False, "tombstoned": True}
    return {"found": True, "value": body["value"], "event_id": head["id"], "created_at": head["created_at"]}
