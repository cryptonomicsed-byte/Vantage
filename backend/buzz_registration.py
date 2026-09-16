"""Self-service Buzz/Nostr registration for Vantage agents.

Replaced docker-exec membership grant with NIP-29 kind 9000 over WS.
"""
import json
import logging
import os

from .buzz_identity import derive_buzz_keypair, derive_instance_keypair, public_key_xonly_hex, get_owner_attestation_tag
from .buzz_client import BuzzSession
from .buzz_pairing import PUBLIC_RELAY_WS_URL as RELAY_WS_URL_PUBLIC
from .db import get_db

logger = logging.getLogger(__name__)

RELAY_WS_URL = os.environ.get("VANTAGE_RELAY_WS_URL", RELAY_WS_URL_PUBLIC)

# RELAY_CONTAINER and _docker_exec removed from this module.
# They live in nostr/adapters/buzz.py for Buzz-managed relays only.
# Re-exported here for backward compat with buzz_inbound.py and buzz_human_identity.py.
from .nostr.adapters.buzz import RELAY_CONTAINER, _docker_exec

_HARDCODED_DEFAULT_CHANNEL_ID = "bb7d6a9e-640e-475e-9deb-9e182b124388"  # secret-scan:allow -- relay channel UUID (buzz-e2e-test), an identifier

# VANTAGE_DEFAULT_GROUP_ID is authoritative. Resolved once at import time
# rather than per-call, because callers import this name directly and use it
# as a default argument value -- there is no call site at which a live lookup
# could happen. Changing the env var therefore requires a service restart.
DEFAULT_CHANNEL_ID = (
    os.environ.get("VANTAGE_DEFAULT_GROUP_ID", "").strip()
    or _HARDCODED_DEFAULT_CHANNEL_ID
)


async def get_default_channel_id() -> str:
    """Resolve the default channel/group ID.

    Priority:
    1. VANTAGE_DEFAULT_GROUP_ID env var (authoritative)
    2. First guild in DB with nostr_group_id set, if is_default exists
    3. Hardcoded fallback
    """
    env_val = os.environ.get("VANTAGE_DEFAULT_GROUP_ID", "").strip()
    if env_val:
        return env_val
    try:
        async with get_db() as db:
            cur = await db.execute("PRAGMA table_info(guilds)")
            _guild_cols = {r[1] for r in await cur.fetchall()}
            if "is_default" not in _guild_cols:
                raise LookupError("guilds.is_default does not exist")
            cur = await db.execute(
                "SELECT nostr_group_id, slug FROM guilds WHERE is_default = 1 LIMIT 1"
            )
            row = await cur.fetchone()
            if row:
                group_id = row[0] or row[1]
                if group_id:
                    return group_id
    except Exception as e:
        logger.debug("get_default_channel_id DB lookup skipped: %s", e)
    return _HARDCODED_DEFAULT_CHANNEL_ID


async def get_buzz_status(agent_id: int) -> dict:
    """Nostr identity shown even before registration."""
    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)
    async with get_db() as db:
        cur = await db.execute(
            "SELECT buzz_registered_at, buzz_joined_channels FROM agents WHERE id = ?", (agent_id,)
        )
        row = await cur.fetchone()
    registered_at = row[0] if row else None
    joined = json.loads(row[1]) if row and row[1] else []
    return {
        "pubkey": pubkey,
        "registered": bool(registered_at),
        "registered_at": registered_at,
        "joined_channels": joined,
    }


async def register_agent_on_buzz(agent_id: int) -> dict:
    from .nostr.groups import add_member as nip29_add_member

    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)
    admin_pk = await derive_instance_keypair()
    default_channel_id = await get_default_channel_id()

    # NIP-29 kind 9000 membership grant (replaces docker-exec)
    add_result = await nip29_add_member(RELAY_WS_URL, admin_pk, default_channel_id, pubkey)
    if not add_result.get("ok"):
        logger.warning(
            "register_agent_on_buzz: NIP-29 add_member not-ok for agent %d: %s",
            agent_id, add_result.get("error"),
        )
        # Not fatal -- agent can still publish; membership may already exist

    async with get_db() as db:
        cur = await db.execute("SELECT name, bio FROM agents WHERE id = ?", (agent_id,))
        agent_row = await cur.fetchone()
    agent_name, agent_bio = (agent_row or ("", ""))

    attestation = await get_owner_attestation_tag(pubkey)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        join_result = await sess.publish(9021, "", tags=[["h", default_channel_id], attestation])
        verify_result = await sess.publish(
            9, "Registered on Buzz via Vantage self-service.",
            tags=[["h", default_channel_id], attestation],
        )
        profile_content = json.dumps({
            "name": agent_name,
            "about": agent_bio or "",
            "client": "vantage-federation",
        })
        await sess.publish(0, profile_content, tags=[attestation])
        if agent_bio:
            await sess.publish(
                30175, agent_bio,
                tags=[["d", f"vantage-agent-{agent_id}"], attestation],
            )
        await sess.publish(10002, "", tags=[["r", RELAY_WS_URL_PUBLIC], attestation])
    finally:
        await sess.close()

    async with get_db() as db:
        await db.execute(
            "UPDATE agents SET buzz_registered_at = datetime('now'), "
            "buzz_joined_channels = ?, nostr_pubkey_hex = ?, buzz_persona_published = 1 WHERE id = ?",
            (json.dumps([default_channel_id]), pubkey, agent_id),
        )
        await db.commit()

    return {
        "ok": True,
        "pubkey": pubkey,
        "joined_channels": [default_channel_id],
        "relay_ack": {"join": join_result["ack"], "verify_publish": verify_result["ack"]},
    }


async def publish_relay_list(agent_id: int) -> dict:
    """Standalone NIP-65 (re-)publish for agents already registered."""
    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)
    attestation = await get_owner_attestation_tag(pubkey)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(10002, "", tags=[["r", RELAY_WS_URL_PUBLIC], attestation])
    finally:
        await sess.close()
    return {"ok": True, "pubkey": pubkey, "relay_ack": result["ack"]}
