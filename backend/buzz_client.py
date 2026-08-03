"""Minimal Nostr client for Buzz: NIP-01 events + NIP-42 auth, enough to
connect, authenticate, and publish/subscribe. Stdlib json + websockets +
coincurve only -- no pynostr dependency."""
import hashlib
import json
import time
import uuid
from typing import Optional

import websockets
from coincurve import PrivateKey

from .buzz_identity import public_key_xonly_hex, sign_event_id


def _event_id(pubkey_hex: str, created_at: int, kind: int, tags: list, content: str) -> str:
    # NIP-01 serialization: [0, pubkey, created_at, kind, tags, content]
    ser = json.dumps(
        [0, pubkey_hex, created_at, kind, tags, content],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(ser.encode("utf-8")).hexdigest()


def build_event(pk: PrivateKey, kind: int, content: str, tags: Optional[list] = None) -> dict:
    pubkey_hex = public_key_xonly_hex(pk)
    tags = tags or []
    created_at = int(time.time())
    eid = _event_id(pubkey_hex, created_at, kind, tags, content)
    sig_hex = sign_event_id(pk, eid)
    return {
        "id": eid,
        "pubkey": pubkey_hex,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig_hex,
    }


class BuzzSession:
    def __init__(self, relay_ws_url: str, pk: PrivateKey):
        self.relay_ws_url = relay_ws_url
        self.pk = pk
        self.ws = None
        self.authed = False

    async def connect(self):
        self.ws = await websockets.connect(self.relay_ws_url, open_timeout=10)

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def _recv_json(self, timeout=10):
        raw = await self.ws.recv()
        return json.loads(raw)

    async def authenticate(self, extra_tags: Optional[list] = None):
        """NIP-42: relay proactively sends ["AUTH", <challenge>], client
        replies ["AUTH", <kind-22242-event-tagged-with-relay+challenge>].

        `extra_tags` (e.g. a NIP-OA `["auth", owner_pubkey, conditions,
        sig]` delegation tag) are appended after the standard relay+
        challenge tags -- additive, default empty, existing callers
        unaffected. Used by buzz_observer.py to materialize a shadow-owner
        mapping server-side on first auth."""
        msg = await self._recv_json()
        if msg[0] != "AUTH":
            raise RuntimeError(f"expected proactive AUTH challenge, got: {msg}")
        challenge = msg[1]
        auth_event = build_event(
            self.pk,
            kind=22242,
            content="",
            tags=[["relay", self.relay_ws_url], ["challenge", challenge]] + (extra_tags or []),
        )
        await self.ws.send(json.dumps(["AUTH", auth_event]))
        ack = await self._recv_json()
        # Relay replies ["OK", <event_id>, true/false, <message>]
        if ack[0] == "OK" and ack[2]:
            self.authed = True
            return ack
        raise RuntimeError(f"NIP-42 auth failed: {ack}")

    async def publish(self, kind: int, content: str, tags: Optional[list] = None) -> dict:
        event = build_event(self.pk, kind, content, tags)
        await self.ws.send(json.dumps(["EVENT", event]))
        ack = await self._recv_json()
        return {"event": event, "ack": ack}

    async def subscribe(self, filters: list, sub_id: Optional[str] = None) -> str:
        sub_id = sub_id or uuid.uuid4().hex[:16]
        await self.ws.send(json.dumps(["REQ", sub_id] + filters))
        return sub_id

    async def recv_until_eose(self, sub_id: str, max_events=50):
        """Real bug found live: a relay-side rejection (e.g. a filter that
        fails req.rs's authors=[self]/#p=[self] gate for gated kinds) comes
        back as ["CLOSED", sub_id, reason] per NIP-01, not EVENT/EOSE. This
        used to loop forever waiting for an EOSE that would never arrive,
        hanging the whole request until the caller's own timeout. CLOSED
        (and any other non-EVENT/EOSE frame targeting this sub_id) now ends
        the wait immediately with a clear error instead of a silent hang."""
        events = []
        while len(events) < max_events:
            msg = await self._recv_json()
            if msg[0] == "EVENT" and msg[1] == sub_id:
                events.append(msg[2])
            elif msg[0] == "EOSE" and msg[1] == sub_id:
                break
            elif msg[0] == "CLOSED" and msg[1] == sub_id:
                reason = msg[2] if len(msg) > 2 else ""
                raise RuntimeError(f"subscription {sub_id} closed by relay: {reason}")
        return events

    async def stream_events(self, sub_id: str):
        """Like recv_until_eose, but doesn't stop there -- yields every
        EVENT for this subscription indefinitely (EOSE just means "caught
        up", not "done"), for long-lived listeners (buzz_bridge's inbound
        feed subscription). Raises the same way on CLOSED."""
        while True:
            msg = await self._recv_json()
            if msg[0] == "EVENT" and msg[1] == sub_id:
                yield msg[2]
            elif msg[0] == "CLOSED" and msg[1] == sub_id:
                reason = msg[2] if len(msg) > 2 else ""
                raise RuntimeError(f"subscription {sub_id} closed by relay: {reason}")
            # EOSE and anything else (other sub_ids) are just ignored here.
