"""HTTP ingress for radio mesh gateway nodes.

Distinct from `routers/mesh.py`, which is the Block Mesh coordination API --
agents joining blocks and negotiating commitments over the internet. This is
the *radio* mesh: LoRa, Meshtastic, Reticulum and LoRaWAN frames arriving
from `omokoda-mesh` firmware. Two different things that share a word, so
they get two prefixes: `/api/mesh` stays with the block mesh, and this takes
`/api/meshnet`.

A gateway that can reach the relay does not need this endpoint -- it
publishes there and the indexer picks the event up. This exists for the
gateway that can reach an HTTP endpoint and not a WebSocket relay, which on
a field deployment behind a captive network is the ordinary case rather than
the exception.

**There is no session authentication here, and that is deliberate.** The
event carries its own BIP-340 signature; verifying it proves the node holds
the key, which is a stronger statement than an API key would make. Handing a
shared API key to field hardware would be the weaker design.
"""
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import mesh_gateway
from .. import meshtastic_frames
from ..deps import _parse_body

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meshnet", tags=["meshnet"])
_limiter = Limiter(key_func=get_remote_address)


@router.post("/ingest")
@_limiter.limit("120/minute")
async def ingest_packet(request: Request):
    """Accept one signed mesh event (kind 20000 / 20001 / 20003)."""
    body = await _parse_body(request)
    event = body.get("event") if isinstance(body.get("event"), dict) else body
    try:
        return await mesh_gateway.ingest(event)
    except mesh_gateway.MeshRejected as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/meshtastic")
@_limiter.limit("120/minute")
async def ingest_meshtastic_frame(request: Request):
    """Accept a signed mesh event together with the raw Meshtastic frame.

    The firmware's Nostr bridge drops the payload when it converts an
    envelope to an event -- by its own comment -- so a gateway consuming
    these off a relay gets routing metadata and not the message. This
    endpoint takes the frame alongside the event and checks the two against
    each other: the gateway may submit any bytes, but not bytes whose packet
    id and hop count disagree with the tags it already signed.
    """
    body = await _parse_body(request)
    event = body.get("event")
    encoded = body.get("frame_b64") or ""
    if not isinstance(event, dict) or not encoded:
        raise HTTPException(422, "send both 'event' and 'frame_b64'")

    try:
        result = await mesh_gateway.ingest(event)
    except mesh_gateway.MeshRejected as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        frame = meshtastic_frames.decode_b64(encoded)
        detail = meshtastic_frames.check_against_event(frame, result)
    except meshtastic_frames.FrameError as exc:
        # The event is already recorded and stays recorded -- it verified on
        # its own signature. Only the frame is refused, and saying which
        # half failed is the difference between a fixable error and a
        # mysterious one.
        raise HTTPException(
            422, f"the event was recorded but the frame does not match it: {exc}"
        ) from exc

    return {**result, "frame": detail}


@router.post("/attestations")
@_limiter.limit("120/minute")
async def ingest_relay_attestation(request: Request):
    """Accept one kind 1902 proof-of-relay attestation."""
    body = await _parse_body(request)
    event = body.get("event") if isinstance(body.get("event"), dict) else body
    try:
        return await mesh_gateway.ingest_attestation(event)
    except mesh_gateway.MeshRejected as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/packets")
async def list_meshnet_packets(
    limit: int = Query(100, ge=1, le=500), network: str = Query("")
):
    """Recent radio mesh traffic.

    `hops_verified` is False on everything, and will stay that way until the
    firmware signs a hop trail -- the field is here so a consumer can tell
    the difference rather than assume proof it does not have.
    """
    packets = await mesh_gateway.recent_packets(limit=limit, network=network or "")
    return {
        "packets": packets,
        "count": len(packets),
        "networks": sorted(mesh_gateway.NETWORKS),
        "hop_signatures_verified": False,
        "note": (
            "hop trails are recorded as claimed; the firmware does not sign them yet, "
            "so they are not proof of relay"
        ),
    }


@router.get("/nodes")
async def list_meshnet_nodes(limit: int = Query(200, ge=1, le=500)):
    """Gateway nodes seen, as principals. Every one holds its own key."""
    nodes = await mesh_gateway.known_nodes(limit=limit)
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/packets/{event_id}/attestations")
async def meshnet_packet_attestations(event_id: str):
    return {"event_id": event_id,
            "attestations": await mesh_gateway.attestations_for(event_id)}
