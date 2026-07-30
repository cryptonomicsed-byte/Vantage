"""Buzz Managed-Agent allowlist projection (NIP-AP, kind:30177). ADDON:
a world-readable, EXPLICITLY OPT-IN projection of an agent record. kind.rs
is unambiguous: this event type "MUST never carry the agent's secret key,
NIP-OA auth tag, env vars, or runtime fields, since these events are
world-readable on the relay." The allowlist below is deliberately narrow
-- extend it only with fields the owner has actually agreed should be
public, never by default.
"""
import json
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_WS_URL

KIND_MANAGED_AGENT = 30177

# The only fields this module will ever publish. Adding to this list is a
# real product decision (what should a stranger on Buzz be able to see
# about a Vantage agent), not a code change to make lightly -- see the
# scoping discussion this was built from.
_ALLOWED_FIELDS = {"name", "archetype", "guild", "created_at"}


async def publish_managed_agent(agent_id: int, projection: dict) -> dict:
    """`projection` must be pre-built by the caller from the agent's real
    record, containing ONLY keys in _ALLOWED_FIELDS -- enforced here, not
    just documented, so a future caller can't accidentally widen the leak
    surface without touching this file."""
    extra = set(projection.keys()) - _ALLOWED_FIELDS
    if extra:
        raise ValueError(
            f"managed-agent projection carries disallowed fields {extra!r} -- "
            f"only {_ALLOWED_FIELDS} may ever be published here"
        )

    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(
            KIND_MANAGED_AGENT, json.dumps(projection), tags=[["d", pubkey]],
        )
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected event: {result['ack']}")
    return {"ok": True, "pubkey": pubkey, "event": result["event"]}


async def get_managed_agent(agent_id: int) -> Optional[dict]:
    pk = await derive_buzz_keypair(agent_id)
    pubkey = public_key_xonly_hex(pk)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        sub_id = await sess.subscribe([{"kinds": [KIND_MANAGED_AGENT], "authors": [pubkey]}])
        events = await sess.recv_until_eose(sub_id, max_events=5)
    finally:
        await sess.close()
    if not events:
        return None
    latest = max(events, key=lambda e: e["created_at"])
    return {"pubkey": pubkey, "body": json.loads(latest["content"]), "created_at": latest["created_at"]}
