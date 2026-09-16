"""Guild agent roster with presence — exposed as MCP tools."""
import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException

from .. import coordination as coord
from ..db import get_db
from ..deps import get_agent
from .. import presence
from ..presence import STATES

router = APIRouter(prefix="/api/guilds/{guild_slug}/roster", tags=["roster"])


async def _get_guild(slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


@router.get(
    "",
    summary="Get guild agent roster",
    description="List all agents in a guild with their roles and current presence states.",
)
async def get_roster(guild_slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Try joining via principals table; fall back gracefully if it doesn't exist
        try:
            async with db.execute(
                """SELECT gm.agent_id, gm.agent_name, gm.role,
                          COALESCE(a.bio, '') AS bio,
                          COALESCE(a.avatar_url, '') AS avatar_url,
                          COALESCE(pp.state, 'offline') AS presence_state,
                          pp.source AS presence_source
                   FROM guild_members gm
                   LEFT JOIN agents a ON a.id = gm.agent_id
                   LEFT JOIN principals pr ON pr.agent_id = gm.agent_id
                   LEFT JOIN principal_presence pp ON pp.principal_id = pr.id AND pp.channel_id IS NULL
                   WHERE gm.guild_id=?
                   ORDER BY gm.role DESC, gm.agent_name""",
                (guild["id"],),
            ) as cur:
                rows = await cur.fetchall()
        except Exception:
            # Fallback without presence if principals table doesn't exist yet
            async with db.execute(
                """SELECT gm.agent_id, gm.agent_name, gm.role,
                          COALESCE(a.bio, '') AS bio,
                          COALESCE(a.avatar_url, '') AS avatar_url,
                          'offline' AS presence_state,
                          NULL AS presence_source
                   FROM guild_members gm
                   LEFT JOIN agents a ON a.id = gm.agent_id
                   WHERE gm.guild_id=?
                   ORDER BY gm.role DESC, gm.agent_name""",
                (guild["id"],),
            ) as cur:
                rows = await cur.fetchall()
    return {"roster": [dict(r) for r in rows]}


@router.patch(
    "/{agent_name}/presence",
    summary="Update agent presence in guild",
    description="Update the calling agent's presence state. Valid states: available, thinking, working, blocked, needs_review, offline.",
)
async def update_presence(
    guild_slug: str,
    agent_name: str,
    state: str = Form(..., description="available|thinking|working|blocked|needs_review|offline"),
    agent: dict = Depends(get_agent),
):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only update your own presence")
    if state not in STATES:
        raise HTTPException(422, f"Invalid state. Must be one of: {', '.join(STATES)}")
    await _get_guild(guild_slug)
    # Routed through the one place presence is written (see backend/presence.py)
    # rather than a second raw-SQL INSERT here -- otherwise this path silently
    # skips the relay mirror and the `source` bookkeeping the other one does.
    principal = await coord.get_or_create_agent_principal(agent["id"])
    result = await presence.set_state(
        principal_id=principal["id"], state=state, source="declared",
    )
    return {"status": "ok", "state": result["state"]}
