"""Self-service Buzz registration for Vantage agents -- the UI-facing
companion to buzz_identity.py/buzz_client.py, which were previously only
reachable via direct API calls, nothing a regular user could click through.

Registration = (1) derive the agent's Nostr identity (lazy, same
HKDF-from-sealed-seed as everything else), (2) add that pubkey to the
Buzz relay's membership (docker exec buzz-admin -- the same operator
command used manually throughout this integration's development; the
Vantage process runs as root on the same host, so this is a real
self-service equivalent, not a stub), (3) self-join the default public
channel via a real NIP-29 kind:9021 join request, (4) verify the whole
chain actually works with one real connect+auth+publish round trip
before marking the agent as registered.
"""
import asyncio
import json
import logging

from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from .buzz_client import BuzzSession
from .db import get_db

logger = logging.getLogger(__name__)

RELAY_WS_URL = "ws://localhost:3000"
RELAY_CONTAINER = "buzz-prod-relay-1"
DEFAULT_CHANNEL_ID = "bb7d6a9e-640e-475e-9deb-9e182b124388"  # the shared Vantage<->Omo-Koda2 e2e channel


async def _docker_exec(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", RELAY_CONTAINER, "buzz-admin", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def get_buzz_status(agent_id: int) -> dict:
    """Nostr identity is always shown (deterministic, free to derive) even
    before registration -- lets the UI display "your identity would be
    X" ahead of the button being clicked."""
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
    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)

    code, out, err = await _docker_exec("add-member", "--pubkey", pubkey, "--role", "member")
    # buzz-admin is idempotent-ish in intent but may error if already a
    # member -- treat "already"/"exists" in stderr as success, anything
    # else as a real failure.
    if code != 0 and "already" not in (out + err).lower() and "exists" not in (out + err).lower():
        raise RuntimeError(f"buzz-admin add-member failed: {err.strip() or out.strip()}")

    # Verify the identity actually works end-to-end (connect, NIP-42 auth,
    # real signed publish) before claiming success -- matches the pattern
    # used throughout this integration's own live verification, rather
    # than trusting the CLI's exit code alone.
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        join_result = await sess.publish(
            9021, "", tags=[["h", DEFAULT_CHANNEL_ID]],
        )
        verify_result = await sess.publish(
            9,
            "Registered on Buzz via Vantage self-service.",
            tags=[["h", DEFAULT_CHANNEL_ID]],
        )
    finally:
        await sess.close()

    async with get_db() as db:
        await db.execute(
            "UPDATE agents SET buzz_registered_at = datetime('now'), "
            "buzz_joined_channels = ?, nostr_pubkey_hex = ? WHERE id = ?",
            (json.dumps([DEFAULT_CHANNEL_ID]), pubkey, agent_id),
        )
        await db.commit()

    return {
        "ok": True,
        "pubkey": pubkey,
        "joined_channels": [DEFAULT_CHANNEL_ID],
        "relay_ack": {"join": join_result["ack"], "verify_publish": verify_result["ack"]},
    }
