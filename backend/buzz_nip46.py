"""NIP-46 (Nostr Connect / "bunker") remote signing.

Unlike NIP-AB device pairing (buzz_pairing.py), which exports the agent's
real nsec to the phone, this mode never lets the private key leave Vantage.
Vantage runs as the *remote signer*: it listens on the relay for kind:24133
request events addressed (via #p) to the agent's own real pubkey, decrypts
them (NIP-44), executes the requested signer operation locally using the
agent's real key (derive_buzz_keypair, same key material as everywhere
else in Buzz), and publishes an encrypted kind:24133 response back to the
requesting client's pubkey. A third-party Nostr client can then act "as"
the agent without ever holding its key -- this is the safer, no-export
alternative flagged (but not built) in buzz_pairing.py's module docstring.

Transport: kind:24133 falls in Nostr's ephemeral range (20000-29999), which
buzz-relay's handle_ephemeral_event() dispatches on BEFORE ingest.rs's
per-kind allowlist ever runs (see handlers/event.rs's `is_ephemeral` gate at
the top of the publish path) -- so no relay-side kind allowlisting was
required for this to transit at all. KIND_NOSTR_CONNECT / NIP 46 were still
added to buzz-core/kind.rs and nip11.rs's SUPPORTED_NIPS for documentation
and honest self-advertisement, not because the ephemeral path needed them.

Session state lives in-memory only (BUNKER_SESSIONS), same rationale as
PAIRING_SESSIONS: single uvicorn process, no --workers, session should not
outlive a restart anyway.

Spec: https://github.com/nostr-protocol/nips/blob/master/46.md
"""
import asyncio
import json
import logging
import secrets
import socket
import time
import urllib.parse
import uuid

import websockets
from coincurve import PrivateKey

from .buzz_client import build_event
from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex, sign_event_id
from .buzz_pairing import INTERNAL_RELAY_HOST, INTERNAL_RELAY_PORT, PUBLIC_RELAY_WS_URL, PUBLIC_TENANT_HOST
from .nip44 import decrypt as nip44_decrypt
from .nip44 import encrypt as nip44_encrypt
from .nip44 import get_conversation_key

logger = logging.getLogger(__name__)

KIND_NOSTR_CONNECT = 24133

# sign_event requests require an explicit host-side approval (like NIP-AB's
# SAS confirm step) before Vantage will actually sign anything with the
# agent's real key -- a connected client cannot silently mint events.
APPROVAL_TIMEOUT = 60

BUNKER_SESSIONS: dict[int, dict] = {}


async def start_bunker(agent_id: int) -> dict:
    """(Re)starts the remote-signer listener for this agent and returns a
    fresh bunker:// connection string. Restarting invalidates the previous
    secret (old clients must reconnect with the new one)."""
    existing = BUNKER_SESSIONS.get(agent_id)
    if existing and existing.get("task") and not existing["task"].done():
        existing["task"].cancel()

    agent_priv = await derive_buzz_keypair(agent_id)
    agent_pubkey_hex = public_key_xonly_hex(agent_priv)
    secret = secrets.token_hex(16)

    bunker_uri = (
        f"bunker://{agent_pubkey_hex}"
        f"?relay={urllib.parse.quote(PUBLIC_RELAY_WS_URL, safe='')}"
        f"&secret={secret}"
    )

    session = {
        "agent_id": agent_id,
        "agent_priv": agent_priv,
        "agent_pubkey_hex": agent_pubkey_hex,
        "secret": secret,
        "bunker_uri": bunker_uri,
        "state": "listening",
        "connected_clients": set(),
        "pending_approvals": {},   # req_id -> {"event": asyncio.Event, "decision": None, "summary": str}
        "seen_event_ids": set(),
        "error": None,
        "started_at": time.time(),
    }
    BUNKER_SESSIONS[agent_id] = session
    session["task"] = asyncio.create_task(_signer_flow(session))
    return {"bunker_uri": bunker_uri, "pubkey": agent_pubkey_hex}


def get_bunker_status(agent_id: int) -> dict:
    s = BUNKER_SESSIONS.get(agent_id)
    if not s:
        return {"state": "not_started"}
    return {
        "state": s["state"],
        "pubkey": s["agent_pubkey_hex"],
        "connected_clients": list(s["connected_clients"]),
        "pending_approvals": [
            {"req_id": rid, "summary": p["summary"]}
            for rid, p in s["pending_approvals"].items()
            if p["decision"] is None
        ],
        "error": s["error"],
    }


def stop_bunker(agent_id: int) -> dict:
    s = BUNKER_SESSIONS.get(agent_id)
    if not s:
        raise ValueError("no bunker session for this agent")
    if s.get("task"):
        s["task"].cancel()
    s["state"] = "stopped"
    return {"ok": True}


def decide_approval(agent_id: int, req_id: str, approve: bool) -> dict:
    s = BUNKER_SESSIONS.get(agent_id)
    if not s:
        raise ValueError("no bunker session for this agent")
    pending = s["pending_approvals"].get(req_id)
    if not pending or pending["decision"] is not None:
        raise ValueError("no pending approval with that id")
    pending["decision"] = "approve" if approve else "deny"
    pending["event"].set()
    return {"ok": True}


async def _signer_flow(session: dict):
    agent_id = session["agent_id"]
    try:
        raw_sock = socket.create_connection((INTERNAL_RELAY_HOST, INTERNAL_RELAY_PORT), timeout=10)
        async with websockets.connect(
            f"ws://{PUBLIC_TENANT_HOST}/", sock=raw_sock, open_timeout=10,
        ) as ws:
            # Real bug found live: unlike NIP-AB pairing's ephemeral,
            # short-lived kind:24134 session, this listener is long-lived
            # and the relay proactively sends ["AUTH", challenge] then
            # drops the connection (close 1005) if it's never answered.
            # Perform real NIP-42 auth before subscribing.
            first = json.loads(await ws.recv())
            if first[0] == "AUTH":
                challenge = first[1]
                # This connection goes through the raw internal socket, not
                # the public Traefik/TLS entrypoint -- no X-Forwarded-Proto
                # header reaches the relay, so its own scheme-guess for the
                # NIP-42 relay-tag match resolves to plain ws://, not
                # wss://. Must match that, not PUBLIC_RELAY_WS_URL.
                auth_event = build_event(
                    session["agent_priv"], kind=22242, content="",
                    tags=[["relay", f"ws://{PUBLIC_TENANT_HOST}"], ["challenge", challenge]],
                )
                await ws.send(json.dumps(["AUTH", auth_event]))
                ack = json.loads(await ws.recv())
                if not (ack[0] == "OK" and ack[2]):
                    raise RuntimeError(f"NIP-42 auth failed for bunker signer: {ack}")

            sub_id = secrets.token_hex(8)
            await ws.send(json.dumps(
                ["REQ", sub_id, {"kinds": [KIND_NOSTR_CONNECT], "#p": [session["agent_pubkey_hex"]]}]
            ))

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg[0] != "EVENT" or msg[1] != sub_id:
                    continue
                ev = msg[2]
                if ev["id"] in session["seen_event_ids"]:
                    continue
                session["seen_event_ids"].add(ev["id"])
                if ev["pubkey"] == session["agent_pubkey_hex"]:
                    continue  # ignore our own echoed responses

                asyncio.create_task(_handle_request(ws, session, ev))
    except asyncio.CancelledError:
        session["state"] = "stopped"
        raise
    except Exception as e:
        logger.exception("NIP-46 bunker listener for agent %s failed", agent_id)
        session["error"] = str(e)
        session["state"] = "error"


async def _handle_request(ws, session: dict, ev: dict):
    client_pub = ev["pubkey"]
    try:
        conv_key = get_conversation_key(session["agent_priv"], client_pub)
        plaintext = nip44_decrypt(ev["content"], conv_key)
        req = json.loads(plaintext)
    except Exception:
        return  # not a well-formed request for us -- ignore, don't error

    req_id = req.get("id", uuid.uuid4().hex)
    method = req.get("method")
    params = req.get("params", [])

    try:
        if method == "connect":
            offered_secret = params[1] if len(params) > 1 else None
            if session["secret"] and offered_secret != session["secret"]:
                result, error = None, "invalid secret"
            else:
                session["connected_clients"].add(client_pub)
                result, error = "ack", None
        elif method == "ping":
            result, error = "pong", None
        elif method == "get_public_key":
            result, error = session["agent_pubkey_hex"], None
        elif method == "nip44_encrypt":
            target_pub, plaintext_in = params[0], params[1]
            target_conv_key = get_conversation_key(session["agent_priv"], target_pub)
            result, error = nip44_encrypt(plaintext_in, target_conv_key), None
        elif method == "nip44_decrypt":
            target_pub, ciphertext_in = params[0], params[1]
            target_conv_key = get_conversation_key(session["agent_priv"], target_pub)
            result, error = nip44_decrypt(ciphertext_in, target_conv_key), None
        elif method == "sign_event":
            unsigned = json.loads(params[0])
            approved = await _await_approval(session, req_id, client_pub, unsigned)
            if not approved:
                result, error = None, "user rejected sign_event request"
            else:
                signed = _sign_unsigned_event(session["agent_priv"], session["agent_pubkey_hex"], unsigned)
                result, error = json.dumps(signed), None
        else:
            result, error = None, f"unsupported method: {method}"
    except Exception as e:
        result, error = None, f"internal error: {e}"

    body = {"id": req_id, "result": result}
    if error:
        body["error"] = error
    conv_key = get_conversation_key(session["agent_priv"], client_pub)
    content = nip44_encrypt(json.dumps(body), conv_key)
    response = build_event(session["agent_priv"], KIND_NOSTR_CONNECT, content, tags=[["p", client_pub]])
    await ws.send(json.dumps(["EVENT", response]))
    session["seen_event_ids"].add(response["id"])


async def _await_approval(session: dict, req_id: str, client_pub: str, unsigned: dict) -> bool:
    summary = f"kind:{unsigned.get('kind')} from client {client_pub[:12]}...: {str(unsigned.get('content', ''))[:80]}"
    pending = {"event": asyncio.Event(), "decision": None, "summary": summary}
    session["pending_approvals"][req_id] = pending
    try:
        await asyncio.wait_for(pending["event"].wait(), timeout=APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        return False
    return pending["decision"] == "approve"


def _sign_unsigned_event(agent_priv: PrivateKey, agent_pubkey_hex: str, unsigned: dict) -> dict:
    from .buzz_client import _event_id
    tags = unsigned.get("tags", [])
    content = unsigned.get("content", "")
    kind = unsigned["kind"]
    created_at = unsigned.get("created_at") or int(time.time())
    eid = _event_id(agent_pubkey_hex, created_at, kind, tags, content)
    sig_hex = sign_event_id(agent_priv, eid)
    return {
        "id": eid,
        "pubkey": agent_pubkey_hex,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig_hex,
    }
