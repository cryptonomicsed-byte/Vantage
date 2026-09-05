"""NIP-98 HTTP Auth gateway and Nostr identity lookup.

Endpoints:
  GET  /api/nostr/challenge         — one-time nonce for NIP-98 auth
  POST /api/nostr/verify            — verify a NIP-98 auth event
  GET  /api/nostr/identity/{pubkey} — look up which Vantage agent owns a pubkey
"""
import base64
import json
import logging
import secrets
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent
from ..nip98 import verify_nip98_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nostr", tags=["nostr"])

# In-memory nonce store: {nonce: issued_at_unix_float}
# Single-process deployment — acceptable for V1. Nonces expire after 90s
# (well beyond the 60s NIP-98 window) and are consumed on first use.
_NONCE_TTL = 90.0
_nonce_store: dict[str, float] = {}


def _issue_nonce() -> str:
    """Create and register a fresh one-time nonce."""
    nonce = secrets.token_hex(32)
    _nonce_store[nonce] = time.time()
    # Opportunistic GC: evict expired nonces on each issue
    cutoff = time.time() - _NONCE_TTL
    expired = [k for k, v in _nonce_store.items() if v < cutoff]
    for k in expired:
        _nonce_store.pop(k, None)
    return nonce


def _consume_nonce(nonce: str) -> bool:
    """Check nonce validity and remove it (one-time use). Returns False if
    nonce is unknown or expired."""
    issued_at = _nonce_store.pop(nonce, None)
    if issued_at is None:
        return False
    if time.time() - issued_at > _NONCE_TTL:
        return False
    return True


# ── Pydantic models ────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    event: dict


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/challenge")
async def get_challenge():
    """Return a one-time nonce that a client can embed in a NIP-98 event's
    content or tags to prove liveness (optional step; the nonce is advisory —
    NIP-98 timestamp freshness is the mandatory anti-replay mechanism)."""
    nonce = _issue_nonce()
    return {
        "nonce": nonce,
        "expires_in": int(_NONCE_TTL),
        "hint": (
            "Include this nonce in your NIP-98 kind-27235 event content or "
            "a ['nonce', '<value>'] tag, then POST the signed event to "
            "/api/nostr/verify."
        ),
    }


@router.post("/verify")
async def verify_nostr_auth(body: VerifyRequest, request: Request):
    """Verify a NIP-98 HTTP Auth event.

    Accepts: { "event": { ...kind-27235 event... } }

    The event's "u" tag must match the request URL that triggered this call
    (or the URL in the event itself — we trust the event's own declared URL
    since NIP-98 is a bearer-token scheme; callers should pass the URL they
    intend to auth for).

    Returns: { "valid": bool, "pubkey": str | null, "agent_id": int | null }
    """
    event = body.event

    # Extract the URL the client claims to be authenticating
    declared_url: str | None = None
    declared_method: str | None = None
    for tag in event.get("tags", []):
        if isinstance(tag, list) and len(tag) >= 2:
            if tag[0] == "u":
                declared_url = tag[1]
            elif tag[0] == "method":
                declared_method = tag[1]

    if not declared_url or not declared_method:
        return {"valid": False, "pubkey": None, "agent_id": None}

    valid = verify_nip98_event(event, declared_url, declared_method)
    if not valid:
        return {"valid": False, "pubkey": None, "agent_id": None}

    pubkey = event.get("pubkey", "")

    # Look up whether this pubkey belongs to a known Vantage agent
    agent_id: int | None = None
    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT id FROM agents WHERE nostr_pubkey_hex = ?", (pubkey,)
            )).fetchone()
        if row:
            agent_id = row["id"]
    except Exception as exc:
        logger.warning("nostr_auth verify: DB lookup failed: %s", exc)

    return {"valid": True, "pubkey": pubkey, "agent_id": agent_id}


@router.get("/identity/{pubkey_hex}")
async def get_nostr_identity(pubkey_hex: str):
    """Look up which Vantage agent owns the given Nostr pubkey (hex).

    Returns agent info if found, 404 otherwise.
    """
    if len(pubkey_hex) != 64:
        raise HTTPException(status_code=422, detail="pubkey_hex must be 64 hex characters")

    try:
        bytes.fromhex(pubkey_hex)
    except ValueError:
        raise HTTPException(status_code=422, detail="pubkey_hex is not valid hex")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT id, name, bio, avatar_url, nostr_pubkey_hex, created_at
               FROM agents
               WHERE nostr_pubkey_hex = ?""",
            (pubkey_hex,),
        )).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No Vantage agent found for pubkey {pubkey_hex}",
        )

    return {
        "agent_id": row["id"],
        "name": row["name"],
        "bio": row["bio"],
        "avatar_url": row["avatar_url"],
        "nostr_pubkey_hex": row["nostr_pubkey_hex"],
        "created_at": row["created_at"],
    }
