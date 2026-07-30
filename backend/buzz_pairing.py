"""NIP-AB device pairing (source role only) -- lets an agent's owner link
the agent's real Buzz/Nostr identity to the official Buzz mobile app via
QR code, per crates/buzz-core/src/pairing/NIP-AB.md in the buzz-relay repo.

buzz-core's PairingSession state machine is a Rust library type (buzz-core
has no FFI/service boundary Vantage can call into), so this is a from-spec
Python reimplementation of the *source* role only (Vantage is always the
device that already holds the identity; the phone is always target). Wire
protocol (event shapes, HKDF derivations, NIP-44 encryption) is followed
byte-for-byte -- validated locally against the official nip44.vectors.json
and against NIP-AB.md's own session_id/sas_input/transcript_hash test
vectors before being wired in here (see tests/test_nip44_vectors.py).

Session state lives in-memory only (PAIRING_SESSIONS dict) -- Vantage runs
as a single uvicorn process (no --workers), so this is safe without a
shared store, and matches the protocol's own guidance that session state
need not persist beyond the 120s session lifetime.

SECURITY NOTE (flagged, not silently decided): this transfers the agent's
actual private key (payload_type "nsec") to the phone. Once transferred,
Vantage's usual sealed-seed/encrypted-at-rest model no longer covers that
copy -- the key now also lives wherever the phone's Buzz app stores it,
with no revocation mechanism (per NIP-AB's own "Limitations" section). This
is what was asked for; the "bunker" (NIP-46 remote signer) payload type
would avoid the export entirely but requires a live bunker/signer service
Vantage doesn't have yet -- noted as a lower-risk follow-up, not built here.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import time
import urllib.parse

import websockets
from coincurve import PrivateKey

from .bech32 import bech32_encode
from .buzz_client import _event_id, build_event
from .buzz_identity import derive_buzz_keypair
from .nip44 import decrypt as nip44_decrypt
from .nip44 import encrypt as nip44_encrypt
from .nip44 import get_conversation_key

logger = logging.getLogger(__name__)

KIND_PAIRING = 24134

# The backend's own connection to the relay stays internal (same as every
# other Buzz integration in this codebase); the QR code must point phones
# at a URL actually reachable from the public internet.
#
# Getting here took three separate real bugs, each only visible once the
# previous one was fixed:
#
# 1. Tenant resolution (crates/buzz-relay/src/tenant.rs, bind_community)
#    binds purely off the inbound Host header against a `communities` row
#    with a UNIQUE index on host -- no alias mechanism. A phone dialing the
#    public domain directly got "no community configured for this host".
#    Fixed by seeding a SECOND communities row for the exact public
#    host:port and routing it through a DEDICATED Traefik entrypoint (see
#    below) rather than rewriting the Host header to fake the internal one.
#
# 2. NIP-42 requires the client's signed AUTH event's `relay` tag to equal
#    {scheme}://{tenant.host()} after normalization -- INCLUDING path. A
#    path-prefixed route (an earlier /buzz-relay attempt) can never satisfy
#    this: a real client's dial URL has no extra path to add. Fixed with a
#    dedicated entrypoint (`buzzrelay`, port 3443, see
#    /docker/traefik/docker-compose.yml) on the SAME hostname with NO path,
#    plus a relay-side patch (ConnectionState::forwarded_tls, populated from
#    the request's X-Forwarded-Proto header) so the relay's own "ws" vs
#    "wss" scheme guess matches what a TLS-terminating proxy actually saw --
#    the relay itself only ever speaks plain ws:// and has no TLS context
#    of its own to derive "wss" from otherwise.
#
# 3. UFW (host firewall) was default-deny on incoming with an explicit
#    allowlist -- port 3443 was never in it. Every test run over SSH *on*
#    the VPS looked fine because that traffic never crosses the external
#    network boundary; a real external client got nothing at all. Fixed
#    with `ufw allow 3443/tcp`.
#
# Verified live end-to-end after all three: a genuinely external client
# (tested from a machine outside the VPS, not over SSH) gets a real TLS
# handshake + WS upgrade, a real NIP-42 AUTH challenge/response with a
# FRESH, NEVER-REGISTERED ephemeral key succeeds (OK:true), and a full
# NIP-AB pairing session completes over this exact URL.
#
# 4. Fourth bug, found by actually running source+target together: the
#    relay's tenant binding is per-connection, keyed off the raw Host
#    header (crates/buzz-relay/src/tenant.rs, bind_community) -- and
#    ephemeral kind:24134 event fan-out is tenant-scoped. The SOURCE's own
#    internal connection (this module, dialing ws://localhost:3000
#    directly) was binding to the ORIGINAL "localhost:3000" community,
#    while the TARGET (a real external client, or our own simulator)
#    binds to the NEW "omokoda.duckdns.org:3443" community seeded for the
#    public entrypoint -- two different tenants, so the target's offer
#    event could never reach the source's subscription no matter how long
#    it waited.
#
#    First fix attempt (websockets.connect's additional_headers={"Host":
#    ...}) looked right in isolation but was WRONG: `websockets` still
#    auto-generates its own Host header from the connection URI and sends
#    it FIRST, then appends the override as a SECOND, duplicate Host
#    header -- confirmed by capturing the raw request with a raw `nc`
#    listener. The relay (like most HTTP implementations) uses the first
#    Host header it sees, so the override was silently ignored the whole
#    time; a dedicated two-connection tenant-isolation test proved the
#    fan-out still failed even with the "fix" in place.
#
#    Real fix: connect via a manually-created, already-connected TCP
#    socket (`sock=`) while passing the DESIRED public URL as the
#    connection URI. `websockets` then derives its Host header purely
#    from the URI (as designed for exactly this reverse-tunnel pattern),
#    with no auto-generated duplicate, while the actual TCP traffic still
#    goes to the real internal address. Verified with the same raw `nc`
#    capture: exactly one Host header, correct value. Source never sends
#    a real NIP-42 AUTH event (exempted via the kind:24134-only bypass),
#    so there's no relay-url matching concern for this side -- only
#    tenant binding needs to agree.
INTERNAL_RELAY_HOST = "localhost"
INTERNAL_RELAY_PORT = 3000
PUBLIC_RELAY_WS_URL = "wss://omokoda.duckdns.org:3443"
PUBLIC_TENANT_HOST = "omokoda.duckdns.org:3443"

SESSION_TIMEOUT = 120
STEP_TIMEOUT = 30

PAIRING_SESSIONS: dict[str, dict] = {}


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def _hkdf32(salt: bytes, ikm: bytes, info: bytes) -> bytes:
    return _hkdf_expand(_hkdf_extract(salt, ikm), info, 32)


def _derive_session_id(session_secret: bytes) -> bytes:
    return _hkdf32(b"", session_secret, b"nostr-pair-session-id")


def _derive_sas(ecdh_shared: bytes, session_secret: bytes) -> tuple[str, bytes]:
    sas_input = _hkdf32(session_secret, ecdh_shared, b"nostr-pair-sas-v1")
    code = int.from_bytes(sas_input[0:4], "big") % 1_000_000
    return f"{code:06d}", sas_input


def _derive_transcript_hash(session_id: bytes, source_pub: bytes, target_pub: bytes,
                             sas_input: bytes, session_secret: bytes) -> bytes:
    transcript = session_id + source_pub + target_pub + sas_input
    return _hkdf32(session_secret, transcript, b"nostr-pair-transcript-v1")


def _ecdh_x_only(privkey: PrivateKey, peer_pubkey_xonly_hex: str) -> bytes:
    from coincurve import PublicKey as CoincurvePublicKey
    full_pub = CoincurvePublicKey(b"\x02" + bytes.fromhex(peer_pubkey_xonly_hex))
    shared_point = full_pub.multiply(privkey.secret, update=False)
    return shared_point.format(compressed=True)[1:]


async def start_pairing(agent_id: int) -> dict:
    """Generates the ephemeral keypair + session secret, publishes the QR
    URI, and spawns the background task that waits for the phone's offer."""
    ephemeral_priv = PrivateKey()
    ephemeral_pub_hex = ephemeral_priv.public_key.format(compressed=True)[1:].hex()
    session_secret = secrets.token_bytes(32)
    session_id = _derive_session_id(session_secret)
    token = secrets.token_hex(16)

    qr_uri = (
        f"nostrpair://{ephemeral_pub_hex}"
        f"?secret={session_secret.hex()}"
        f"&relay={urllib.parse.quote(PUBLIC_RELAY_WS_URL, safe='')}"
        f"&v=1"
    )
    if len(qr_uri) > 2048:
        raise ValueError("QR URI exceeds 2048 chars")

    session = {
        "agent_id": agent_id,
        "token": token,
        "ephemeral_priv": ephemeral_priv,
        "ephemeral_pub_hex": ephemeral_pub_hex,
        "session_secret": session_secret,
        "session_id": session_id,
        "qr_uri": qr_uri,
        "state": "waiting_for_offer",
        "target_pub_hex": None,
        "sas_code": None,
        "sas_input": None,
        "created_at": time.time(),
        "seen_event_ids": set(),
        "error": None,
        "confirm_event": asyncio.Event(),
        "deny_reason": None,
    }
    PAIRING_SESSIONS[token] = session
    session["task"] = asyncio.create_task(_source_flow(session))
    return {"token": token, "qr_uri": qr_uri, "expires_in": SESSION_TIMEOUT}


def get_pairing_status(token: str) -> dict:
    s = PAIRING_SESSIONS.get(token)
    if not s:
        return {"state": "not_found"}
    return {
        "state": s["state"],
        "sas_code": s["sas_code"],
        "error": s["error"],
    }


def confirm_pairing(token: str) -> dict:
    s = PAIRING_SESSIONS.get(token)
    if not s:
        raise ValueError("session not found")
    if s["state"] != "sas_ready":
        raise ValueError(f"cannot confirm in state {s['state']!r}")
    s["state"] = "confirmed"
    s["confirm_event"].set()
    return {"ok": True}


def deny_pairing(token: str) -> dict:
    s = PAIRING_SESSIONS.get(token)
    if not s:
        raise ValueError("session not found")
    s["deny_reason"] = "user_denied"
    s["confirm_event"].set()
    return {"ok": True}


async def _source_flow(session: dict):
    token = session["token"]
    try:
        raw_sock = socket.create_connection((INTERNAL_RELAY_HOST, INTERNAL_RELAY_PORT), timeout=10)
        async with websockets.connect(
            f"ws://{PUBLIC_TENANT_HOST}/", sock=raw_sock, open_timeout=10,
        ) as ws:
            sub_id = secrets.token_hex(8)
            await ws.send(json.dumps(
                ["REQ", sub_id, {"kinds": [KIND_PAIRING], "#p": [session["ephemeral_pub_hex"]]}]
            ))

            deadline = time.time() + SESSION_TIMEOUT
            offer_accepted = False
            while not offer_accepted:
                remaining = deadline - time.time()
                if remaining <= 0:
                    session["state"] = "timeout"
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    session["state"] = "timeout"
                    return
                msg = json.loads(raw)
                if msg[0] != "EVENT" or msg[1] != sub_id:
                    continue
                ev = msg[2]
                if ev["id"] in session["seen_event_ids"]:
                    continue
                session["seen_event_ids"].add(ev["id"])

                p_tags = [t[1] for t in ev.get("tags", []) if t[0] == "p"]
                if session["ephemeral_pub_hex"] not in p_tags:
                    continue

                try:
                    conv_key = get_conversation_key(session["ephemeral_priv"], ev["pubkey"])
                    plaintext = nip44_decrypt(ev["content"], conv_key)
                    body = json.loads(plaintext)
                except Exception:
                    continue

                if body.get("type") != "offer" or body.get("version") != 1:
                    continue
                expected_session_id = session["session_id"].hex()
                candidate_session_id = body.get("session_id", "")
                if not hmac.compare_digest(expected_session_id, candidate_session_id):
                    continue

                session["target_pub_hex"] = ev["pubkey"]
                offer_accepted = True

            ecdh_shared = _ecdh_x_only(session["ephemeral_priv"], session["target_pub_hex"])
            sas_code, sas_input = _derive_sas(ecdh_shared, session["session_secret"])
            session["sas_input"] = sas_input
            session["sas_code"] = sas_code
            session["state"] = "sas_ready"

            try:
                await asyncio.wait_for(session["confirm_event"].wait(), timeout=STEP_TIMEOUT)
            except asyncio.TimeoutError:
                session["state"] = "timeout"
                await _publish_abort(ws, session, "timeout")
                return

            if session["deny_reason"]:
                session["state"] = "denied"
                await _publish_abort(ws, session, session["deny_reason"])
                return

            transcript_hash = _derive_transcript_hash(
                session["session_id"],
                bytes.fromhex(session["ephemeral_pub_hex"]),
                bytes.fromhex(session["target_pub_hex"]),
                sas_input,
                session["session_secret"],
            )
            target_conv_key = get_conversation_key(session["ephemeral_priv"], session["target_pub_hex"])

            sas_confirm_content = nip44_encrypt(
                json.dumps({"type": "sas-confirm", "transcript_hash": transcript_hash.hex()}),
                target_conv_key,
            )
            await _publish(ws, session, sas_confirm_content)

            agent_keypair = await derive_buzz_keypair(session["agent_id"])
            nsec = bech32_encode("nsec", agent_keypair.secret)
            payload_content = nip44_encrypt(
                json.dumps({"type": "payload", "payload_type": "nsec", "payload": nsec}),
                target_conv_key,
            )
            await _publish(ws, session, payload_content)
            session["state"] = "payload_sent"

            deadline = time.time() + STEP_TIMEOUT
            while time.time() < deadline:
                remaining = deadline - time.time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg[0] != "EVENT" or msg[1] != sub_id:
                    continue
                ev = msg[2]
                if ev["id"] in session["seen_event_ids"]:
                    continue
                session["seen_event_ids"].add(ev["id"])
                if ev["pubkey"] != session["target_pub_hex"]:
                    continue
                try:
                    plaintext = nip44_decrypt(ev["content"], target_conv_key)
                    body = json.loads(plaintext)
                except Exception:
                    continue
                if body.get("type") == "complete":
                    session["state"] = "completed" if body.get("success", True) else "completed_with_error"
                    break

            if session["state"] == "payload_sent":
                session["state"] = "sent_unconfirmed"

    except Exception as e:
        logger.exception("NIP-AB pairing session %s failed", token)
        session["error"] = str(e)
        session["state"] = "error"
    finally:
        session["ephemeral_priv"] = None
        session["session_secret"] = None


async def _publish(ws, session: dict, content: str):
    event = build_event(session["ephemeral_priv"], KIND_PAIRING, content,
                         tags=[["p", session["target_pub_hex"]]])
    await ws.send(json.dumps(["EVENT", event]))


async def _publish_abort(ws, session: dict, reason: str):
    if not session.get("target_pub_hex"):
        return
    try:
        conv_key = get_conversation_key(session["ephemeral_priv"], session["target_pub_hex"])
        content = nip44_encrypt(json.dumps({"type": "abort", "reason": reason}), conv_key)
        await _publish(ws, session, content)
    except Exception:
        pass
