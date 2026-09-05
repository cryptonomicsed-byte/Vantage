"""NIP-98 HTTP Auth verification — pure utility module.

NIP-98 spec: https://github.com/nostr-protocol/nips/blob/master/98.md

The client creates a kind-27235 Nostr event with:
  - tags: ["u", <url>], ["method", <METHOD>], optionally ["payload", <sha256hex>]
  - created_at: unix timestamp (must be within 60s of server time)
Signs it with their secp256k1 key (BIP340 schnorr via NIP-01), base64-encodes
the JSON event, and sends as: Authorization: Nostr <base64>

Verification steps (this module):
  1. Decode base64 -> JSON event
  2. Verify kind == 27235
  3. Verify created_at within 60s of now
  4. Verify "u" tag matches expected URL
  5. Verify "method" tag matches expected method (case-insensitive)
  6. Verify BIP340 schnorr signature over event_id (sha256 of NIP-01 canonical JSON)

This module has no FastAPI dependency — import it anywhere.
"""
import hashlib
import json
import time
from typing import Optional

from coincurve import PublicKeyXOnly

# NIP-98 HTTP Auth kind
KIND_HTTP_AUTH = 27235

# Maximum clock skew allowed (seconds)
_MAX_AGE_SECONDS = 60


def get_event_id(event: dict) -> str:
    """Compute NIP-01 event id: sha256 of canonical serialization.

    Canonical form: '[0,"<pubkey>",<created_at>,<kind>,<tags>,"<content>"]'
    Returns the hex event id.
    """
    canonical = json.dumps(
        [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event.get("content", ""),
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_nip98_event(
    event: dict,
    expected_url: str,
    expected_method: str,
) -> bool:
    """Verify a NIP-98 HTTP Auth event.

    Returns True iff every check passes. Returns False (never raises)
    on any validation failure so callers can safely map False -> 401.

    Args:
        event: The decoded Nostr event dict.
        expected_url: The full URL of the HTTP request being authenticated.
        expected_method: The HTTP method of the request (e.g. "GET", "POST").
    """
    try:
        # 1. Kind check
        if event.get("kind") != KIND_HTTP_AUTH:
            return False

        # 2. Timestamp freshness
        created_at = event.get("created_at")
        if not isinstance(created_at, int):
            return False
        if abs(time.time() - created_at) > _MAX_AGE_SECONDS:
            return False

        # 3. Required fields present
        pubkey = event.get("pubkey")
        sig = event.get("sig")
        if not pubkey or not sig:
            return False

        # 4. Tags: find "u" and "method"
        tags = event.get("tags", [])
        u_tag: Optional[str] = None
        method_tag: Optional[str] = None
        for tag in tags:
            if isinstance(tag, list) and len(tag) >= 2:
                if tag[0] == "u":
                    u_tag = tag[1]
                elif tag[0] == "method":
                    method_tag = tag[1]

        if u_tag is None or method_tag is None:
            return False

        # 5. URL match (exact)
        if u_tag != expected_url:
            return False

        # 6. Method match (case-insensitive)
        if method_tag.upper() != expected_method.upper():
            return False

        # 7. Verify event id consistency
        computed_id = get_event_id(event)
        event_id = event.get("id", "")
        if computed_id != event_id:
            return False

        # 8. Verify BIP340 schnorr signature
        # PublicKeyXOnly accepts the 32-byte x-coordinate of the secp256k1 point
        pubkey_bytes = bytes.fromhex(pubkey)
        sig_bytes = bytes.fromhex(sig)
        msg_bytes = bytes.fromhex(event_id)  # event_id IS the message (sha256 digest)

        pk = PublicKeyXOnly(pubkey_bytes)
        return pk.verify(sig_bytes, msg_bytes)

    except Exception:
        # Any decode error, key format error, etc. -> treat as invalid
        return False
