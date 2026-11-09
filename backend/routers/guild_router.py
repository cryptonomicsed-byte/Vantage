"""P4-10: guild router endpoints (see backend/guild_router.py)."""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from .. import guild_router as gr
from ..deps import get_agent

router = APIRouter(tags=["guild-router"])


@router.post("/api/guilds/{slug}/trading-spaces/{space_id}/tasks/{task_id}/intel")
async def submit_intel(slug: str, space_id: str, task_id: str, record: Any = Body(...),
                       agent: dict = Depends(get_agent)):
    """A guild agent files an intel_record/1 on a task it holds the claim on."""
    return await gr.submit(slug, space_id, task_id, record, agent)


@router.get("/api/guilds/{slug}/trading-spaces/{space_id}/intel")
async def space_intel(slug: str, space_id: str, limit: int = Query(50, ge=1, le=500),
                      agent: dict = Depends(get_agent)):
    from .trading_space import _assert_guild_member, _get_space
    await _assert_guild_member(slug, agent)
    await _get_space(space_id, slug)
    return await gr.recent(space_id, limit)


@router.get("/api/guild-router/status")
async def router_status():
    """Lanes (with who may post), configured schedules and problems. No records."""
    scheds, probs = gr.load_schedules()
    return {"enabled": gr.enabled(), "schema": gr.SCHEMA, "lanes": gr.lanes_view(),
            "schedules": scheds, "schedule_problems": probs}
