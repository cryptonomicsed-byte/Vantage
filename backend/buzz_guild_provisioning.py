"""Section 5.1 of the buzz_vantage_blueprint: guild create -> a real
tenant community on the relay via its operator API.

REAL, VERIFIED BLOCKER (not assumed): `POST /operator/communities`
requires NIP-98 HTTP auth from a pubkey in `RELAY_OPERATOR_PUBKEYS`
(buzz-relay/src/api/operator.rs's authorize_operator_request), AND a
configured `RELAY_OPERATOR_API_ORIGIN` -- confirmed via `docker exec` and
`docker inspect` that NEITHER is set on this relay deployment at all.
This is a relay-operator-level config change (who is allowed to create
new tenant communities on this deployment), not something Vantage's own
backend can grant itself -- the code below is real and will work the
moment an operator adds this instance's identity to
RELAY_OPERATOR_PUBKEYS, but cannot be live-verified end-to-end until then.
"""
import hashlib
import json
import logging
import time

import httpx

from .buzz_client import build_event
from .buzz_identity import derive_instance_keypair
from .buzz_pairing import PUBLIC_RELAY_HTTP_URL

logger = logging.getLogger(__name__)

OPERATOR_ENDPOINT = f"{PUBLIC_RELAY_HTTP_URL}/operator/communities"


def _nip98_auth_header(pk, method: str, url: str, body: bytes = b"") -> str:
    """NIP-98 HTTP Auth: a kind:27235 event tagged with the exact URL +
    method (and a payload hash for requests with a body), base64-encoded
    into the Authorization header. Same signing primitives as every other
    event in this codebase (build_event), just a different kind/tag shape."""
    import base64
    tags = [["u", url], ["method", method.upper()]]
    if body:
        tags.append(["payload", hashlib.sha256(body).hexdigest()])
    event = build_event(pk, kind=27235, content="", tags=tags)
    return "Nostr " + base64.b64encode(json.dumps(event).encode()).decode()


async def provision_guild_community(guild_slug: str, initial_owner_pubkey_hex: str) -> dict:
    """Real attempt, real error surfaced -- never pretends success. Returns
    {"ok": True, "community_id": ...} on success, or {"ok": False, "error":
    ...} with the relay's actual rejection reason (e.g. "not a relay
    operator") so the caller/operator knows exactly what to fix."""
    pk = await derive_instance_keypair()
    host = f"{guild_slug}.communities.buzz.xyz"
    body = json.dumps({"host": host, "initial_owner_pubkey": initial_owner_pubkey_hex}).encode()
    auth = _nip98_auth_header(pk, "POST", OPERATOR_ENDPOINT, body)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                OPERATOR_ENDPOINT, content=body,
                headers={"Authorization": auth, "Content-Type": "application/json"},
            )
        if r.status_code == 200:
            data = r.json()
            return {"ok": True, "community_id": data.get("community_id") or data.get("id"), "raw": data}
        return {"ok": False, "status": r.status_code, "error": r.text[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
