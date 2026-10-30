import uuid, time, json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.db import get_db
from backend.deps import get_agent

router = APIRouter(tags=["guild-skills"])

class SkillCreate(BaseModel):
    name: str
    mode: str = ""
    mcp_tool_tag: str = ""
    fallback_router: str = ""
    capability_required: str = ""

class SkillInvoke(BaseModel):
    args: dict = {}

@router.post("/api/guilds/{slug}/skills")
async def register_skill(slug: str, body: SkillCreate, agent=Depends(get_agent)):
    async with get_db() as db:
        skill_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO guild_skills (id, guild_slug, name, mode, mcp_tool_tag, fallback_router,
               capability_required, created_by_agent_id, enabled, created_ts)
               VALUES (?,?,?,?,?,?,?,?,1,?)""",
            (skill_id, slug, body.name, body.mode, body.mcp_tool_tag, body.fallback_router,
             body.capability_required, agent.get("id"), int(time.time()))
        )
        await db.commit()
        return {"id": skill_id, "name": body.name, "guild_slug": slug}

@router.get("/api/guilds/{slug}/skills")
async def list_skills(slug: str, mode: Optional[str] = None):
    async with get_db() as db:
        if mode:
            rows = await (await db.execute(
                "SELECT * FROM guild_skills WHERE guild_slug=? AND mode=? AND enabled=1",
                (slug, mode)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM guild_skills WHERE guild_slug=? AND enabled=1", (slug,)
            )).fetchall()
        return [dict(r) for r in rows]

@router.post("/api/guilds/{slug}/skills/{skill_id}/invoke")
async def invoke_skill(slug: str, skill_id: str, body: SkillInvoke, agent=Depends(get_agent)):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM guild_skills WHERE id=? AND guild_slug=? AND enabled=1",
            (skill_id, slug)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Skill not found")
        skill = dict(row)
        if skill.get("capability_required"):
            caps = agent.get("capabilities", [])
            if isinstance(caps, str):
                try:
                    caps = json.loads(caps)
                except Exception:
                    caps = []
            if skill["capability_required"] not in caps:
                return {"ok": False, "error": "agent lacks required capability", "required": skill["capability_required"]}
        if skill.get("mcp_tool_tag"):
            return {"ok": True, "skill_id": skill_id, "routed_to": "mcp", "tool_tag": skill["mcp_tool_tag"], "args": body.args}
        elif skill.get("fallback_router"):
            return {"ok": True, "skill_id": skill_id, "routed_to": "native", "router": skill["fallback_router"], "args": body.args}
        return {"ok": True, "skill_id": skill_id, "routed_to": "none"}

@router.delete("/api/guilds/{slug}/skills/{skill_id}")
async def delete_skill(slug: str, skill_id: str, agent=Depends(get_agent)):
    async with get_db() as db:
        await db.execute("UPDATE guild_skills SET enabled=0 WHERE id=? AND guild_slug=?", (skill_id, slug))
        await db.commit()
        return {"ok": True}
