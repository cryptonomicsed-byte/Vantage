"""Capability registry and task routing API endpoints.

Routes:
    GET  /api/agents/me/capabilities       — my registered capabilities
    POST /api/agents/me/capabilities       — register/update my capabilities
    GET  /api/agents/capabilities          — all agents' capabilities (directory)
    GET  /api/tasks/{task_id}/candidates   — ranked agents for a task
    POST /api/tasks/{task_id}/route        — auto-route task to best agent
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent
from ..capability_registry import (
    get_agent_capabilities,
    get_all_capabilities,
    register_capabilities,
    update_availability,
)
from ..task_router import auto_assign, route_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["capabilities"])


# ── Request/response models ───────────────────────────────────────────────────

class CapabilityRegistration(BaseModel):
    capabilities: List[str]
    tools: List[str] = []
    runtime: str = "vantage-derived"
    availability: Optional[str] = None


class AvailabilityUpdate(BaseModel):
    availability: str


# ── Agent capability endpoints ────────────────────────────────────────────────

@router.get("/api/agents/me/capabilities")
async def get_my_capabilities(agent: dict = Depends(get_agent)):
    """Return the calling agent's registered capabilities."""
    record = await get_agent_capabilities(agent["id"])
    if not record:
        return {
            "agent_id": agent["id"],
            "agent_name": agent.get("name"),
            "capabilities": [],
            "tools": [],
            "runtime": "vantage-derived",
            "availability": "offline",
            "trust_level": 1,
            "reputation": 0.5,
        }
    return record


@router.post("/api/agents/me/capabilities")
async def set_my_capabilities(
    body: CapabilityRegistration,
    agent: dict = Depends(get_agent),
):
    """Register or update the calling agent's capabilities."""
    await register_capabilities(
        agent_id=agent["id"],
        capabilities=body.capabilities,
        tools=body.tools,
        runtime=body.runtime,
        agent_name=agent.get("name"),
    )
    if body.availability:
        try:
            await update_availability(agent["id"], body.availability)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    record = await get_agent_capabilities(agent["id"])
    return {"status": "ok", **record}


@router.get("/api/agents/capabilities")
async def list_all_capabilities(agent: dict = Depends(get_agent)):
    """Return the full agent capability directory."""
    all_caps = await get_all_capabilities()
    return {"agents": all_caps, "count": len(all_caps)}


# ── Task routing endpoints ────────────────────────────────────────────────────

@router.get("/api/tasks/{task_id}/candidates")
async def get_task_candidates(
    task_id: int,
    capabilities: str = "",
    agent: dict = Depends(get_agent),
):
    """Return ranked candidate agents for a task.

    Query param `capabilities` is a comma-separated list of required capability strings.
    Example: GET /api/tasks/42/candidates?capabilities=research,code.execute
    """
    required = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
    candidates = await route_task(task_id, required)
    # Remove internal _match_count from response or rename it
    for c in candidates:
        c["match_count"] = c.pop("_match_count", 0)
    return {"task_id": task_id, "candidates": candidates, "count": len(candidates)}


@router.post("/api/tasks/{task_id}/route")
async def route_task_endpoint(
    task_id: int,
    body: dict = None,
    agent: dict = Depends(get_agent),
):
    """Auto-route a task to the best capable agent.

    Body (optional JSON):
        {"capabilities": ["research", "code.execute"]}

    Returns the best-match agent or 404 if no capable agent is available.
    """
    if body is None:
        body = {}
    required = body.get("capabilities", [])
    if not isinstance(required, list):
        raise HTTPException(status_code=422, detail="'capabilities' must be a list of strings")

    best = await auto_assign(task_id, required)
    if best is None:
        raise HTTPException(
            status_code=404,
            detail=f"No capable agent found for task {task_id} with capabilities {required}",
        )
    best["match_count"] = best.pop("_match_count", 0)
    return {"task_id": task_id, "assigned_agent": best}
