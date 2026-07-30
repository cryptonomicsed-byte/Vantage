"""Buzz Agent Engram mirror (NIP-AE, kind:30174). ADDON: Vantage's own
memory_vault (backend/routers/memory_vault.py -- real markdown notes,
FTS search, per-agent filesystem vault) stays the single authoritative
memory store. This module optionally MIRRORS a selected note out as an
encrypted, HMAC-addressed kind:30174 event so external Buzz clients can
see it -- it never becomes a second memory system. Skip calling this
entirely and nothing about the vault changes.

Per NIP-AE, engram addressing needs an (agent, owner) key pair: d-tag is
HMAC-SHA256 over the NIP-44 conversation key between them. Vantage has no
separate human-owner identity/key layer yet (that's the still-unbuilt
dual-layer-auth plan), so this mirrors in SELF-OWNED mode: pubkey_o =
pubkey_a, i.e. the agent is its own owner for addressing purposes. This is
a legitimate degenerate case of the spec (owner and agent may be the same
identity), not a workaround -- when a real owner identity exists later,
add an owner_pubkey parameter and this module's job is unchanged, only
which conversation key gets used.
"""
import hashlib
import hmac
import json
import re
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from .nip44 import decrypt as nip44_decrypt
from .nip44 import encrypt as nip44_encrypt
from .nip44 import get_conversation_key
from .buzz_registration import RELAY_WS_URL

KIND_AGENT_ENGRAM = 30174

_SLUG_RE = re.compile(r"^(core|mem/[a-z0-9][a-z0-9_-]{0,63}(/[a-z0-9][a-z0-9_-]{0,63})*)$")
_D_TAG_DOMAIN = b"agent-memory/v1/d-tag"


def _derive_d_tag(conversation_key: bytes, slug: str) -> str:
    msg = _D_TAG_DOMAIN + b"\x00" + slug.encode("utf-8")
    return hmac.new(conversation_key, msg, hashlib.sha256).hexdigest()


def note_slug(note_id: str) -> str:
    """Vault note ids look like 'note_a1b2c3d4' -- already matches the
    mem/... slug grammar's character class, just needs the mem/ prefix."""
    slug = f"mem/vault/{note_id}"
    if not _SLUG_RE.match(slug):
        raise ValueError(f"note_id {note_id!r} does not produce a valid NIP-AE slug")
    return slug


async def mirror_note_to_engram(agent_id: int, note_id: str, title: str, body: str, tags: list) -> dict:
    """Publish (or update -- same d-tag replaces) a memory body mirroring
    one vault note. Self-owned mode: conversation key is ECDH(agent, agent)."""
    pk = await derive_buzz_keypair(agent_id)
    pubkey_hex = public_key_xonly_hex(pk)
    conv_key = get_conversation_key(pk, pubkey_hex)  # self-owned: ECDH(agent, agent)

    slug = note_slug(note_id)
    d_tag = _derive_d_tag(conv_key, slug)

    memory_body = {
        "slug": slug,
        "value": json.dumps({"title": title, "body": body, "tags": tags}),
    }
    content = nip44_encrypt(json.dumps(memory_body), conv_key)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            KIND_AGENT_ENGRAM, content, tags=[["d", d_tag], ["p", pubkey_hex]],
        )
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected event: {result['ack']}")
    return {"ok": True, "note_id": note_id, "d_tag": d_tag, "event": result["event"]}


async def tombstone_engram(agent_id: int, note_id: str) -> dict:
    """Publish a tombstone (value: null) for a previously-mirrored note --
    per NIP-AE, the event is still published, readers just treat the slug
    as absent. Used when a vault note mirror should stop being visible."""
    pk = await derive_buzz_keypair(agent_id)
    pubkey_hex = public_key_xonly_hex(pk)
    conv_key = get_conversation_key(pk, pubkey_hex)

    slug = note_slug(note_id)
    d_tag = _derive_d_tag(conv_key, slug)
    memory_body = {"slug": slug, "value": None}
    content = nip44_encrypt(json.dumps(memory_body), conv_key)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            KIND_AGENT_ENGRAM, content, tags=[["d", d_tag], ["p", pubkey_hex]],
        )
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected event: {result['ack']}")
    return {"ok": True, "note_id": note_id, "tombstoned": True}


async def get_mirrored_engram(agent_id: int, note_id: str) -> Optional[dict]:
    pk = await derive_buzz_keypair(agent_id)
    pubkey_hex = public_key_xonly_hex(pk)
    conv_key = get_conversation_key(pk, pubkey_hex)
    slug = note_slug(note_id)
    d_tag = _derive_d_tag(conv_key, slug)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        # req.rs's engram_filters_authorized requires authors=[self] or
        # #p=[self] on any filter that can match kind:30174 -- confirmed
        # live (a filter with neither got CLOSED, not silently ignored).
        sub_id = await sess.subscribe([
            {"kinds": [KIND_AGENT_ENGRAM], "#d": [d_tag], "authors": [pubkey_hex]}
        ])
        events = await sess.recv_until_eose(sub_id, max_events=5)
    finally:
        await sess.close()
    if not events:
        return None
    latest = max(events, key=lambda e: e["created_at"])
    try:
        plaintext = nip44_decrypt(latest["content"], conv_key)
        memory_body = json.loads(plaintext)
    except Exception:
        return None
    if memory_body.get("value") is None:
        return {"note_id": note_id, "tombstoned": True}
    return {"note_id": note_id, "value": json.loads(memory_body["value"]), "created_at": latest["created_at"]}
