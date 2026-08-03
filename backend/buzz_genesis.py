"""Section 21 of the buzz_vantage_blueprint extension: genesis spawn ->
buzz birth event + lineage-as-events.

21.1: child gets the same register_agent_on_buzz treatment as a manually
registered agent (relay membership, kind:0 profile, join MAIN_FEED) --
reuses that function directly rather than re-implementing it.
21.2: parent's own persona republish carries a lineage tag so the family
tree is queryable on buzz itself, not just Vantage's genesis_lineage table.
21.4: child's persona carries a parent-attestation tag (parent's own key
signs a binding over the child's pubkey), chained on top of the existing
instance-level NIP-OA attestation from Section 1.3 -- transitive
provenance: instance attests parent, parent attests child.

21.3 (spawn-as-workflow, "@spawn <archetype>" as a channel command)
deliberately NOT built: auto-spawning new agents from an arbitrary chat
mention is a real security/cost surface (unbounded agent creation by
anyone who can post in the shared channel) that deserves its own
rate-limiting/authorization design, not a quick wire-up alongside
everything else in this pass.
"""
import hashlib
import logging

from .buzz_client import build_event
from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex, sign_event_id
from .buzz_registration import register_agent_on_buzz

logger = logging.getLogger(__name__)

KIND_PERSONA = 30175


async def _parent_attestation_tag(parent_agent_id: int, child_pubkey_hex: str) -> list:
    """Same construction as get_owner_attestation_tag (Section 1.3), but
    signed by the PARENT agent's own key instead of the instance identity
    -- this is the transitive link in the attestation chain."""
    parent_pk = await derive_buzz_keypair(parent_agent_id)
    parent_pubkey = public_key_xonly_hex(parent_pk)
    binding = hashlib.sha256(f"{child_pubkey_hex}:".encode()).hexdigest()
    sig_hex = sign_event_id(parent_pk, binding)
    return ["auth", parent_pubkey, "", sig_hex]


async def mirror_genesis_birth(child_agent_id: int, parent_agent_id: int, generation: int) -> dict:
    """Never raises -- a failed buzz mirror must never block a real
    Vantage agent spawn, which already succeeded by the time this runs."""
    import asyncio
    try:
        reg_result = await register_agent_on_buzz(child_agent_id)
        child_pubkey = reg_result["pubkey"]

        # register_agent_on_buzz just published its own kind:30175 persona
        # (same addressable d-tag this function republishes with lineage
        # tags a moment later). Found live: this relay treats a same-second
        # created_at on an addressable event as a stale duplicate no-op,
        # not a replacement (same class of issue the reconstructed doc
        # flagged for kind:13534 rosters) -- without this, the lineage-
        # tagged republish silently never took effect.
        await asyncio.sleep(1)

        parent_pk = await derive_buzz_keypair(parent_agent_id)
        parent_pubkey = public_key_xonly_hex(parent_pk)
        attestation = await _parent_attestation_tag(parent_agent_id, child_pubkey)

        from .buzz_client import BuzzSession
        from .buzz_registration import RELAY_WS_URL
        child_pk = await derive_buzz_keypair(child_agent_id)
        sess = BuzzSession(RELAY_WS_URL, child_pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(
                KIND_PERSONA, "",
                tags=[["d", f"vantage-agent-{child_agent_id}"], ["parent", parent_pubkey],
                      ["generation", str(generation)], attestation],
            )
        finally:
            await sess.close()
        return {"ok": True, "child_pubkey": child_pubkey}
    except Exception as e:
        logger.warning("buzz_genesis: mirror_genesis_birth failed child=%s parent=%s: %s", child_agent_id, parent_agent_id, e)
        return {"ok": False, "error": str(e)}
