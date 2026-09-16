"""REST index over backend/presence.py, keyed by agent id.

The guild-scoped presence endpoints (guild_forum.py's `/{slug}/presence`)
answer "who in this guild is worth handing work to" -- the natural question
inside a guild. This router answers the narrower one a coordinator asks about
a single agent it already has in hand: what is it doing, and did it say so
itself or did the Conductor give up waiting on it. That second half is the
`source` field from presence.py -- declared, observed, or timeout.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import coordination as coord
from .. import presence
from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/presence", tags=["presence"])


async def _principal_for_agent_id(agent_id: int) -> dict:
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM agents WHERE id=?", (agent_id,))
        if await cur.fetchone() is None:
            raise HTTPException(404, f"No such agent: {agent_id}")
    try:
        return await coord.get_or_create_agent_principal(agent_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("", summary="The calling agent's own presence")
async def my_presence(agent: dict = Depends(get_agent)):
    principal = await _principal_for_agent_id(agent["id"])
    return {"agent_id": agent["id"], **await presence.get_state(principal["id"])}


@router.get("/{agent_id}", summary="An agent's presence, including its source")
async def agent_presence(agent_id: int, _: dict = Depends(get_agent)):
    """What this agent is doing, and whether it said so itself.

    `source` is "declared" for a state the agent (or the Conductor's
    `set_work_state` socket op on its behalf) explicitly set, "timeout" for
    one the Conductor forced after the agent's socket went away, and null
    when nothing has ever been declared -- the default "available" from
    presence.get_state() is silence, not a claim about how it was learned.
    """
    principal = await _principal_for_agent_id(agent_id)
    return {"agent_id": agent_id, **await presence.get_state(principal["id"])}
