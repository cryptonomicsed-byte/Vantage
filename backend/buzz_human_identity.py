"""Section 1.4 of the buzz_vantage_blueprint: humans get their own real
Buzz/Nostr identity (same sealed-seed pattern as agents, distinct
principal namespace), and agent_grants changes translate into the
human's own relay membership role -- never the agent's key being
"shared" with the human.

Role mapping is RELAY-WIDE, not per-channel/per-agent -- found live: a
plain "member" pubkey publishing kind:9030 (add-member) itself is
rejected outright ("invalid: actor not authorized: must be admin or
owner"), so there is no authenticated per-channel path available to an
ordinary agent session. The only real mechanism is the same root-level
`buzz-admin add-member --role` CLI registration itself already uses.
Since that sets a relay-wide role, a human's actual role is recomputed as
the MAX across ALL their active grants (admin_full anywhere -> relay
"admin", else "member") every time any one grant changes -- see
_max_role_across_active_grants. The grant itself (which scopes a human
has on which agent) still lives in Vantage's own agent_grants table as
the real source of truth; this is a *mirror* of that state into buzz's
relay-wide membership model, coarser than Vantage's own per-agent scopes
until this relay grows real per-channel role authorization.
"""
import asyncio
import json
import logging
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_human_buzz_keypair, public_key_xonly_hex, get_owner_attestation_tag
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


async def _max_role_across_active_grants(human_id: int) -> Optional[str]:
    """The relay's membership model (confirmed live: buzz-admin add-member
    --role) is RELAY-WIDE, not per-channel -- there is no authenticated way
    for a plain member to grant another pubkey a per-channel role via
    kind:9030 (found live: "invalid: actor not authorized: must be admin
    or owner" -- a regular member publishing 9030 itself is rejected,
    correctly, by the relay). So a human's actual buzz role must be the
    MAX across all their active grants, not scoped to one agent at a time.
    Returns None if the human has no active grants at all (caller should
    then remove relay membership entirely rather than downgrade)."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT scopes FROM agent_grants WHERE human_id=? AND revoked_at IS NULL", (human_id,)
        )
        rows = await cur.fetchall()
    if not rows:
        return None
    all_scopes: set[str] = set()
    for (scopes_json,) in rows:
        try:
            all_scopes.update(json.loads(scopes_json))
        except Exception:
            pass
    return "admin" if "admin_full" in all_scopes else "member"


async def _set_human_relay_role(human_id: int, role: Optional[str]) -> None:
    status = await get_human_buzz_status(human_id)
    if not status["registered"]:
        if role is None:
            return
        await register_human_on_buzz(human_id)
        status = await get_human_buzz_status(human_id)
    pubkey = status["pubkey"]
    if role is None:
        code, out, err = await _docker_exec("remove-member", "--pubkey", pubkey)
        if code != 0 and "not found" not in (out + err).lower():
            raise RuntimeError(f"buzz-admin remove-member failed: {err.strip() or out.strip()}")
    else:
        code, out, err = await _docker_exec("add-member", "--pubkey", pubkey, "--role", role)
        if code != 0 and "already" not in (out + err).lower() and "exists" not in (out + err).lower():
            raise RuntimeError(f"buzz-admin add-member --role {role} failed: {err.strip() or out.strip()}")


async def sync_grant_to_buzz(human_id: int, agent_id: int, scopes: list[str]) -> None:
    """A grant change recomputes the human's relay-wide role from the MAX
    across ALL their active grants (see _max_role_across_active_grants) --
    `agent_id`/`scopes` identify which grant just changed, triggering the
    recompute, not a per-channel target. Never raises -- a failed mirror
    shouldn't break the actual Vantage-side grant, which is the real
    source of truth."""
    try:
        role = await _max_role_across_active_grants(human_id)
        await _set_human_relay_role(human_id, role)
        logger.info("buzz_human_identity: synced human_id=%s (grant on agent_id=%s changed) -> relay role=%s", human_id, agent_id, role)
    except Exception as e:
        logger.warning("buzz_human_identity: sync_grant_to_buzz failed human_id=%s agent_id=%s: %s", human_id, agent_id, e)


async def revoke_grant_from_buzz(human_id: int, agent_id: int) -> None:
    """Called AFTER the grant's revoked_at is already set, so
    _max_role_across_active_grants naturally excludes it -- recomputes
    from whatever grants remain, or removes relay membership entirely if
    none do."""
    try:
        role = await _max_role_across_active_grants(human_id)
        await _set_human_relay_role(human_id, role)
        logger.info("buzz_human_identity: synced human_id=%s (grant on agent_id=%s revoked) -> relay role=%s", human_id, agent_id, role)
    except Exception as e:
        logger.warning("buzz_human_identity: revoke_grant_from_buzz failed human_id=%s agent_id=%s: %s", human_id, agent_id, e)
