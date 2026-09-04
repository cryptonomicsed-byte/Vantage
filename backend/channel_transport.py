"""Channel transports: one interface, several ways a message can travel.

Until now "publish a channel message" meant one thing -- open a WebSocket to
the Buzz relay. That is still the default and still the only backend that
carries a full round trip, but it is no longer the only way an event reaches
this instance: radio mesh gateways already push events in over HTTP, and
Freenet is a third possibility. This module is the seam that lets those be
alternatives rather than special cases scattered through the coordination
layer.

A transport does not change what a message *is*. Every backend moves the same
signed kind-9 event; none of them gets an event kind of its own, and none may
rewrite one in transit. What differs is reach, latency and whether a round
trip is possible at all.

**On Freenet, specifically.** Its client API is bincode over a WebSocket to
a local node -- `ContractRequest::{Put, Update, Get, Subscribe}`, checked
against freenet-stdlib rather than recalled. Bincode is a Rust-struct-layout
format with no self-describing schema, so reimplementing it in Python would
be guesswork that compiles, passes its own tests, and fails against a real
node. The adapter therefore speaks to a *bridge* -- a small Rust service that
holds freenet-stdlib as a real dependency and exposes HTTP -- and refuses to
enable itself unless one is configured. What is asserted here is the shape of
the contract with that bridge; a live Freenet round trip is not asserted
anywhere, because none has been run.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class TransportUnavailable(RuntimeError):
    """This backend cannot carry the message right now."""


class TransportNotSupported(TransportUnavailable):
    """This backend cannot carry this *direction* at all -- distinct from a
    transport that is merely down, because retrying will never help."""


@dataclass(frozen=True)
class TransportInfo:
    name: str
    #: Can this backend publish, receive, or both?
    can_publish: bool
    can_receive: bool
    configured: bool
    #: Has a real round trip actually been run against it?
    proven: bool
    detail: str = ""


class Transport(Protocol):
    name: str

    def info(self) -> TransportInfo: ...

    async def publish(self, *, principal: dict, channel: dict, guild_slug: str,
                      content: str, msg_type: str, **kwargs) -> dict: ...


class RelayTransport:
    """The Buzz relay. The default, and the only one with a proven round trip."""

    name = "relay"

    def info(self) -> TransportInfo:
        from .buzz_registration import RELAY_WS_URL

        return TransportInfo(
            name=self.name, can_publish=True, can_receive=True,
            configured=bool(RELAY_WS_URL), proven=True,
            detail=f"NIP-01/NIP-42 relay at {RELAY_WS_URL or 'unset'}",
        )

    async def publish(self, *, principal: dict, channel: dict, guild_slug: str,
                      content: str, msg_type: str = "say", **kwargs) -> dict:
        from . import coordination as coord

        return await coord.publish_message(
            channel=channel, guild_slug=guild_slug, principal=principal,
            content=content, msg_type=msg_type, **kwargs,
        )


class MeshnetTransport:
    """Radio mesh gateways. Ingress only, and structurally so.

    A gateway pushes what its radio heard; there is no route back out to a
    specific LoRa node from here, and pretending otherwise would produce a
    publish that silently goes nowhere. Refusing is the honest answer.
    """

    name = "meshnet"

    def info(self) -> TransportInfo:
        return TransportInfo(
            name=self.name, can_publish=False, can_receive=True,
            configured=True, proven=False,
            detail="gateway ingress at /api/meshnet; no egress path exists",
        )

    async def publish(self, **kwargs) -> dict:
        raise TransportNotSupported(
            "the radio mesh is ingress only: a gateway pushes what its radio heard, "
            "and there is no route from here back to a specific node"
        )


class FreenetTransport:
    """Freenet, through a bridge that holds the real client library.

    Disabled unless VANTAGE_FREENET_BRIDGE_URL names one. The bridge contract
    is deliberately thin -- POST an event, GET a contract's state -- because
    everything that needs freenet-stdlib stays on the far side of it.
    """

    name = "freenet"

    #: Operations the bridge exposes, named after the ContractRequest variants
    #: they map to so the correspondence stays legible from this side.
    OPERATIONS = {
        "publish": "ContractRequest::Update",
        "fetch": "ContractRequest::Get",
        "subscribe": "ContractRequest::Subscribe",
        "create": "ContractRequest::Put",
    }

    def __init__(self, bridge_url: Optional[str] = None, timeout: float = 10.0):
        self.bridge_url = (bridge_url if bridge_url is not None
                           else os.getenv("VANTAGE_FREENET_BRIDGE_URL", "")).rstrip("/")
        self.timeout = timeout

    def info(self) -> TransportInfo:
        return TransportInfo(
            name=self.name, can_publish=True, can_receive=True,
            configured=bool(self.bridge_url), proven=False,
            detail=(
                f"via bridge at {self.bridge_url}" if self.bridge_url
                else "unconfigured; set VANTAGE_FREENET_BRIDGE_URL to a bridge that "
                     "holds freenet-stdlib"
            ),
        )

    def contract_key(self, channel: dict) -> str:
        """The contract standing in for one channel.

        Derived from the channel's relay id rather than assigned, so the same
        channel resolves to the same contract on every instance that carries
        it -- which is what makes a Freenet-backed channel federatable at all.
        """
        import hashlib

        seed = str(channel.get("buzz_channel_id") or channel.get("id") or "")
        if not seed:
            raise TransportUnavailable("channel has no stable identifier")
        return hashlib.sha256(f"vantage-channel-v1:{seed}".encode()).hexdigest()

    async def publish(self, *, principal: dict, channel: dict, guild_slug: str,
                      content: str, msg_type: str = "say", **kwargs) -> dict:
        if not self.bridge_url:
            raise TransportUnavailable(
                "no Freenet bridge is configured on this instance"
            )

        from . import coordination as coord

        # The event is signed here, with the same key and the same tags as
        # the relay path. The bridge moves bytes; it never signs.
        pk = await coord.signing_key_for_principal(principal)
        if pk is None:
            raise TransportUnavailable(
                "this principal holds its own key — sign and submit the event yourself"
            )
        from .buzz_client import build_event

        tags = coord.build_message_tags(
            buzz_channel_id=channel["buzz_channel_id"], guild_slug=guild_slug,
            channel_slug=channel["slug"], msg_type=msg_type,
            root_event_id=kwargs.get("root_event_id"),
            reply_to_event_id=kwargs.get("reply_to_event_id"),
            addressed_to=kwargs.get("addressed_to"),
            work_ref=kwargs.get("work_ref"),
            extra_tags=kwargs.get("extra_tags"),
        )
        event = build_event(pk, coord.KIND_MESSAGE, content, tags=tags)

        import httpx

        payload = {
            "operation": self.OPERATIONS["publish"],
            "contract_key": self.contract_key(channel),
            "event": event,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.bridge_url}/contract/update", json=payload)
                resp.raise_for_status()
        except Exception as exc:
            raise TransportUnavailable(f"Freenet bridge unreachable: {exc}") from exc

        # Indexed exactly as a relay event would be, by the same function --
        # so a message that travelled over Freenet and one that travelled
        # over the relay produce identical rows.
        await coord.index_event(event, channel=channel)
        return event


_REGISTRY: dict[str, Transport] = {}


def register(transport: Transport) -> None:
    _REGISTRY[transport.name] = transport


def get(name: str) -> Transport:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise TransportUnavailable(
            f"unknown transport {name!r}; this instance has "
            f"{', '.join(sorted(_REGISTRY)) or 'none'}"
        ) from None


def available() -> list[TransportInfo]:
    return [t.info() for t in _REGISTRY.values()]


def default_name() -> str:
    """The relay, unless the deployment says otherwise and that transport is
    actually configured. A misconfigured override falls back rather than
    breaking every post on the instance."""
    chosen = os.getenv("VANTAGE_DEFAULT_TRANSPORT", "relay")
    transport = _REGISTRY.get(chosen)
    if transport is None or not transport.info().configured:
        if chosen != "relay":
            logger.warning(
                "transport: VANTAGE_DEFAULT_TRANSPORT=%s is not usable; using the relay",
                chosen,
            )
        return "relay"
    return chosen


register(RelayTransport())
register(MeshnetTransport())
register(FreenetTransport())
