"""Nostr-based federation peer discovery, riding the Buzz relay Vantage
already connects to for agent chat (buzz_acp_bridge.py).

Kind choice, corrected after a live 401 ("restricted: unknown event kind"):
this relay enforces a STRICT kind allowlist (see
buzz-relay/src/handlers/ingest.rs's classify_kind match), not open custom
kinds -- a NIP-33-range custom kind (originally chosen: 30166) is rejected
outright, and kind:10100 ("KIND_AGENT_PROFILE" -- despite the name) is
actually Buzz-specific channel_add_policy state, not a general manifest
slot, so repurposing it would misuse a real feature. The correct vehicle
on THIS relay is kind:0 (standard NIP-01 profile metadata) -- explicitly
generic, explicitly supported (NOSTR.md: "User profiles (kind:0) [ok] NIP-01
metadata"), and its handler only extracts known fields (name/about/picture/
nip05), ignoring unrecognized ones -- confirmed safe to carry a custom
"client":"vantage-federation" marker + "url" field alongside them.

Other instances (or this same one) discover peers by querying the relay for
kind:0 profiles carrying that marker, instead of only relying on manual
POST /federation/peers registration -- which stays working unchanged as
the explicit-add fallback.

Separately, publishes a real kind:1 note into a public channel -- the
"discovery/social" play: Buzz's own user base can organically stumble onto
Vantage the same way they'd notice any other real participant.
"""
import json
import logging
from typing import Optional

from .buzz_identity import derive_instance_keypair, public_key_xonly_hex
from .buzz_client import BuzzSession, build_event
from .config import settings
from .db import get_db

logger = logging.getLogger(__name__)

FEDERATION_ANNOUNCE_KIND = 0  # NIP-01 profile metadata -- see module docstring for why
FEDERATION_MARKER = "vantage-federation"
INSTANCE_SLUG = "vantage-main"
RELAY_WS_URL = "ws://localhost:3000"


async def _instance_manifest() -> dict:
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM agents")
        row = await cur.fetchone()
        agent_count = row[0] if row else 0
    return {
        # Standard NIP-01 profile fields (relay syncs these to its users table)
        "name": "Vantage",
        "about": f"Agent economy platform -- registry, skill marketplace, "
                 f"trading, creation pipelines. {agent_count} agents.",
        # Custom fields, ignored by the relay's kind:0 handler (only known
        # fields are extracted) but readable by our own discovery query.
        "client": FEDERATION_MARKER,
        "url": settings.PUBLIC_URL,
        "agent_count": agent_count,
    }


async def publish_federation_announcement() -> dict:
    """Publishes/replaces this instance's kind:0 profile, carrying the
    federation manifest in custom fields. Returns the publish ack."""
    pk = await derive_instance_keypair()
    manifest = await _instance_manifest()
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(FEDERATION_ANNOUNCE_KIND, json.dumps(manifest), tags=[])
        return result["ack"]
    finally:
        await sess.close()


async def discover_peers_via_buzz(limit: int = 50) -> int:
    """Queries the relay for other instances' kind:0 profiles carrying the
    vantage-federation marker and upserts them into federation_peers
    (discovered_via='buzz'), same auto-verify-by-pinging pattern manual
    registration already uses -- doesn't blindly trust the announcement,
    still has to earn reputation through the normal gossip loop. Returns
    the number of new peers found."""
    pk = await derive_instance_keypair()
    my_pubkey = public_key_xonly_hex(pk)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    new_count = 0
    try:
        sub = await sess.subscribe([{"kinds": [FEDERATION_ANNOUNCE_KIND], "limit": limit}])
        events = await sess.recv_until_eose(sub)
        for event in events:
            if event["pubkey"] == my_pubkey:
                continue  # skip our own announcement
            try:
                manifest = json.loads(event["content"])
            except Exception:
                continue
            if manifest.get("client") != FEDERATION_MARKER:
                continue  # a normal user/agent profile, not a Vantage instance
            url = str(manifest.get("url", "")).strip().rstrip("/")
            if not url or url == settings.PUBLIC_URL.rstrip("/"):
                continue
            name = str(manifest.get("name", ""))[:100] or url
            async with get_db() as db:
                cur = await db.execute(
                    "INSERT INTO federation_peers (url, name, status, reputation, nostr_pubkey, discovered_via) "
                    "VALUES (?,?,'unknown',0.5,?,'buzz') "
                    "ON CONFLICT (url) DO UPDATE SET nostr_pubkey=excluded.nostr_pubkey "
                    "WHERE federation_peers.nostr_pubkey IS NULL",
                    (url, name, event["pubkey"]),
                )
                await db.commit()
                if cur.rowcount and cur.rowcount > 0:
                    new_count += 1
    finally:
        await sess.close()
    return new_count


async def publish_social_intro(channel_id: Optional[str] = None) -> dict:
    """A real, human-readable kind:1 (or channel kind:9 if channel_id is
    given) post introducing Vantage -- the actual discovery/distribution
    play: real Buzz users organically noticing a real participant, not
    just a machine-readable manifest nobody's client renders."""
    pk = await derive_instance_keypair()
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    content = (
        "Vantage here — an agent economy platform (agent registry, skill "
        "marketplace, trading, creation pipelines) now discoverable over Nostr/Buzz. "
        f"{settings.PUBLIC_URL}"
    )
    try:
        if channel_id:
            result = await sess.publish(9, content, tags=[["h", channel_id]])
        else:
            result = await sess.publish(1, content)
        return result["ack"]
    finally:
        await sess.close()
