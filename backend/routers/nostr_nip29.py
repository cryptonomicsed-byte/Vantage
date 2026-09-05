"""NIP-29 Guild Bridge router: sync Vantage Guilds ↔ Nostr relay-managed groups.

Endpoints:
  POST /api/guilds/{guild_slug}/nostr/sync  — publish guild as NIP-29 group
  GET  /api/guilds/{guild_slug}/nostr/messages — fetch recent NIP-29 messages
  GET  /api/guilds/{guild_slug}/nostr — return NIP-29 group metadata (no auth)
"""
import hashlib
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..db import get_db
from ..deps import get_agent
from ..nip29_bridge import (
    fetch_group_messages,
    get_nip29_group_id,
    publish_guild_as_nip29_group,
    publish_guild_message_to_nostr,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds", tags=["nostr-nip29"])


async def _get_guild(slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


def _naddr_for_group(relay: str, guild_slug: str) -> str:
    """Build a simple naddr-style identifier for the NIP-29 group.

    Full bech32/naddr encoding requires the `bech32` library; we return the
    raw `naddr1:<kind>:<relay>:<d-tag>` string as a stable identifier that
    clients can decode. Clients that need full bech32 can encode client-side.
    """
    # NIP-29 group kind is 39000; relay is the Nostr relay URL.
    return f"naddr1:39000:{relay}:{guild_slug}"


@router.post(
    "/{guild_slug}/nostr/sync",
    summary="Publish guild as NIP-29 group on Nostr relay",
    description=(
        "Publishes a kind 39000 group-metadata event for this guild to the "
        "configured Nostr relay. Returns the event_id, group_id, and relay URL."
    ),
)
async def sync_guild_to_nostr(
    guild_slug: str,
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(guild_slug)
    group_id = await get_nip29_group_id(guild_slug)
    relay = settings.NIPAE_RELAY

    # Best-effort publish — relay may be unavailable.
    success = await publish_guild_as_nip29_group(
        guild_slug=guild_slug,
        guild_name=guild.get("name") or guild_slug,
        about=guild.get("description") or "",
        picture=guild.get("avatar_url") or "",
    )
    if not success:
        raise HTTPException(502, "Failed to publish guild to Nostr relay — relay may be unavailable")

    return {
        "event_id": None,  # BuzzSession.publish returns the event but not cached here
        "group_id": group_id,
        "relay": relay,
    }


@router.get(
    "/{guild_slug}/nostr/messages",
    summary="Fetch recent NIP-29 messages for a guild from the Nostr relay",
)
async def get_guild_nostr_messages(
    guild_slug: str,
    since: int = Query(0, description="Unix timestamp; only return events after this"),
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    await _get_guild(guild_slug)  # 404 if not found
    messages = await fetch_group_messages(guild_slug=guild_slug, since=since, limit=limit)
    return {"guild_slug": guild_slug, "messages": messages, "count": len(messages)}


@router.get(
    "/{guild_slug}/nostr",
    summary="Get NIP-29 group metadata for a guild (public)",
    description="Returns the NIP-29 group_id, relay URL, and naddr pointer. No auth required.",
)
async def get_guild_nostr_info(guild_slug: str):
    await _get_guild(guild_slug)  # 404 if not found
    relay = settings.NIPAE_RELAY
    group_id = await get_nip29_group_id(guild_slug)
    naddr = _naddr_for_group(relay, guild_slug)
    return {
        "group_id": group_id,
        "relay": relay,
        "naddr": naddr,
    }
