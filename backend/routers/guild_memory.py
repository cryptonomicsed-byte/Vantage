"""Guild memory — per-agent key-value store scoped to a guild, exposed as MCP tools."""
import secrets

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException

from ..db import get_db
from ..deps import get_agent

router = APIRouter(prefix="/api/guilds/{guild_slug}/memory", tags=["memory"])

_VALID_VISIBILITY = {"agent", "guild", "public"}


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
    summary="Read own guild memory",
    description="Get the calling agent's memory entries for this guild.",
)
async def read_own_memory(guild_slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_memory WHERE guild_id=? AND agent_id=? ORDER BY updated_at DESC",
            (guild["id"], agent["id"]),
        ) as cur:
            rows = await cur.fetchall()
    return {"entries": [dict(r) for r in rows]}


@router.get(
    "/shared",
    summary="Read shared guild memory",
    description="Get guild-visible and public memory entries across all agents in this guild.",
)
async def read_shared_memory(guild_slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM guild_memory
               WHERE guild_id=? AND visibility IN ('guild','public')
               ORDER BY updated_at DESC""",
            (guild["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return {"entries": [dict(r) for r in rows]}


@router.get(
    "/{key}",
    summary="Read guild memory key",
    description="Get a specific memory key for the calling agent in this guild.",
)
async def read_memory_key(guild_slug: str, key: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_memory WHERE guild_id=? AND agent_id=? AND key=?",
            (guild["id"], agent["id"], key),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Memory key not found")
    return dict(row)


@router.put(
    "/{key}",
    summary="Write guild memory key",
    description="Upsert a memory entry. visibility: agent (default, private), guild (all guild members), public.",
)
async def write_memory_key(
    guild_slug: str,
    key: str,
    value: str = Form(..., description="Value as any JSON string"),
    visibility: str = Form("agent", description="agent|guild|public"),
    agent: dict = Depends(get_agent),
):
    if visibility not in _VALID_VISIBILITY:
        raise HTTPException(422, f"visibility must be one of: {', '.join(_VALID_VISIBILITY)}")
    guild = await _get_guild(guild_slug)
    entry_id = secrets.token_hex(16)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_memory (id, guild_id, agent_id, agent_name, key, value, visibility)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(guild_id, agent_id, key) DO UPDATE SET
                 value=excluded.value,
                 visibility=excluded.visibility,
                 updated_at=datetime('now')""",
            (entry_id, guild["id"], agent["id"], agent["name"], key, value, visibility),
        )
        await db.commit()
    return {"status": "ok", "key": key}


@router.delete(
    "/{key}",
    summary="Delete guild memory key",
    description="Delete the calling agent's memory entry for a key.",
)
async def delete_memory_key(guild_slug: str, key: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(guild_slug)
    async with get_db() as db:
        async with db.execute(
            "DELETE FROM guild_memory WHERE guild_id=? AND agent_id=? AND key=?",
            (guild["id"], agent["id"], key),
        ) as cur:
            deleted = cur.rowcount
        await db.commit()
    if not deleted:
        raise HTTPException(404, "Memory key not found")
    return {"status": "deleted", "key": key}
