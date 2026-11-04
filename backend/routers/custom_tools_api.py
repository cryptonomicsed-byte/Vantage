"""REST surface for user-defined integrations and skills."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import custom_tools
from ..deps import get_agent

router = APIRouter(prefix="/api/agents/me", tags=["mcp"])


class ActionIn(BaseModel):
    name: str
    description: Optional[str] = ""
    method: str = "GET"
    path: str = "/"
    params: list[dict] = Field(default_factory=list)


class IntegrationIn(BaseModel):
    name: str
    base_url: str
    actions: list[ActionIn] = Field(default_factory=list)
    auth_header: Optional[str] = ""      # e.g. "Authorization" or "X-Api-Key"
    secret: Optional[str] = ""           # sealed on write, never returned


class SkillIn(BaseModel):
    name: str
    description: Optional[str] = ""
    instructions: str


@router.get("/integrations")
async def list_integrations(agent: dict = Depends(get_agent)):
    await custom_tools.ensure_tables()
    return {"integrations": await custom_tools.list_integrations(int(agent["id"]))}


@router.post("/integrations")
async def create_integration(body: IntegrationIn, agent: dict = Depends(get_agent)):
    await custom_tools.ensure_tables()
    try:
        return await custom_tools.create_integration(
            int(agent["id"]), body.name, body.base_url,
            [a.model_dump() for a in body.actions],
            body.auth_header or "", body.secret or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/integrations/{integration_id}")
async def delete_integration(integration_id: int, agent: dict = Depends(get_agent)):
    ok = await custom_tools.delete_integration(int(agent["id"]), integration_id)
    if not ok:
        raise HTTPException(404, "integration not found")
    return {"deleted": integration_id}


@router.get("/skills")
async def list_skills(agent: dict = Depends(get_agent)):
    await custom_tools.ensure_tables()
    return {"skills": await custom_tools.list_skills(int(agent["id"]))}


@router.post("/skills")
async def create_skill(body: SkillIn, agent: dict = Depends(get_agent)):
    await custom_tools.ensure_tables()
    try:
        return await custom_tools.create_skill(
            int(agent["id"]), body.name, body.description or "", body.instructions)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: int, agent: dict = Depends(get_agent)):
    ok = await custom_tools.delete_skill(int(agent["id"]), skill_id)
    if not ok:
        raise HTTPException(404, "skill not found")
    return {"deleted": skill_id}
