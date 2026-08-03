"""Section 1.4 of the buzz_vantage_blueprint: humans get their own real
Buzz/Nostr identity (same sealed-seed pattern as agents, distinct
principal namespace), and an agent_grants row translates into buzz
channel membership for that human's own pubkey -- never the agent's key
being "shared" with the human.

Role mapping (blueprint's own vocabulary, Section 5): admin_full grants
the buzz "admin" role in the agent's channels; every other scope
combination (view_state/copilot_chat/trading_execute/wallet_manage)
maps to plain "member" -- this relay's kind:9030 add-member only
recognizes member/admin/owner, so finer Vantage-side scopes aren't all
individually representable as a buzz role today. The grant itself (which
scopes a human has) still lives in Vantage's own agent_grants table as
the real source of truth; this is a *mirror* of that state into buzz's
membership model, not a replacement for it.

Current topology note: today every agent only belongs to the one shared,
open MAIN_FEED channel (no restricted per-agent channels exist yet --
those land in P4/rooms and P3/guilds). So a 9030/9031 add/remove-member
event on that open channel is real, signed, and lands on the relay, but
has limited practical access-control effect until private per-agent
channels exist. Wiring it now means grant changes already flow into buzz
membership state with zero further work once those channels exist.
"""
import asyncio
import json
import logging

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, derive_human_buzz_keypair, public_key_xonly_hex, get_owner_attestation_tag
from .buzz_registration import RELAY_WS_URL, RELAY_CONTAINER
from .db import get_db

logger = logging.getLogger(__name__)


async def _docker_exec(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", RELAY_CONTAINER, "buzz-admin", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def get_human_buzz_status(human_id: int) -> dict:
    pk = await derive_human_buzz_keypair(human_id)
    pubkey = public_key_xonly_hex(pk)
    async with get_db() as db:
        cur = await db.execute("SELECT buzz_registered_at FROM humans WHERE id = ?", (human_id,))
        row = await cur.fetchone()
    return {"pubkey": pubkey, "registered": bool(row and row[0]), "registered_at": row[0] if row else None}


async def register_human_on_buzz(human_id: int) -> dict:
    """Same real 3-step pattern as register_agent_on_buzz: derive
    identity, add relay membership, verify with a real connect+auth+
    publish round trip. Idempotent -- calling again on an already-
    registered human just re-verifies and re-publishes the profile."""
    pk = await derive_human_buzz_keypair(human_id)
    pubkey = public_key_xonly_hex(pk)

    code, out, err = await _docker_exec("add-member", "--pubkey", pubkey, "--role", "member")
    if code != 0 and "already" not in (out + err).lower() and "exists" not in (out + err).lower():
        raise RuntimeError(f"buzz-admin add-member failed: {err.strip() or out.strip()}")

    async with get_db() as db:
        cur = await db.execute("SELECT email, display_name FROM humans WHERE id = ?", (human_id,))
        row = await cur.fetchone()
    display_name = (row[1] if row else "") or (row[0].split("@")[0] if row else "")

    attestation = await get_owner_attestation_tag(pubkey)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        profile_content = json.dumps({"name": display_name, "client": "vantage-federation", "kind": "human"})
        await sess.publish(0, profile_content, tags=[attestation])
    finally:
        await sess.close()

    async with get_db() as db:
        await db.execute(
            "UPDATE humans SET buzz_pubkey_hex = ?, buzz_registered_at = datetime('now') WHERE id = ?",
            (pubkey, human_id),
        )
        await db.commit()

    return {"ok": True, "pubkey": pubkey}


async def _agent_channels_and_session(agent_id: int) -> tuple[list[str], BuzzSession]:
    async with get_db() as db:
        cur = await db.execute("SELECT buzz_joined_channels FROM agents WHERE id = ?", (agent_id,))
        row = await cur.fetchone()
    channels = json.loads(row[0]) if row and row[0] else []
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    return channels, sess


async def sync_grant_to_buzz(human_id: int, agent_id: int, scopes: list[str]) -> None:
    """Mirrors an agent_grants row into buzz channel membership for the
    human's own pubkey. Never raises -- a failed mirror shouldn't break
    the actual Vantage-side grant, which is the real source of truth."""
    try:
        status = await get_human_buzz_status(human_id)
        if not status["registered"]:
            await register_human_on_buzz(human_id)
        human_pk = await derive_human_buzz_keypair(human_id)
        human_pubkey = public_key_xonly_hex(human_pk)
        role = "admin" if "admin_full" in scopes else "member"

        channels, sess = await _agent_channels_and_session(agent_id)
        if not channels:
            await sess.close()
            return
        try:
            for channel in channels:
                await sess.publish(9030, "", tags=[["h", channel], ["p", human_pubkey], ["role", role]])
        finally:
            await sess.close()
        logger.info("buzz_human_identity: synced grant human_id=%s agent_id=%s role=%s to %d channel(s)", human_id, agent_id, role, len(channels))
    except Exception as e:
        logger.warning("buzz_human_identity: sync_grant_to_buzz failed human_id=%s agent_id=%s: %s", human_id, agent_id, e)


async def revoke_grant_from_buzz(human_id: int, agent_id: int) -> None:
    try:
        human_pk = await derive_human_buzz_keypair(human_id)
        human_pubkey = public_key_xonly_hex(human_pk)
        channels, sess = await _agent_channels_and_session(agent_id)
        if not channels:
            await sess.close()
            return
        try:
            for channel in channels:
                await sess.publish(9031, "", tags=[["h", channel], ["p", human_pubkey]])
        finally:
            await sess.close()
        logger.info("buzz_human_identity: revoked grant human_id=%s agent_id=%s from %d channel(s)", human_id, agent_id, len(channels))
    except Exception as e:
        logger.warning("buzz_human_identity: revoke_grant_from_buzz failed human_id=%s agent_id=%s: %s", human_id, agent_id, e)
