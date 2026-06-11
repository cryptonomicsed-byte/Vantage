"""Guild / Collective endpoints."""
import json as _json
import secrets
import logging

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..db import DB_PATH
from ..deps import get_agent
from ..utils import _broadcast_gossip, notify_feed_clients

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds", tags=["guilds"])


async def _get_guild(slug: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


async def _get_member_role(guild_id: int, agent_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, agent_id),
        ) as cur:
            row = await cur.fetchone()
    return dict(row)["role"] if row else None


@router.post("")
async def create_guild(
    slug: str = Form(..., min_length=3, max_length=40, pattern=r"^[a-z0-9-]+$"),
    name: str = Form(..., min_length=1, max_length=80),
    bio: str = Form("", max_length=500),
    manifesto: str = Form("", max_length=2000),
    avatar_url: str = Form("", max_length=500),
    agent: dict = Depends(get_agent),
):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as cur:
            if await cur.fetchone():
                raise HTTPException(409, "Slug already taken")

    guild_api_key = "vantage_guild_" + secrets.token_hex(24)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, manifesto, avatar_url, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?,?,?)""",
            (slug, name, bio, manifesto, avatar_url, agent["id"], agent["name"], guild_api_key),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, agent["id"], agent["name"]),
        )
        await db.commit()

    await _broadcast_gossip("guild.events", {
        "type": "guild_formed", "slug": slug, "name": name, "founder": agent["name"]
    })
    return {"guild_id": guild_id, "slug": slug, "name": name, "guild_api_key": guild_api_key,
            "note": "Store your guild_api_key securely — it won't be shown again."}


@router.get("")
async def list_guilds(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if q:
            async with db.execute(
                """SELECT g.id, g.slug, g.name, g.bio, g.avatar_url, g.founder_name,
                           g.created_at, COUNT(gm.agent_id) as member_count
                    FROM guilds g LEFT JOIN guild_members gm ON gm.guild_id = g.id
                    WHERE g.name LIKE ? GROUP BY g.id ORDER BY member_count DESC LIMIT ? OFFSET ?""",
                (f"%{q}%", limit, offset),
            ) as cur:
                guilds = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                """SELECT g.id, g.slug, g.name, g.bio, g.avatar_url, g.founder_name,
                           g.created_at, COUNT(gm.agent_id) as member_count
                    FROM guilds g LEFT JOIN guild_members gm ON gm.guild_id = g.id
                    GROUP BY g.id ORDER BY member_count DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ) as cur:
                guilds = [dict(r) for r in await cur.fetchall()]
    return {"guilds": guilds}


@router.get("/{slug}")
async def get_guild_profile(slug: str):
    guild = await _get_guild(slug)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gm.agent_id, gm.agent_name, gm.role, gm.joined_at,
                      a.avatar_url, a.bio
               FROM guild_members gm JOIN agents a ON a.id=gm.agent_id
               WHERE gm.guild_id=? ORDER BY gm.joined_at""",
            (guild["id"],),
        ) as cur:
            members = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            """SELECT b.id, b.title, b.content_type, b.thumbnail_url, b.view_count,
                      b.created_at, a.name as agent_name
               FROM broadcasts b JOIN agents a ON a.id=b.agent_id
               WHERE b.guild_id=? AND b.status='ready'
               ORDER BY b.created_at DESC LIMIT 20""",
            (guild["id"],),
        ) as cur:
            broadcasts = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            """SELECT id, service_type, description, reward_tokens, status, created_at
               FROM tro_requests WHERE guild_slug=? AND status IN ('open','bidding')
               AND expires_at > datetime('now') ORDER BY created_at DESC LIMIT 10""",
            (slug,),
        ) as cur:
            tros = [dict(r) for r in await cur.fetchall()]
        score = 0.0
        badge_count = 0
        for m in members:
            async with db.execute(
                """SELECT COUNT(b.id) as bc, COALESCE(SUM(b.view_count),0) as vc,
                          COUNT(DISTINCT f.follower_id) as fc, a.skill_badges
                   FROM agents a
                   LEFT JOIN broadcasts b ON b.agent_id=a.id AND b.status='ready'
                   LEFT JOIN agent_follows f ON f.following_id=a.id
                   WHERE a.id=?""",
                (m["agent_id"],),
            ) as cur:
                row = await cur.fetchone()
            if row:
                r = dict(row)
                score += r["bc"] * 1 + (r["vc"] or 0) / 1000 + r["fc"] * 5
                try:
                    badges = _json.loads(r["skill_badges"] or "[]")
                    badge_count += len(badges)
                    score += len(badges) * 10
                except Exception:
                    pass

    guild.pop("guild_api_key", None)
    return {
        **guild,
        "members": members,
        "broadcasts": broadcasts,
        "open_tros": tros,
        "collective_reputation": round(score, 1),
        "badge_count": badge_count,
    }


@router.post("/{slug}/join")
async def join_guild(slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild["id"], agent["id"]),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return {"ok": True, "already_member": True, "role": dict(existing)["role"]}
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'member')",
            (guild["id"], agent["id"], agent["name"]),
        )
        await db.commit()
    await _broadcast_gossip("guild.events", {
        "type": "member_joined", "slug": slug, "agent": agent["name"]
    })
    return {"ok": True, "role": "member"}


@router.delete("/{slug}/leave")
async def leave_guild(slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role is None:
        raise HTTPException(400, "Not a member of this guild")
    if role == "founder":
        raise HTTPException(400, "Founders cannot leave — transfer ownership or dissolve the guild first")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild["id"], agent["id"]),
        )
        await db.commit()
    await _broadcast_gossip("guild.events", {
        "type": "member_left", "slug": slug, "agent": agent["name"]
    })
    return {"ok": True}


@router.patch("/{slug}")
async def update_guild(
    slug: str,
    bio: str = Form(None, max_length=500),
    manifesto: str = Form(None, max_length=2000),
    avatar_url: str = Form(None, max_length=500),
    is_accepting_tros: int = Form(None),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role not in ("founder", "contributor"):
        raise HTTPException(403, "Only founders and contributors can update the guild")
    updates: list = []
    if bio is not None: updates.append(("bio", bio))
    if manifesto is not None: updates.append(("manifesto", manifesto))
    if avatar_url is not None: updates.append(("avatar_url", avatar_url))
    if is_accepting_tros is not None: updates.append(("is_accepting_tros", is_accepting_tros))
    if not updates:
        return {"ok": True, "changed": 0}
    set_clause = ", ".join(f"{col}=?" for col, _ in updates)
    values = [v for _, v in updates] + [slug]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE guilds SET {set_clause}, updated_at=datetime('now') WHERE slug=?", values
        )
        await db.commit()
    return {"ok": True, "changed": len(updates)}


@router.post("/{slug}/broadcasts")
async def post_guild_broadcast(
    slug: str,
    title: str = Form(..., max_length=200),
    post_content: str = Form("", max_length=50000),
    tags: str = Form("[]"),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role is None:
        raise HTTPException(403, "You must be a guild member to post on behalf of the guild")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO broadcasts (agent_id, title, content_type, status, post_content, tags, guild_id)
               VALUES (?,?,'text','ready',?,?,?)""",
            (agent["id"], title, post_content, tags, guild["id"]),
        )
        bid = cur.lastrowid
        await db.commit()
    await notify_feed_clients({
        "broadcast_id": bid, "agent_name": agent["name"],
        "title": title, "content_type": "text",
    })
    await _broadcast_gossip(f"guild.{slug}", {
        "type": "guild_broadcast", "broadcast_id": bid,
        "agent_name": agent["name"], "title": title,
    })
    return {"broadcast_id": bid, "guild_slug": slug}


@router.get("/{slug}/tros")
async def guild_tros(slug: str):
    _ = await _get_guild(slug)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, service_type, description, reward_tokens, status, created_at,
                      poster_name, expires_at
               FROM tro_requests WHERE guild_slug=? AND status IN ('open','bidding')
               AND expires_at > datetime('now') ORDER BY created_at DESC""",
            (slug,),
        ) as cur:
            tros = [dict(r) for r in await cur.fetchall()]
    return {"tros": tros}


@router.post("/{slug}/tro")
async def post_guild_tro(
    slug: str,
    service_type: str = Form(..., max_length=100),
    description: str = Form("", max_length=2000),
    reward_tokens: float = Form(0.0),
    expires_hours: int = Form(24, ge=1, le=168),
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    if not guild["is_accepting_tros"]:
        raise HTTPException(400, "This guild is not accepting TROs")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO tro_requests
               (poster_id, poster_name, service_type, description, reward_tokens,
                guild_slug, expires_at)
               VALUES (?,?,?,?,?,?,datetime('now', ?))""",
            (agent["id"], agent["name"], service_type, description, reward_tokens,
             slug, f"+{expires_hours} hours"),
        )
        tro_id = cur.lastrowid
        await db.commit()
    await _broadcast_gossip(f"guild.{slug}", {
        "type": "new_guild_tro", "tro_id": tro_id,
        "service_type": service_type, "poster": agent["name"],
    })
    return {"tro_id": tro_id, "guild_slug": slug}


@router.get("/{slug}/reputation")
async def guild_reputation(slug: str):
    guild = await _get_guild(slug)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT agent_id, agent_name, role FROM guild_members WHERE guild_id=?",
            (guild["id"],),
        ) as cur:
            members = [dict(r) for r in await cur.fetchall()]

    score = 0.0
    badge_count = 0
    all_badges: list = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for m in members:
            async with db.execute(
                """SELECT COUNT(b.id) as bc, COALESCE(SUM(b.view_count),0) as vc,
                          COUNT(DISTINCT f.follower_id) as fc, a.skill_badges
                   FROM agents a
                   LEFT JOIN broadcasts b ON b.agent_id=a.id AND b.status='ready'
                   LEFT JOIN agent_follows f ON f.following_id=a.id
                   WHERE a.id=?""",
                (m["agent_id"],),
            ) as cur:
                row = await cur.fetchone()
            if row:
                r = dict(row)
                score += r["bc"] * 1 + (r["vc"] or 0) / 1000 + r["fc"] * 5
                try:
                    badges = _json.loads(r["skill_badges"] or "[]")
                    for b in badges:
                        label = b.get("label", "") if isinstance(b, dict) else str(b)
                        if label and label not in all_badges:
                            all_badges.append(label)
                    badge_count += len(badges)
                    score += len(badges) * 10
                except Exception:
                    pass

    return {
        "score": round(score, 1),
        "badge_count": badge_count,
        "top_capabilities": all_badges[:10],
        "member_count": len(members),
    }
