"""Decoding Meshtastic frames, and refusing ones that are not what they claim.

Field numbers checked against meshtastic/protobufs mesh.proto directly. The
encoder below is only a test fixture -- it exists so the decoder is exercised
against bytes built to the spec rather than against its own output.
"""
import base64

import pytest

from backend import meshtastic_frames as mf


# ── a minimal encoder, for building fixtures ─────────────────────────────────

def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _key(number: int, wire: int) -> bytes:
    return _varint((number << 3) | wire)


def _fixed32(number: int, value: int) -> bytes:
    return _key(number, mf.WIRE_FIXED32) + value.to_bytes(4, "little")


def _uint(number: int, value: int) -> bytes:
    return _key(number, mf.WIRE_VARINT) + _varint(value)


def _bytes(number: int, value: bytes) -> bytes:
    return _key(number, mf.WIRE_LENGTH) + _varint(len(value)) + value


def build_packet(*, packet_id=0x1A2B3C4D, from_node=0xDEADBEEF, to_node=0xFFFFFFFF,
                 channel=0, hop_limit=3, hop_start=5, payload=b"hello mesh",
                 portnum=1, rx_time=1756000000, extra=b"", encrypted=False):
    data = _uint(mf.D_PORTNUM, portnum) + _bytes(mf.D_PAYLOAD, payload)
    out = (
        _fixed32(mf.F_FROM, from_node)
        + _fixed32(mf.F_TO, to_node)
        + _uint(mf.F_CHANNEL, channel)
        + _fixed32(mf.F_ID, packet_id)
        + _fixed32(mf.F_RX_TIME, rx_time)
        + _uint(mf.F_HOP_LIMIT, hop_limit)
        + _uint(mf.F_HOP_START, hop_start)
    )
    out += _bytes(mf.F_ENCRYPTED, b"\x00" * 8) if encrypted else _bytes(mf.F_DECODED, data)
    return out + extra


# ── decoding ─────────────────────────────────────────────────────────────────

def test_a_frame_decodes_to_its_fields():
    frame = mf.decode_frame(build_packet())
    assert frame.packet_id == 0x1A2B3C4D
    assert frame.from_node == 0xDEADBEEF
    assert frame.payload == b"hello mesh"
    assert frame.portnum == 1
    assert frame.channel == 0


def test_the_node_id_is_the_form_meshtastic_shows_users():
    frame = mf.decode_frame(build_packet(from_node=0x0A0B0C0D))
    assert frame.node_id == "!0a0b0c0d"
    assert frame.packet_ref.startswith("!")


def test_hops_taken_is_the_difference_between_start_and_limit():
    frame = mf.decode_frame(build_packet(hop_start=5, hop_limit=3))
    assert frame.hops_taken == 2


def test_a_frame_without_hop_start_has_an_unknown_hop_count():
    """Older firmware does not send hop_start. Reporting 0 for it would say
    every such packet arrived direct, which is a different claim entirely."""
    frame = mf.decode_frame(build_packet(hop_start=0, hop_limit=3))
    assert frame.hops_taken is None


def test_an_encrypted_frame_decodes_its_header_and_says_so():
    """There is nothing to decode without the channel key, and this module
    does not hold one."""
    frame = mf.decode_frame(build_packet(encrypted=True))
    assert frame.encrypted is True
    assert frame.payload == b""
    assert frame.packet_id == 0x1A2B3C4D


def test_unknown_fields_are_recorded_rather_than_fatal():
    """A newer firmware adding a field must not break this decoder."""
    frame = mf.decode_frame(build_packet(extra=_uint(99, 7)))
    assert 99 in frame.unknown_fields
    assert frame.packet_id == 0x1A2B3C4D


def test_a_frame_truncated_mid_field_is_refused():
    raw = build_packet()
    for cut in (1, 3, len(raw) - 1):
        with pytest.raises(mf.FrameError):
            mf.decode_frame(raw[:cut])


def test_a_frame_truncated_on_a_field_boundary_is_caught_by_its_missing_fields():
    """Protobuf has no terminator and no length prefix, so a message cut
    exactly between fields decodes cleanly as a shorter message. Nothing
    structural can catch that -- requiring the fields a real received packet
    always carries is what does."""
    raw = build_packet()
    boundary = raw[:5]  # the `from` field, complete, and nothing else
    mf._fields(boundary)  # the reader itself is happy
    with pytest.raises(mf.FrameError) as excinfo:
        mf.decode_frame(boundary)
    assert "packet id" in str(excinfo.value)


def test_a_frame_with_no_sending_node_is_refused():
    with pytest.raises(mf.FrameError):
        mf.decode_frame(build_packet(from_node=0))


def test_an_empty_frame_is_refused():
    with pytest.raises(mf.FrameError):
        mf.decode_frame(b"")


def test_a_hop_limit_above_the_protocol_maximum_is_refused():
    with pytest.raises(mf.FrameError) as excinfo:
        mf.decode_frame(build_packet(hop_limit=9, hop_start=9))
    assert "maximum" in str(excinfo.value)


def test_a_hop_limit_above_hop_start_is_refused():
    """It would decode to a negative hop count -- the frame is malformed."""
    with pytest.raises(mf.FrameError):
        mf.decode_frame(build_packet(hop_start=2, hop_limit=4))


def test_field_number_zero_is_refused():
    with pytest.raises(mf.FrameError):
        mf.decode_frame(_key(0, mf.WIRE_VARINT) + _varint(1))


def test_an_overlong_varint_is_refused():
    with pytest.raises(mf.FrameError):
        mf.decode_frame(_key(9, mf.WIRE_VARINT) + b"\xff" * 12 + b"\x01")


def test_base64_decoding_reports_bad_input_clearly():
    with pytest.raises(mf.FrameError) as excinfo:
        mf.decode_b64("not base64 at all!!")
    assert "base64" in str(excinfo.value)


def test_a_frame_round_trips_through_base64():
    encoded = base64.b64encode(build_packet()).decode()
    assert mf.decode_b64(encoded).packet_id == 0x1A2B3C4D


# ── binding to the signed event ──────────────────────────────────────────────

def _event(hop_count=2, origin_id="!1a2b3c4d", network="meshtastic"):
    return {"network": network, "hop_count": hop_count, "origin_id": origin_id}


def test_a_frame_matching_its_event_is_accepted():
    frame = mf.decode_frame(build_packet(hop_start=5, hop_limit=3))
    detail = mf.check_against_event(frame, _event())
    assert detail["payload_bytes"] == len(b"hello mesh")
    assert detail["hops_taken"] == 2


def test_a_frame_naming_a_different_packet_is_refused():
    """A frame that is not the one the event describes is not extra
    information -- it is a different packet wearing another's signature."""
    frame = mf.decode_frame(build_packet(packet_id=0x11111111))
    with pytest.raises(mf.FrameError) as excinfo:
        mf.check_against_event(frame, _event())
    assert "does not name this frame" in str(excinfo.value)


def test_a_frame_whose_hop_count_contradicts_the_signed_event_is_refused():
    frame = mf.decode_frame(build_packet(hop_start=5, hop_limit=3))  # 2 hops
    with pytest.raises(mf.FrameError) as excinfo:
        mf.check_against_event(frame, _event(hop_count=6))
    assert "the frame records 2" in str(excinfo.value)


def test_a_frame_submitted_against_a_non_meshtastic_event_is_refused():
    frame = mf.decode_frame(build_packet())
    with pytest.raises(mf.FrameError):
        mf.check_against_event(frame, _event(network="lorawan"))


def test_an_old_frame_with_no_hop_start_does_not_contradict_anything():
    """Unknown is not a mismatch. Refusing it would reject every packet from
    firmware that predates hop_start."""
    frame = mf.decode_frame(build_packet(hop_start=0, hop_limit=3))
    detail = mf.check_against_event(frame, _event(hop_count=4))
    assert detail["hops_taken"] is None


def test_the_frame_never_claims_to_prove_a_route():
    """Hop counts come out of the frame's own header, which any relay along
    the path could have rewritten."""
    frame = mf.decode_frame(build_packet())
    assert mf.check_against_event(frame, _event())["route_proven"] is False


# ── cross-repo agreement ─────────────────────────────────────────────────────

#: Emitted by omokoda-mesh's C++ `meshtasticEncode` -- an independent
#: implementation of the same six fields, compiled and run. Two decoders
#: written from one spec agree until one of them quietly stops agreeing, and
#: this is where that shows up.
FIRMWARE_FRAME_HEX = "0defbeadde15ffffffff220e0801120a63726f7373207265706f354d3c2b1a3d006faa6848037805"


def test_this_decoder_agrees_with_the_firmware_encoder():
    frame = mf.decode_frame(bytes.fromhex(FIRMWARE_FRAME_HEX))
    assert frame.packet_ref == "!1a2b3c4d"
    assert frame.node_id == "!deadbeef"
    assert frame.hops_taken == 2
    assert frame.payload == b"cross repo"
    assert frame.portnum == 1


def test_a_firmware_frame_binds_to_a_matching_event():
    """The end-to-end shape: firmware encodes, a gateway signs tags about it,
    and this instance checks the two against each other."""
    frame = mf.decode_frame(bytes.fromhex(FIRMWARE_FRAME_HEX))
    detail = mf.check_against_event(
        frame, {"network": "meshtastic", "hop_count": 2, "origin_id": "!1a2b3c4d"}
    )
    assert detail["payload_bytes"] == len(b"cross repo")
