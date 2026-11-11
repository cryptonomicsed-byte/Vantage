"""Nostr client for Vantage: NIP-01 events + NIP-42 auth.

Renamed from buzz_client.py. BuzzSession alias kept for backward compat.
"""
import hashlib
import json
import time
import uuid
from typing import Optional

import asyncio
from urllib.parse import urlparse
import websockets
try:
    from coincurve import PrivateKey
except ImportError:
    PrivateKey = None  # type: ignore[assignment,misc]

from ..buzz_identity import public_key_xonly_hex, sign_event_id


def _event_id(pubkey_hex: str, created_at: int, kind: int, tags: list, content: str) -> str:
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


#: How long a subscription may sit silent before we simply keep waiting.
STREAM_IDLE_SECONDS = 30.0


class NostrSession:
    """Generic NIP-01/NIP-42 WebSocket session to a Nostr relay."""

    def __init__(self, relay_ws_url: str, pk: PrivateKey):
        self.relay_ws_url = relay_ws_url
        self.pk = pk
        self.ws = None
        self.authed = False

    async def connect(self):
        # Reconnecting on the same session must not orphan the old socket.
        await self.close()
        self.ws = await websockets.connect(self.relay_ws_url, open_timeout=10)

    async def close(self):
        """Close the socket. Safe to call more than once and on a session
        whose connect/auth failed part-way: a socket that is merely dropped
        is kept alive by websockets' keepalive pings and never released."""
        ws, self.ws, self.authed = self.ws, None, False
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def _recv_json(self, timeout=10):
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def authenticate(self, extra_tags: Optional[list] = None, timeout: float = 15.0):
        """NIP-42 auth handshake. Raises asyncio.TimeoutError if relay sends no AUTH within timeout."""
        msg = await self._recv_json(timeout=timeout)
        if msg[0] != "AUTH":
            raise RuntimeError(f"expected proactive AUTH challenge, got: {msg}")
        challenge = msg[1]
        # NIP-42 relay tag must match what the relay validates: scheme+host only,
        # no path (buzz-relay derives wss://host from the tenant Host header,
        # not from the WS path). Strip any path from the connection URL.
        _parsed = urlparse(self.relay_ws_url)
        _nip42_relay = f"{_parsed.scheme}://{_parsed.netloc}"
        auth_event = build_event(
            self.pk,
            kind=22242,
            content="",
            tags=[["relay", _nip42_relay], ["challenge", challenge]] + (extra_tags or []),
        )
        await self.ws.send(json.dumps(["AUTH", auth_event]))
        ack = await self._recv_json()
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
        """Yield events until the relay genuinely closes the subscription.

        `_recv_json` defaults to a 10s read timeout. Calling it bare in here
        meant a QUIET channel raised TimeoutError every 10s, which the caller
        caught and logged as "listener disconnected" -- an idle feed looked
        exactly like a broken one and reconnected forever. An idle read is
        normal for a subscription; only a closed socket or an explicit CLOSED
        frame means the subscription ended.
        """
        import asyncio as _aio
        while True:
            try:
                raw = await _aio.wait_for(self.ws.recv(), timeout=STREAM_IDLE_SECONDS)
            except _aio.TimeoutError:
                continue                      # quiet channel, not a disconnect
            except Exception as exc:          # socket closed / transport gone
                raise RuntimeError(f"relay stream ended: {type(exc).__name__}") from exc
            msg = json.loads(raw)
            if msg[0] == "EVENT" and msg[1] == sub_id:
                yield msg[2]
            elif msg[0] == "CLOSED" and msg[1] == sub_id:
                reason = msg[2] if len(msg) > 2 else ""
                raise RuntimeError(f"subscription {sub_id} closed by relay: {reason}")


# Backward-compat alias
BuzzSession = NostrSession
