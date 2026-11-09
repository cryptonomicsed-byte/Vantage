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
import os
import sys

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, derive_instance_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_WS_URL
from . import nip44

logger = logging.getLogger(__name__)

KIND_ENGRAM = 30174
D_TAG_DOMAIN = b"agent-memory/v1/d-tag\x00"


def _derive_d_tag(conversation_key: bytes, slug: str) -> str:
    return hmac.new(conversation_key, D_TAG_DOMAIN + slug.encode(), hashlib.sha256).hexdigest()


# ── P3-09: engrams via minipae ───────────────────────────────────────────────
# minipae is the ecosystem's reference NIP-AE implementation; Vantage kept its
# own copy here. With BUZZ_ENGRAMS_VIA_MINIPAE=1 the engram (d-tag, NIP-44
# body, id, BIP-340 sig) is built by minipae and published over the same
# relay session with the same keys; reads decrypt with minipae. The two are
# wire-identical (same d-tag domain + 0x00, same {"slug","value"} body), so
# engrams written either way read back either way. BUZZ_ENGRAMS_LEGACY=1
# forces this module's own path; any minipae failure falls back to it.
_MINIPAE = None


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


def use_minipae() -> bool:
    return _flag("BUZZ_ENGRAMS_VIA_MINIPAE") and not _flag("BUZZ_ENGRAMS_LEGACY")


def _minipae():
    """The minipae module, or None when it isn't importable here."""
    global _MINIPAE
    if _MINIPAE is None:
        repo = os.environ.get("VANTAGE_MINIPAE_REPO", "/opt/ares/minipae")
        if repo and repo not in sys.path and os.path.isdir(repo):
            sys.path.append(repo)
        try:
            import minipae as _m  # noqa: PLC0415
            _MINIPAE = _m
        except Exception:  # noqa: BLE001
            _MINIPAE = False
    return _MINIPAE or None


async def _write_via_minipae(agent_pk, owner_pubkey: str, slug: str, value) -> dict:
    mp = _minipae()
    if mp is None:
        raise RuntimeError("minipae not importable")
    ev = mp.build_event(slug, {"slug": slug, "value": value}, agent_pk.secret, bytes.fromhex(owner_pubkey))
    sess = BuzzSession(RELAY_WS_URL, agent_pk)
    await sess.connect()
    await sess.authenticate()
    try:
        await sess.ws.send(json.dumps(["EVENT", ev]))
        ack = await sess._recv_json()
    finally:
        await sess.close()
    if not (isinstance(ack, list) and len(ack) >= 3 and ack[2]):
        return {"ok": False, "error": str(ack), "via": "minipae"}
    d = next(t[1] for t in ev["tags"] if t[0] == "d")
    return {"ok": True, "event_id": ev["id"], "d_tag": d, "via": "minipae"}


async def write_engram(agent_id: int, slug: str, value: str) -> dict:
    """Writes (or tombstones, if value is None) a memory-body engram for
    slug. Never raises -- mirrors this codebase's other fire-and-forget
    Buzz mirrors; the real source of truth stays Vantage's own knowledge
    tables regardless of whether the engram mirror succeeds."""
    agent_pk = await derive_buzz_keypair(agent_id)
    owner_pk = await derive_instance_keypair()
    owner_pubkey = public_key_xonly_hex(owner_pk)

    body = json.dumps({"slug": slug, "value": value}, separators=(",", ":"))
    if use_minipae() and len(body.encode()) <= 65535:
        try:
            return await _write_via_minipae(agent_pk, owner_pubkey, slug, value)
        except Exception as e:  # noqa: BLE001 -- fall back to this module's own path
            logger.warning("buzz_engrams: minipae path failed, using legacy: %s", e)

    conversation_key = nip44.get_conversation_key(agent_pk, owner_pubkey)
    d_tag = _derive_d_tag(conversation_key, slug)
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
        mp = _minipae() if use_minipae() else None
        if mp is not None:
            try:
                plaintext = mp.nip44_decrypt(head["content"], conversation_key)
            except Exception:  # noqa: BLE001 -- legacy decrypt below
                plaintext = nip44.decrypt(head["content"], conversation_key)
        else:
            plaintext = nip44.decrypt(head["content"], conversation_key)
        body = json.loads(plaintext)
    except Exception as e:
        return {"found": False, "error": f"decrypt/parse failed: {e}"}
    if body.get("slug") != slug:
        return {"found": False, "error": "slug mismatch on decrypt"}
    if body.get("value") is None:
        return {"found": False, "tombstoned": True}
    return {"found": True, "value": body["value"], "event_id": head["id"], "created_at": head["created_at"]}
