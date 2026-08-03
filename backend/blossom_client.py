"""Blossom media upload client (BUD-01/BUD-02) -- Section 11 of the
integration blueprint. Confirmed real and live on this relay by reading
buzz-relay/crates/buzz-relay/src/api/media.rs directly (not assumed from
the blueprint's summary alone): `PUT /media/upload`, 50MB limit, requires
a kind:24242 Blossom auth event with tags [t=upload, x=<sha256>,
expiration=<unix ts>], base64-encoded in an `Authorization: Nostr <b64>`
header, verified BEFORE the body is buffered (BUD-11 hash binding).

Same relay host as the WS door (ws://localhost:3000 -> http://localhost:3000
for this HTTP-only surface), same per-agent Buzz keypair used for the
mirror -- one identity, no separate media credential.
"""
import base64
import hashlib
import json
import logging
import time
from typing import Optional

import httpx

from .buzz_client import build_event
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL

logger = logging.getLogger(__name__)

RELAY_HTTP_URL = RELAY_WS_URL.replace("ws://", "http://").replace("wss://", "https://")


async def upload_media(agent_id: int, data: bytes, mime_type: str) -> Optional[dict]:
    """Returns the relay's BlobDescriptor dict ({url, sha256, size, type,
    uploaded}) on success, None on any failure -- never raises, matching
    this codebase's existing fire-and-forget-adjacent media/mirror
    conventions (the broadcast itself must not fail just because the
    Blossom mirror did)."""
    sha256_hex = hashlib.sha256(data).hexdigest()
    pk = await derive_buzz_keypair(agent_id)
    auth_event = build_event(
        pk,
        kind=24242,
        content="Upload blob",
        tags=[["t", "upload"], ["x", sha256_hex], ["expiration", str(int(time.time()) + 300)]],
    )
    auth_header = "Nostr " + base64.urlsafe_b64encode(
        json.dumps(auth_event, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.put(
                f"{RELAY_HTTP_URL}/media/upload",
                content=data,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": mime_type,
                    # BUD-11: mandatory alongside the auth event's own `x`
                    # tag -- the relay checks this HEADER matches an `x` tag
                    # in the auth event body; found live (401, generic
                    # "authentication failed") that this is required
                    # separately, not implied by the tag alone.
                    "X-SHA-256": sha256_hex,
                },
            )
        if r.status_code in (200, 201):
            return r.json()
        logger.warning("blossom upload failed for agent_id=%s: %s %s", agent_id, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("blossom upload error for agent_id=%s: %s", agent_id, e)
    return None
