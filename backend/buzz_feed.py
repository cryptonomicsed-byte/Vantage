"""Buzz activity feed aggregation. ADDON: extends the existing single-
channel ACP bridge (buzz_acp_bridge.py) pattern to read across every
channel this agent has joined, for a real cross-channel feed view. Read-
only; publishes nothing, so there is no additive write-side surface here
to worry about breaking anything.
"""
from typing import Optional

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL

KIND_STREAM_MESSAGE = 9
KIND_MEMBER_ADDED_NOTIFICATION = 44100


async def get_joined_channels(agent_id: int) -> list:
    """This agent's joined_channels, from the same DB column
    buzz_registration.py already maintains -- no new state, just reading
    the existing column back."""
    from .db import get_db
    async with get_db() as db:
        cur = await db.execute("SELECT buzz_joined_channels FROM agents WHERE id = ?", (agent_id,))
        row = await cur.fetchone()
    if not row or not row[0]:
        return []
    import json
    return json.loads(row[0])


async def get_feed(agent_id: int, limit: int = 100) -> list:
    """Aggregated kind:9 stream messages across every channel this agent
    has joined, newest first."""
    channel_ids = await get_joined_channels(agent_id)
    if not channel_ids:
        return []

    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        sub_id = await sess.subscribe([
            {"kinds": [KIND_STREAM_MESSAGE], "#h": channel_ids, "limit": limit}
        ])
        events = await sess.recv_until_eose(sub_id, max_events=limit)
    finally:
        await sess.close()

    events.sort(key=lambda e: e["created_at"], reverse=True)
    out = []
    for ev in events:
        channel_id = next((t[1] for t in ev.get("tags", []) if t[0] == "h"), None)
        out.append({
            "channel_id": channel_id,
            "pubkey": ev["pubkey"],
            "content": ev["content"],
            "created_at": ev["created_at"],
        })
    return out
