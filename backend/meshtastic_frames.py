"""Decoding Meshtastic frames, and binding one to the event that carries it.

`omokoda-mesh` canonicalises every frame into an envelope and bridges it onto
Nostr as a signed kind-20000 event. Reading `envelopeToNostrEvent` in
`lib/nostr_bridge/nostr_event.cpp` turns up a real limitation, stated in its
own comment: the bridged event carries the origin pubkey in `content` and
**drops the payload bytes**. So a mesh packet arriving over the relay is
routing metadata -- who relayed what, when, over which network -- and not the
message.

That is a defensible firmware trade-off (base64 on an ESP32, in a LoRa MTU),
and it is a problem for a gateway, which is not on the radio and cannot fetch
the payload over the originating transport. This module is the other half:
the gateway submits the raw frame alongside the signed event, and the frame
is checked *against* the event's signed tags rather than trusted on its own.

The frame stays untrusted, and the binding is what gives it standing. A
gateway could submit any bytes; what it cannot do is submit bytes whose
packet id, hop count and network disagree with the tags it already signed.

Field numbers are from `meshtastic/protobufs` `mesh.proto`, read directly
rather than from memory -- the same rule the mesh repo applies to itself.
Only the fields the bridge needs are decoded; everything else is skipped by
wire type, so a newer firmware adding fields does not break this.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Optional

# ── MeshPacket, from mesh.proto ──────────────────────────────────────────────
F_FROM = 1          # fixed32
F_TO = 2            # fixed32
F_CHANNEL = 3       # uint32
F_DECODED = 4       # Data (part of the payload_variant oneof)
F_ENCRYPTED = 5     # bytes (the other arm of that oneof)
F_ID = 6            # fixed32
F_RX_TIME = 7       # fixed32
F_HOP_LIMIT = 9     # uint32
F_VIA_MQTT = 14     # bool
F_HOP_START = 15    # uint32
F_RELAY_NODE = 19   # uint32

# ── Data ─────────────────────────────────────────────────────────────────────
D_PORTNUM = 1       # PortNum enum
D_PAYLOAD = 2       # bytes

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH = 2
WIRE_FIXED32 = 5

#: Meshtastic's own cap. A frame claiming more is malformed, not a long trip.
MAX_HOP_LIMIT = 7


class FrameError(ValueError):
    """A frame that will not decode, and why."""


# ── a minimal protobuf reader ────────────────────────────────────────────────
# Only what these two messages need. Written out rather than pulled in as a
# dependency because generating Meshtastic's protobufs would drag the whole
# schema -- and its licence -- into a repository that needs six fields.

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise FrameError("truncated varint")
        if shift > 63:
            raise FrameError("varint longer than 64 bits")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _skip(data: bytes, pos: int, wire_type: int) -> int:
    if wire_type == WIRE_VARINT:
        _, pos = _read_varint(data, pos)
        return pos
    if wire_type == WIRE_FIXED32:
        return pos + 4
    if wire_type == WIRE_FIXED64:
        return pos + 8
    if wire_type == WIRE_LENGTH:
        length, pos = _read_varint(data, pos)
        return pos + length
    raise FrameError(f"unsupported protobuf wire type {wire_type}")


def _fields(data: bytes):
    """Yield (field_number, wire_type, value) for one message.

    Values come back as ints for varints and fixed32s, and as bytes for
    length-delimited fields. Unknown fields are yielded too, so a caller can
    ignore them by number rather than by having to know their shape.
    """
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_number, wire_type = key >> 3, key & 0x07
        if field_number == 0:
            raise FrameError("field number 0 is not valid protobuf")
        if wire_type == WIRE_VARINT:
            value, pos = _read_varint(data, pos)
        elif wire_type == WIRE_FIXED32:
            if pos + 4 > len(data):
                raise FrameError("truncated fixed32")
            value = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        elif wire_type == WIRE_LENGTH:
            length, pos = _read_varint(data, pos)
            if pos + length > len(data):
                raise FrameError("truncated length-delimited field")
            value = data[pos:pos + length]
            pos += length
        else:
            start = pos
            pos = _skip(data, pos, wire_type)
            if pos > len(data):
                raise FrameError("truncated field")
            value = data[start:pos]
        yield field_number, wire_type, value


@dataclass
class MeshtasticFrame:
    packet_id: int = 0
    from_node: int = 0
    to_node: int = 0
    channel: int = 0
    rx_time: int = 0
    hop_limit: int = 0
    hop_start: int = 0
    relay_node: int = 0
    via_mqtt: bool = False
    portnum: Optional[int] = None
    payload: bytes = b""
    encrypted: bool = False
    unknown_fields: list[int] = field(default_factory=list)

    @property
    def node_id(self) -> str:
        """The `!hex` form Meshtastic shows users, and what the firmware puts
        in `origin_id`."""
        return f"!{self.from_node:08x}"

    @property
    def packet_ref(self) -> str:
        return f"!{self.packet_id:08x}"

    @property
    def hops_taken(self) -> Optional[int]:
        """`hop_start - hop_limit`, or None where hop_start is unset.

        Older firmware does not send hop_start, and a frame from one is a
        frame whose hop count is genuinely unknown. Returning 0 for it would
        report every such packet as having arrived direct.
        """
        if self.hop_start == 0:
            return None
        return max(0, self.hop_start - self.hop_limit)


def decode_frame(raw: bytes) -> MeshtasticFrame:
    """Decode a MeshPacket. Unknown fields are recorded, not fatal."""
    if not raw:
        raise FrameError("empty frame")
    frame = MeshtasticFrame()
    for number, wire_type, value in _fields(raw):
        if number == F_FROM:
            frame.from_node = value
        elif number == F_TO:
            frame.to_node = value
        elif number == F_CHANNEL:
            frame.channel = value
        elif number == F_ID:
            frame.packet_id = value
        elif number == F_RX_TIME:
            frame.rx_time = value
        elif number == F_HOP_LIMIT:
            frame.hop_limit = value
        elif number == F_HOP_START:
            frame.hop_start = value
        elif number == F_RELAY_NODE:
            frame.relay_node = value
        elif number == F_VIA_MQTT:
            frame.via_mqtt = bool(value)
        elif number == F_ENCRYPTED:
            # The other arm of the oneof. There is nothing to decode without
            # the channel key, and this module does not hold one.
            frame.encrypted = True
        elif number == F_DECODED:
            if wire_type != WIRE_LENGTH:
                raise FrameError("decoded must be a length-delimited message")
            for d_number, _wt, d_value in _fields(value):
                if d_number == D_PORTNUM:
                    frame.portnum = d_value
                elif d_number == D_PAYLOAD:
                    frame.payload = d_value
        else:
            frame.unknown_fields.append(number)

    # Protobuf has no terminator and no length prefix, so a message
    # truncated exactly on a field boundary decodes cleanly as a shorter
    # message -- there is no way to catch that structurally. Requiring the
    # fields a real received packet always carries is what catches it
    # instead, and `id` is the one `origin_id` binds to.
    if frame.packet_id == 0:
        raise FrameError("frame has no packet id; it is truncated or not a MeshPacket")
    if frame.from_node == 0:
        raise FrameError("frame names no sending node")

    if frame.hop_limit > MAX_HOP_LIMIT:
        raise FrameError(f"hop_limit {frame.hop_limit} exceeds the protocol maximum")
    if frame.hop_start and frame.hop_limit > frame.hop_start:
        raise FrameError("hop_limit is above hop_start; the frame is malformed")
    return frame


def decode_b64(encoded: str) -> MeshtasticFrame:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FrameError(f"frame is not valid base64: {exc}") from exc
    return decode_frame(raw)


# ── binding a frame to the event that carries it ─────────────────────────────

def check_against_event(frame: MeshtasticFrame, parsed_event: dict) -> dict:
    """Confirm a submitted frame is the one the signed event describes.

    This is the whole point of accepting a frame at all. A gateway can submit
    any bytes it likes; what it cannot do is submit bytes whose packet id,
    hop count and origin network disagree with tags it has already signed
    with its own key.

    Returns a summary. Raises FrameError on a mismatch, because a frame that
    is not the one the event describes is not extra information -- it is a
    different packet wearing another packet's signature.
    """
    if parsed_event.get("network") != "meshtastic":
        raise FrameError(
            f"a Meshtastic frame belongs to a meshtastic-origin event, "
            f"not {parsed_event.get('network')!r}"
        )

    origin_id = str(parsed_event.get("origin_id") or "")
    if origin_id and origin_id.lower() not in (frame.packet_ref, str(frame.packet_id)):
        raise FrameError(
            f"origin_id {origin_id} does not name this frame "
            f"(packet {frame.packet_ref})"
        )

    hops = frame.hops_taken
    if hops is not None and hops != parsed_event.get("hop_count"):
        raise FrameError(
            f"the event claims {parsed_event.get('hop_count')} hops but the frame "
            f"records {hops} (hop_start {frame.hop_start} - hop_limit {frame.hop_limit})"
        )

    return {
        "packet_id": frame.packet_ref,
        "from_node": frame.node_id,
        "channel": frame.channel,
        "portnum": frame.portnum,
        "payload_bytes": len(frame.payload),
        "encrypted": frame.encrypted,
        "via_mqtt": frame.via_mqtt,
        "hops_taken": hops,
        # Stated rather than assumed: hop counts come from the frame's own
        # header, which any relay along the path could have rewritten. This
        # is consistency with the signed event, not proof of the route.
        "route_proven": False,
    }
