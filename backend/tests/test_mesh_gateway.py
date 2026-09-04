"""Mesh ingress, against the firmware's actual tag contract.

The contract is omokoda-mesh/docs/EVENT_KINDS.md and
lib/nostr_bridge/nostr_event.cpp, read directly. The cases below are the
ones where a gateway that is too permissive stops being useful: an unlabelled
origin network becoming "local", a hop trail being reported as proof of a
path nobody signed, and an unsigned event being recorded because it looked
well-formed.
"""
import json
import time

import pytest
import pytest_asyncio
from coincurve import PrivateKey

from backend import mesh_gateway as mesh
from backend.buzz_client import _event_id
from backend.buzz_identity import public_key_xonly_hex, sign_event_id
from backend.db import get_db


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    from backend import coordination as coord

    await coord.init_coordination_db()
    await mesh.init_mesh_db()


@pytest.fixture
def node():
    """A mesh node with its own key, as the firmware has."""
    key = PrivateKey()

    def sign(kind=mesh.KIND_MESH_ROUTE, tags=None, content="", created_at=None):
        pubkey = public_key_xonly_hex(key)
        created_at = created_at or int(time.time())
        tags = tags if tags is not None else default_tags()
        eid = _event_id(pubkey, created_at, kind, tags, content)
        return {"id": eid, "pubkey": pubkey, "created_at": created_at, "kind": kind,
                "tags": tags, "content": content, "sig": sign_event_id(key, eid)}

    sign.pubkey = public_key_xonly_hex(key)
    return sign


def default_tags(network="meshtastic", hop=2, origin_id="!a1b2c3d4", extra=None):
    tags = [["network", network], ["hop", str(hop)], ["origin_id", origin_id]]
    return tags + (extra or [])


# ── the signature ────────────────────────────────────────────────────────────

def test_a_signed_event_verifies(node):
    event = node()
    assert mesh.verify_event(event) == event["id"]


def test_an_event_whose_id_was_supplied_rather_than_derived_is_refused(node):
    """A signature over an id the sender chose proves nothing about the
    content the id is supposed to describe."""
    event = node()
    event["tags"] = default_tags(hop=99)  # content changed, id left alone
    with pytest.raises(mesh.MeshRejected) as excinfo:
        mesh.verify_event(event)
    assert "does not match" in str(excinfo.value)


def test_an_event_signed_by_a_different_key_is_refused(node):
    other = PrivateKey()
    event = node()
    event["pubkey"] = public_key_xonly_hex(other)
    with pytest.raises(mesh.MeshRejected):
        mesh.verify_event(event)


def test_a_truncated_signature_is_refused(node):
    event = node()
    event["sig"] = event["sig"][:100]
    with pytest.raises(mesh.MeshRejected) as excinfo:
        mesh.verify_event(event)
    assert "64 bytes" in str(excinfo.value)


# ── the tag contract ─────────────────────────────────────────────────────────

def test_the_three_required_tags_are_required(node):
    for missing in ("network", "hop", "origin_id"):
        tags = [t for t in default_tags() if t[0] != missing]
        with pytest.raises(mesh.MeshRejected) as excinfo:
            mesh.parse_mesh_event(node(tags=tags))
        assert missing in str(excinfo.value)


def test_an_unknown_origin_network_is_refused_not_defaulted(node):
    """A packet from a network this instance cannot interpret must not land
    in the same bucket as traffic that really is local."""
    with pytest.raises(mesh.MeshRejected) as excinfo:
        mesh.parse_mesh_event(node(tags=default_tags(network="zigbee")))
    assert "unknown origin network" in str(excinfo.value)


def test_every_network_the_firmware_can_emit_is_understood():
    """From SourceNetwork in include/packet/envelope.h."""
    assert mesh.NETWORKS == {"nostr", "reticulum", "meshtastic", "lorawan", "depin", "local"}


def test_a_reticulum_packet_without_its_destination_is_refused(node):
    """The firmware emits reticulum_dest for exactly this network; missing
    means the event was assembled by something that is not the firmware."""
    with pytest.raises(mesh.MeshRejected) as excinfo:
        mesh.parse_mesh_event(node(tags=default_tags(network="reticulum")))
    assert "reticulum_dest" in str(excinfo.value)


def test_a_reticulum_packet_with_its_destination_parses(node):
    parsed = mesh.parse_mesh_event(node(tags=default_tags(
        network="reticulum", extra=[["reticulum_dest", "ab" * 16]],
    )))
    assert parsed["reticulum_dest"] == "ab" * 16


def test_a_non_numeric_hop_count_is_refused(node):
    with pytest.raises(mesh.MeshRejected):
        mesh.parse_mesh_event(node(tags=[["network", "local"], ["hop", "many"],
                                         ["origin_id", "x"]]))


def test_a_hop_count_beyond_the_wire_format_is_refused(node):
    """The firmware's hop trail is length-prefixed with one byte."""
    with pytest.raises(mesh.MeshRejected):
        mesh.parse_mesh_event(node(tags=default_tags(hop=999)))


def test_a_malformed_hop_signature_tag_is_refused(node):
    """The contract is [pubkey, signature, timestamp]; two of three is a
    hop trail that cannot be checked even in principle."""
    with pytest.raises(mesh.MeshRejected):
        mesh.parse_mesh_event(node(tags=default_tags(
            extra=[["hop_sig", "aa" * 32, "bb" * 64]],
        )))


def test_a_hop_trail_is_recorded_but_never_reported_as_verified(node):
    """Nothing in the firmware signs a hop yet -- there is no hop-signing
    code in lib/, only the struct and its encoding. Reporting these as
    proof of relay would be a claim this instance cannot support."""
    parsed = mesh.parse_mesh_event(node(tags=default_tags(
        extra=[["hop_sig", "aa" * 32, "bb" * 64, "1756000000"]],
    )))
    assert len(parsed["hop_trail"]) == 1
    assert parsed["hops_verified"] is False


def test_a_non_mesh_kind_is_refused(node):
    with pytest.raises(mesh.MeshRejected):
        mesh.parse_mesh_event(node(kind=1))


def test_telemetry_and_presence_are_mesh_kinds_too(node):
    for kind in (mesh.KIND_MESH_TELEMETRY, mesh.KIND_MESH_PRESENCE):
        assert mesh.parse_mesh_event(node(kind=kind))["kind"] == kind


# ── ingress ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingesting_a_packet_records_it_and_the_node(client, node):
    event = node()
    result = await mesh.ingest(event)
    assert result["recorded"] is True
    assert result["network"] == "meshtastic"

    nodes = await mesh.known_nodes()
    mine = [n for n in nodes if n["pubkey"] == node.pubkey]
    assert mine, "the gateway should be registered as a node"


@pytest.mark.asyncio
async def test_a_mesh_node_is_a_principal_that_holds_its_own_key(client, node):
    """A node is a member that arrived over LoRa. Vantage holds the public
    half only -- it cannot sign for field hardware and must not appear to."""
    from backend import coordination as coord

    await mesh.ingest(node())
    principal = await coord.get_or_create_external_principal(
        pubkey=node.pubkey, display_name="x", framework="omokoda-mesh",
    )
    assert principal["key_custody"] == "self"
    assert await coord.signing_key_for_principal(principal) is None


@pytest.mark.asyncio
async def test_ingesting_the_same_packet_twice_records_it_once(client, node):
    event = node()
    first = await mesh.ingest(event)
    second = await mesh.ingest(event)
    assert first["recorded"] is True
    assert second["recorded"] is False


@pytest.mark.asyncio
async def test_an_unsigned_packet_is_never_recorded(client, node):
    event = node()
    event["sig"] = "00" * 64
    with pytest.raises(mesh.MeshRejected):
        await mesh.ingest(event)
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM mesh_packets WHERE event_id=?",
                               (event["id"],))
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_the_hop_trail_round_trips_through_storage(client, node):
    event = node(tags=default_tags(extra=[
        ["hop_sig", "aa" * 32, "bb" * 64, "1756000000"],
        ["hop_sig", "cc" * 32, "dd" * 64, "1756000005"],
    ]))
    await mesh.ingest(event)
    packets = await mesh.recent_packets(limit=50)
    mine = [p for p in packets if p["event_id"] == event["id"]][0]
    assert len(mine["hop_trail"]) == 2
    assert mine["hop_trail"][0]["pubkey"] == "aa" * 32
    assert mine["hops_verified"] is False


# ── proof of relay ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_relay_attestation_is_recorded_against_its_subject(client, node):
    packet = node()
    await mesh.ingest(packet)
    attestation = node(kind=mesh.KIND_ATTESTATION, tags=[
        ["e", packet["id"]], ["stance", "confirm"], ["p", node.pubkey],
    ])
    result = await mesh.ingest_attestation(attestation)
    assert result["subject_event_id"] == packet["id"]
    assert [a["event_id"] for a in await mesh.attestations_for(packet["id"])] \
        == [attestation["id"]]


@pytest.mark.asyncio
async def test_an_attestation_naming_no_subject_is_refused(client, node):
    with pytest.raises(mesh.MeshRejected) as excinfo:
        await mesh.ingest_attestation(node(kind=mesh.KIND_ATTESTATION,
                                           tags=[["stance", "confirm"]]))
    assert "e-tag" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_unknown_stance_is_refused(client, node):
    with pytest.raises(mesh.MeshRejected):
        await mesh.ingest_attestation(node(kind=mesh.KIND_ATTESTATION, tags=[
            ["e", "aa" * 32], ["stance", "shrug"],
        ]))


def test_proof_of_relay_reuses_the_ecosystem_attestation_kind():
    """The mesh repo dropped its own kind 20002 for this. Minting a parallel
    one here would undo that decision."""
    from backend import nostr_kinds

    assert mesh.KIND_ATTESTATION == nostr_kinds.kind("attestation") == 1902
    assert 20002 not in nostr_kinds.REGISTRY


# ── the HTTP surface ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_ingest_endpoint_needs_no_api_key_only_a_signature(client, node):
    """Handing a shared API key to field hardware would be the weaker
    design; the event's own signature proves the node holds the key."""
    resp = await client.post("/api/meshnet/ingest", json={"event": node()})
    assert resp.status_code == 200, resp.text
    assert resp.json()["recorded"] is True


@pytest.mark.asyncio
async def test_the_ingest_endpoint_refuses_a_bad_signature(client, node):
    event = node()
    event["sig"] = "11" * 64
    resp = await client.post("/api/meshnet/ingest", json={"event": event})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_the_packets_endpoint_says_hop_trails_are_unverified(client, node):
    await mesh.ingest(node())
    resp = await client.get("/api/meshnet/packets?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hop_signatures_verified"] is False
    assert "not proof" in body["note"]


# ── Meshtastic frames, paired with the event that carries them ───────────────

@pytest.mark.asyncio
async def test_a_frame_can_be_submitted_alongside_the_event_that_describes_it(
    client, node
):
    """The firmware's bridge drops the payload when it builds the event, so
    a gateway consuming these off a relay gets routing metadata and not the
    message. This is the path that carries both."""
    import base64

    from backend.tests.test_meshtastic_frames import build_packet

    frame = build_packet(packet_id=0x1A2B3C4D, hop_start=5, hop_limit=3)
    event = node(tags=default_tags(hop=2, origin_id="!1a2b3c4d"))
    resp = await client.post("/api/meshnet/meshtastic", json={
        "event": event, "frame_b64": base64.b64encode(frame).decode(),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recorded"] is True
    assert body["frame"]["payload_bytes"] > 0
    assert body["frame"]["route_proven"] is False


@pytest.mark.asyncio
async def test_a_frame_that_contradicts_its_signed_event_is_refused(client, node):
    """A gateway may submit any bytes. What it cannot do is submit bytes
    whose packet id disagrees with the tags it already signed."""
    import base64

    from backend.tests.test_meshtastic_frames import build_packet

    frame = build_packet(packet_id=0x99999999)
    event = node(tags=default_tags(hop=2, origin_id="!1a2b3c4d"))
    resp = await client.post("/api/meshnet/meshtastic", json={
        "event": event, "frame_b64": base64.b64encode(frame).decode(),
    })
    assert resp.status_code == 422
    assert "does not match" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_the_event_survives_a_rejected_frame(client, node):
    """The event verified on its own signature and is part of the log. Only
    the frame is refused."""
    import base64

    from backend.tests.test_meshtastic_frames import build_packet

    event = node(tags=default_tags(hop=2, origin_id="!1a2b3c4d"))
    await client.post("/api/meshnet/meshtastic", json={
        "event": event,
        "frame_b64": base64.b64encode(build_packet(packet_id=0x88888888)).decode(),
    })
    packets = await mesh.recent_packets(limit=100)
    assert any(p["event_id"] == event["id"] for p in packets)
