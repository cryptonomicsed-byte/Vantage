"""Nostr-based federation peer discovery, riding the Buzz relay Vantage
already connects to for agent chat (buzz_acp_bridge.py).

An instance publishes a signed, self-updating announcement -- kind 30166,
NIP-33 parameterized-replaceable (d-tagged by a stable instance slug, so
re-announcing replaces the old event instead of accumulating duplicates).
This is a custom kind, not a registered NIP -- documented here rather than
claimed as official; NIP-33's 30000-39999 range exists exactly for
app-specific events like this, and switching kinds later (if a better-fit
official one shows up) is a one-line change since nothing else depends on
the number.

Other instances (or this same one) can then discover peers by querying the
relay for kind 30166 instead of only relying on manual POST /federation/peers
registration -- which stays working unchanged as the explicit-add fallback.

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

FEDERATION_ANNOUNCE_KIND = 30166  # custom, NIP-33 parameterized-replaceable range
INSTANCE_SLUG = "vantage-main"
RELAY_WS_URL = "ws://localhost:3000"


async def _instance_manifest() -> dict:
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM agents")
        row = await cur.fetchone()
        agent_count = row[0] if row else 0
    return {
        "name": "Vantage",
        "url": settings.PUBLIC_URL,
        "agent_count": agent_count,
        "client": "vantage",
    }


async def publish_federation_announcement() -> dict:
    """Publishes/replaces this instance's kind:30166 announcement. Returns
    the publish ack from the relay."""
    pk = await derive_instance_keypair()
    manifest = await _instance_manifest()
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            FEDERATION_ANNOUNCE_KIND,
            json.dumps(manifest),
            tags=[["d", INSTANCE_SLUG], ["client", "vantage"], ["url", settings.PUBLIC_URL]],
        )
        return result["ack"]
    finally:
        await sess.close()


async def discover_peers_via_buzz(limit: int = 50) -> int:
    """Queries the relay for other instances' kind:30166 announcements and
    upserts them into federation_peers (discovered_via='buzz'), same
    auto-verify-by-pinging pattern manual registration already uses --
    doesn't blindly trust the announcement, still has to earn reputation
    through the normal gossip loop. Returns the number of new peers found."""
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
