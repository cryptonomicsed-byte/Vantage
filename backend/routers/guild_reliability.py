"""P3-06: bondhive reliability for guild agents in a trading space.

GET  /api/guilds/{slug}/trading-spaces/{space_id}/reliability
     Any guild member. Optional ?agent_id= narrows to one agent (an agent with
     no outcomes reads as the neutral 50).
POST /api/guilds/{slug}/trading-spaces/{space_id}/reliability/outcomes
     Space owner only; records a resolved success/failure/fraud for a guild
     member (not themself). No automatic recording is wired: nothing writes
     outcomes in the background.

Math: backend/guild_reliability.py (port of bondhive core/src/score.rs).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from .. import guild_reliability as gr
from ..db import get_db
from ..deps import get_agent
from .trading_space import _assert_guild_member, _get_space

router = APIRouter(prefix="/api/guilds/{slug}/trading-spaces", tags=["trading-space"])


@router.get("/{space_id}/reliability")
async def get_reliability(
    slug: str,
    space_id: str,
    agent_id: Optional[int] = Query(None),
    agent: dict = Depends(get_agent),
):
    await _assert_guild_member(slug, agent)
    await _get_space(space_id, slug)
    async with get_db() as db:
        rows = await gr.space_reliability(db, slug, space_id, agent_id=agent_id)
        await db.commit()
    return {
        "guild_slug": slug,
        "space_id": space_id,
        "model": "bondhive/score.rs",
        "neutral_score": gr.NEUTRAL_SCORE,
        "half_life_days": gr.REPUTATION_HALF_LIFE_SECS / 86_400.0,
        "deltas": dict(gr.OUTCOME_DELTAS),
        "agents": rows,
    }


@router.post("/{space_id}/reliability/outcomes")
async def record_reliability_outcome(
    slug: str,
    space_id: str,
    agent_id: int = Form(...),
    outcome: str = Form(...),
    ref: str = Form(""),
    note: str = Form(""),
    agent: dict = Depends(get_agent),
):
    guild_id = await _assert_guild_member(slug, agent)
    space = await _get_space(space_id, slug)
    if agent["id"] != space.get("created_by_agent_id"):
        raise HTTPException(403, "Only the space owner may record reliability outcomes")
    if agent_id == agent["id"]:
        raise HTTPException(403, "The space owner may not record outcomes for themself")
    if outcome not in gr.OUTCOMES:
        raise HTTPException(422, f"outcome must be one of {list(gr.OUTCOMES)}")
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM guild_members WHERE guild_id=? AND agent_id=?", (guild_id, agent_id)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Agent is not a member of this guild")
        row = await gr.record_outcome(
            db, slug, space_id, agent_id, outcome,
            ref=ref[:200] or None, note=note[:500] or None, recorded_by_agent_id=agent["id"],
        )
        await db.commit()
    return {"ok": True, **row}
