"""agents_public.py — Unauthenticated public identity endpoints (Phase 8.2).

Exposes agent public profiles by npub or BIPON39 phrase without requiring
an X-Agent-Key header. No private vault data, private keys, or credentials
are ever included in responses.

Routes:
  GET /agents/{identifier}/public  — lookup by npub (bech32) or BIPON39 phrase
  GET /agents/{identifier}/profile.json — NIP-05 compatible profile
  GET /agents/                     — paginated public directory
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents-public"])

# Default Nostr relays — operator override via env var
_DEFAULT_RELAYS = "wss://relay.damus.io,wss://nos.lol"
_AGENT_NOSTR_RELAYS: list[str] = [
    r.strip()
    for r in os.getenv("AGENT_NOSTR_RELAYS", _DEFAULT_RELAYS).split(",")
    if r.strip()
]

# Odù names (0-255) — indexed by Odù number
_ODU_NAMES: list[str] = [
    "Ogbe", "Oyeku", "Iwori", "Odi", "Irosun", "Owonrin", "Obara", "Okanran",
    "Ogunda", "Osa", "Ika", "Oturupon", "Otura", "Irete", "Ose", "Ofun",
] + [f"Odu-{i}" for i in range(16, 256)]


def _odu_name(index: int | None) -> str:
    if index is None or not (0 <= index < len(_ODU_NAMES)):
        return "Unknown"
    return _ODU_NAMES[index]


class AgentPublicProfile(BaseModel):
    agent_id: str
    npub: str
    bipon39_phrase: str
    odu_index: int
    odu_name: str
    tier: int
    reputation: float
    capabilities: list[str]
    relay_list: list[str]
    is_online: bool
    last_seen: Optional[int]       # unix timestamp, None if never seen
    behavioral_archetype: Optional[str]
    # NEVER include: api_key, sealed_seed_hex/enc, private vault, email,
    # cognition_auth_token, omokoda_agent_key, or any credentials


def _row_to_profile(row: aiosqlite.Row) -> AgentPublicProfile:
    d = dict(row)

    # Capabilities: stored as JSON array of strings
    caps_raw = d.get("skill_badges") or "[]"
    try:
        caps = json.loads(caps_raw)
        if not isinstance(caps, list):
            caps = []
    except (ValueError, TypeError):
        caps = []

    # last_seen_at → unix timestamp
    last_seen_str = d.get("last_seen_at") or ""
    last_seen: Optional[int] = None
    if last_seen_str:
        try:
            import datetime
            dt = datetime.datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            last_seen = int(dt.timestamp())
        except Exception:
            pass

    # is_online: last seen within 15 minutes
    is_online = False
    if last_seen is not None:
        is_online = (int(time.time()) - last_seen) < 900

    odu_index = int(d.get("odu_index") or 0)

    return AgentPublicProfile(
        agent_id=str(d.get("name", "")),
        npub=str(d.get("nostr_pubkey_hex") or ""),
        bipon39_phrase=str(d.get("bipon39_phrase") or ""),
        odu_index=odu_index,
        odu_name=_odu_name(odu_index),
        tier=int(d.get("tier") or 0),
        reputation=float(d.get("reputation") or 0.0),
        capabilities=caps,
        relay_list=_AGENT_NOSTR_RELAYS,
        is_online=is_online,
        last_seen=last_seen,
        behavioral_archetype=d.get("active_profile") or None,
    )


def _is_bipon39(identifier: str) -> bool:
    """Heuristic: BIPON39 phrases are hyphen-separated uppercase words,
    e.g. FIRE-OYA-MOON-RIVER. npubs start with 'npub1' (bech32)."""
    return "-" in identifier and not identifier.startswith("npub")


async def _lookup_agent(identifier: str) -> aiosqlite.Row:
    """Resolve an agent row by npub (nostr_pubkey_hex) or BIPON39 phrase.
    Raises 404 (never 403) for unknown identifiers.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if _is_bipon39(identifier):
            row = await (await db.execute(
                """SELECT id, name, nostr_pubkey_hex, bipon39_phrase,
                          odu_index, tier, reputation, skill_badges,
                          last_seen_at, active_profile
                   FROM agents
                   WHERE bipon39_phrase = ? AND agent_status != 'revoked'""",
                (identifier,),
            )).fetchone()
        else:
            # Treat as npub / pubkey hex
            row = await (await db.execute(
                """SELECT id, name, nostr_pubkey_hex, bipon39_phrase,
                          odu_index, tier, reputation, skill_badges,
                          last_seen_at, active_profile
                   FROM agents
                   WHERE nostr_pubkey_hex = ? AND agent_status != 'revoked'""",
                (identifier,),
            )).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    return row


@router.get(
    "/{identifier}/public",
    response_model=AgentPublicProfile,
    summary="Get public agent profile by npub or BIPON39 phrase",
    description=(
        "Unauthenticated. Returns the public-safe portion of an agent's profile. "
        "Lookup by npub (bech32 hex) or BIPON39 mnemonic phrase. "
        "Returns 404 for unknown agents (never 403 — no auth required, no auth leaking)."
    ),
)
async def get_agent_public_profile(identifier: str) -> AgentPublicProfile:
    row = await _lookup_agent(identifier)
    return _row_to_profile(row)


@router.get(
    "/{identifier}/profile.json",
    summary="NIP-05 compatible profile JSON",
    description=(
        "Unauthenticated. Returns a NIP-05 compatible JSON envelope for the agent. "
        "Compatible with `/.well-known/nostr.json` lookup patterns."
    ),
)
async def get_agent_nip05_profile(identifier: str) -> dict:
    row = await _lookup_agent(identifier)
    profile = _row_to_profile(row)
    agent_mail_domain = os.getenv("AGENT_MAIL_DOMAIN", "")
    nip05_id = (
        f"{profile.agent_id}@{agent_mail_domain}"
        if agent_mail_domain
        else profile.agent_id
    )
    return {
        "names": {profile.agent_id: profile.npub},
        "relays": {profile.npub: profile.relay_list},
        "nip05": nip05_id,
        "profile": profile.model_dump(),
    }


@router.get(
    "/",
    summary="Paginated public agent directory",
    description=(
        "Unauthenticated. Returns a paginated list of agents sorted by reputation. "
        "Filter by Odù index with ?odu=42. Never returns private data."
    ),
)
async def list_agents_public(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    sort: str = Query("reputation", description="Sort field: reputation | tier | name"),
    odu: Optional[int] = Query(None, ge=0, le=255, description="Filter by Odù index"),
) -> dict:
    # Validate sort field to prevent SQL injection
    _VALID_SORTS = {"reputation": "reputation DESC", "tier": "tier DESC", "name": "name ASC"}
    order_clause = _VALID_SORTS.get(sort, "reputation DESC")

    offset = (page - 1) * limit
    clauses = ["agent_status != 'revoked'", "is_external = 0"]
    params: list = []

    if odu is not None:
        clauses.append("odu_index = ?")
        params.append(odu)

    where = "WHERE " + " AND ".join(clauses)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        count_row = await (await db.execute(
            f"SELECT COUNT(*) FROM agents {where}", params
        )).fetchone()
        total = count_row[0] if count_row else 0

        rows = await (await db.execute(
            f"""SELECT id, name, nostr_pubkey_hex, bipon39_phrase,
                       odu_index, tier, reputation, skill_badges,
                       last_seen_at, active_profile
                FROM agents {where}
                ORDER BY {order_clause}
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )).fetchall()

    agents = [_row_to_profile(r).model_dump() for r in rows]
    return {
        "agents": agents,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }
