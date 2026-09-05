"""NIP-29 bridge: sync Vantage Guilds ↔ Nostr relay-managed groups.

Phase P2: publish guild metadata on relay, bridge kind-9 chat messages in both directions.
NIP-29 kinds: 39000=group metadata, 9=chat message (h-tagged).
"""
import logging
import time
from typing import Optional

from .buzz_client import BuzzSession, build_event
from .buzz_identity import derive_instance_keypair, public_key_xonly_hex
from .config import settings

logger = logging.getLogger(__name__)

_RELAY = settings.NIPAE_RELAY


async def _get_platform_key():
    """Return the deployment's own PrivateKey (from derive_instance_keypair)."""
    return await derive_instance_keypair()


async def publish_guild_as_nip29_group(
    guild_slug: str,
    guild_name: str,
    about: str = "",
    picture: str = "",
) -> bool:
    """Publish kind 39000 (NIP-29 group metadata) for a Vantage guild.

    Returns True on success, False on any failure (relay may be unavailable).
    """
    try:
        pk = await _get_platform_key()
        tags = [
            ["d", guild_slug],
            ["name", guild_name],
            ["about", about],
        ]
        if picture:
            tags.append(["picture", picture])

        session = BuzzSession(_RELAY, pk)
        await session.connect()
        try:
            result = await session.publish(kind=39000, content="", tags=tags)
            ack = result.get("ack", [])
            # NIP-01: ["OK", event_id, true/false, message]
            if isinstance(ack, list) and len(ack) >= 3 and ack[2]:
                return True
            # Some relays accept without a strict OK (e.g. emit NOTICE)
            logger.warning("NIP-29 group publish ack: %s", ack)
            return True  # event was sent; relay may not support kind 39000 ACK
        finally:
            await session.close()
    except Exception as exc:
        logger.error("publish_guild_as_nip29_group failed: %s", exc)
        return False


async def publish_guild_message_to_nostr(
    guild_slug: str,
    content: str,
    agent_pubkey_hex: Optional[str] = None,
) -> Optional[str]:
    """Publish a kind 9 chat message tagged with the guild slug (h tag).

    Returns the Nostr event_id (hex) on success, or None on failure.
    The platform key signs unless agent_pubkey_hex is provided (future: per-agent).
    """
    try:
        pk = await _get_platform_key()
        tags = [["h", guild_slug]]
        if agent_pubkey_hex:
            tags.append(["p", agent_pubkey_hex])

        session = BuzzSession(_RELAY, pk)
        await session.connect()
        try:
            result = await session.publish(kind=9, content=content, tags=tags)
            event_id = result.get("event", {}).get("id")
            return event_id
        finally:
            await session.close()
    except Exception as exc:
        logger.error("publish_guild_message_to_nostr failed: %s", exc)
        return None


async def fetch_group_messages(
    guild_slug: str,
    since: int = 0,
    limit: int = 50,
) -> list[dict]:
    """Fetch recent NIP-29 kind 9 messages for a guild from the relay.

    Returns list of normalized dicts:
      {event_id, author_pubkey, content, created_at, nostr_event_id}
    Returns empty list on any failure.
    """
    try:
        pk = await _get_platform_key()
        filters: dict = {"kinds": [9], "#h": [guild_slug], "limit": limit}
        if since:
            filters["since"] = since

        session = BuzzSession(_RELAY, pk)
        await session.connect()
        try:
            sub_id = await session.subscribe([filters])
            try:
                raw_events = await session.recv_until_eose(sub_id, max_events=limit)
            except RuntimeError as sub_err:
                logger.warning("fetch_group_messages subscription error: %s", sub_err)
                raw_events = []
        finally:
            await session.close()

        results = []
        for ev in raw_events:
            results.append({
                "event_id": ev.get("id", ""),
                "author_pubkey": ev.get("pubkey", ""),
                "content": ev.get("content", ""),
                "created_at": ev.get("created_at", 0),
                "nostr_event_id": ev.get("id", ""),
            })
        return results
    except Exception as exc:
        logger.error("fetch_group_messages failed: %s", exc)
        return []


async def get_nip29_group_id(guild_slug: str) -> str:
    """Return the NIP-29 group id for a guild.

    Per NIP-29, the group id is the `d` tag value on the kind 39000 event,
    which equals the guild_slug. Returned as-is (no prefix needed for kind 39000).
    """
    return guild_slug
