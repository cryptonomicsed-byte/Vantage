"""Section 15 of the buzz_vantage_blueprint: Vantage jail/suspend mirrors
to real buzz moderation commands (kind:9040 ban / 9041 unban, confirmed
via buzz-sdk's builders.rs). Community-global commands, tags [["p",
target_pubkey], ["expiration", ...], ["reason", ...]].

Uses the INSTANCE identity to publish (the most-privileged identity
Vantage holds), since these are moderation-authority actions, not
something the jailed agent's own key should ever be trusted to
self-execute. Real, stated constraint: even the instance identity is
only a plain relay "member" (added via buzz-admin add-member --role
member at bootstrap, same as every agent) -- if the relay requires
admin/owner role for these commands too (matching what was found live
for kind:9030 in Section 1.4), this will surface as the same
"actor not authorized" rejection, not silently pretend to work."""
import logging

from .buzz_client import BuzzSession
from .buzz_identity import derive_instance_keypair, derive_buzz_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_WS_URL

logger = logging.getLogger(__name__)

KIND_MODERATION_BAN = 9040
KIND_MODERATION_UNBAN = 9041


async def mirror_jail(agent_id: int, reason: str = "jailed by Vantage admin") -> dict:
    """Section 15.1: Vantage jail -> buzz ban. Fire-and-forget; the
    Vantage-side jail_mode flag is always the real, authoritative
    enforcement -- this mirror is defense-in-depth at the identity seam,
    not a replacement."""
    try:
        target_pubkey = public_key_xonly_hex(await derive_buzz_keypair(agent_id))
        pk = await derive_instance_keypair()
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            result = await sess.publish(KIND_MODERATION_BAN, "", tags=[["p", target_pubkey], ["reason", reason]])
        finally:
            await sess.close()
        if not result["ack"][2]:
            logger.info("buzz_moderation: ban mirror not applied for agent_id=%s: %s", agent_id, result["ack"])
            return {"ok": False, "error": str(result["ack"])}
        return {"ok": True}
    except Exception as e:
        logger.warning("buzz_moderation: mirror_jail failed for agent_id=%s: %s", agent_id, e)
        return {"ok": False, "error": str(e)}


async def mirror_unjail(agent_id: int) -> dict:
    try:
        target_pubkey = public_key_xonly_hex(await derive_buzz_keypair(agent_id))
        pk = await derive_instance_keypair()
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            result = await sess.publish(KIND_MODERATION_UNBAN, "", tags=[["p", target_pubkey]])
        finally:
            await sess.close()
        if not result["ack"][2]:
            logger.info("buzz_moderation: unban mirror not applied for agent_id=%s: %s", agent_id, result["ack"])
            return {"ok": False, "error": str(result["ack"])}
        return {"ok": True}
    except Exception as e:
        logger.warning("buzz_moderation: mirror_unjail failed for agent_id=%s: %s", agent_id, e)
        return {"ok": False, "error": str(e)}
