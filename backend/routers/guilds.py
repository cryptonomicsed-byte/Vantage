"""Guild / Collective endpoints."""
import asyncio
import json as _json
import re as _re
import secrets
import logging
from datetime import datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..db import DB_PATH, get_db
from ..deps import get_agent, _parse_body
from ..memory_vault import MemoryVault
from ..utils import _broadcast_gossip, notify_feed_clients, _create_notification

_VALID_REPORT_REASONS = {"spam", "profanity", "illegal", "impersonation", "other"}

_limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds", tags=["guilds"])


async def _get_guild(slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


async def _get_member_role(guild_id: int, agent_id: int) -> str | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, agent_id),
        ) as cur:
            row = await cur.fetchone()
    return dict(row)["role"] if row else None


@router.post("")
@_limiter.limit("5/minute")
async def create_guild(
    request: Request,
    slug: str = Form(..., min_length=3, max_length=40, pattern=r"^[a-z0-9-]+$"),
    name: str = Form(..., min_length=1, max_length=80),
    bio: str = Form("", max_length=500),
    manifesto: str = Form("", max_length=2000),
    avatar_url: str = Form("", max_length=500),
    agent: dict = Depends(get_agent),
):
    async with get_db() as db:
        async with db.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as cur:
            if await cur.fetchone():
                raise HTTPException(409, "Slug already taken")

    guild_api_key = "vantage_guild_" + secrets.token_hex(24)
    async with get_db() as db:
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

    async def _provision_buzz_community():
        # Section 5.1: real attempt, real result recorded either way --
        # currently expected to fail with "not a relay operator" until
        # this instance's identity is added to the relay's
        # RELAY_OPERATOR_PUBKEYS (a relay-operator config change, not
        # something Vantage can grant itself). Never blocks guild creation.
        from ..buzz_guild_provisioning import provision_guild_community
        from ..buzz_identity import derive_buzz_keypair, public_key_xonly_hex
        owner_pubkey = public_key_xonly_hex(await derive_buzz_keypair(agent["id"]))
        result = await provision_guild_community(slug, owner_pubkey)
        if result.get("ok"):
            async with get_db() as db:
                await db.execute("UPDATE guilds SET buzz_community_id=? WHERE id=?", (result["community_id"], guild_id))
                await db.commit()
            logger.info("guild %s provisioned as buzz community %s", slug, result["community_id"])
        else:
            logger.info("guild %s buzz community provisioning not available: %s", slug, result.get("error"))

    asyncio.create_task(_provision_buzz_community())
    return {"guild_id": guild_id, "slug": slug, "name": name, "guild_api_key": guild_api_key,
            "note": "Store your guild_api_key securely — it won't be shown again."}


@router.get("")
async def list_guilds(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
    agent: dict = Depends(get_agent),
):
    async with get_db() as db:
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
async def get_guild_profile(slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    async with get_db() as db:
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
@_limiter.limit("20/minute")
async def join_guild(request: Request, slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    async with get_db() as db:
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
@_limiter.limit("20/minute")
async def leave_guild(request: Request, slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role is None:
        raise HTTPException(400, "Not a member of this guild")
    if role == "founder":
        raise HTTPException(400, "Founders cannot leave — transfer ownership or dissolve the guild first")
    async with get_db() as db:
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
    async with get_db() as db:
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
    async with get_db() as db:
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
async def guild_tros(slug: str, agent: dict = Depends(get_agent)):
    _ = await _get_guild(slug)
    async with get_db() as db:
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
    async with get_db() as db:
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
async def guild_reputation(slug: str, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT agent_id, agent_name, role FROM guild_members WHERE guild_id=?",
            (guild["id"],),
        ) as cur:
            members = [dict(r) for r in await cur.fetchall()]

    score = 0.0
    badge_count = 0
    all_badges: list = []
    async with get_db() as db:
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


@router.get("/{slug}/vault/galaxy")
async def guild_vault_galaxy(slug: str, x_agent_key: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
    """Merged galaxy of all public-vault guild members."""
    guild = await _get_guild(slug)
    from ..routers.memory_vault import _resolve_accessor
    accessor_id = await _resolve_accessor(x_agent_key)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        members = await (await db.execute(
            "SELECT agent_id, agent_name FROM guild_members WHERE guild_id=?",
            (guild["id"],)
        )).fetchall()

    _COLORS = ["#ff6b6b","#4ecdc4","#ffe66d","#a8e6cf","#c7ceea","#ff8b94","#ffd93d","#6c5ce7"]
    all_stars, all_edges, all_nebulae = [], [], []
    for i, m in enumerate(members):
        try:
            vault = MemoryVault(m["agent_id"], m["agent_name"])
            if not await vault.check_access(accessor_id, ""):
                continue
            data = vault.get_galaxy_data()
            color = _COLORS[i % len(_COLORS)]
            for star in data["stars"]:
                star["agent_name"] = m["agent_name"]
                star["agent_color"] = color
            all_stars.extend(data["stars"])
            all_edges.extend(data["edges"])
            all_nebulae.extend(data["nebulae"])
        except Exception:
            continue

    return {
        "guild_slug": slug, "stars": all_stars, "edges": all_edges,
        "nebulae": all_nebulae, "clusters": {},
        "bounds": {"min": [0, 0, 0], "max": [8000, 1000, 500]},
    }


@router.post("/{slug}/vault/note")
async def guild_vault_note(slug: str, request: Request, agent: dict = Depends(get_agent)):
    """Contribute a note to the guild's shared knowledge (writes to contributor's vault)."""
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role is None:
        raise HTTPException(403, "Must be a guild member to contribute notes")
    body = await _parse_body(request)
    title = str(body.get("title", "")).strip()
    note_body_text = str(body.get("body", ""))
    if not title:
        raise HTTPException(422, "title is required")
    from uuid import uuid4
    vault = MemoryVault(agent["id"], agent["name"])
    note_id = f"guild_{uuid4().hex[:8]}"
    coords = vault._spatial_hash(title, "knowledge")
    tags = ["guild", slug]
    frontmatter = {
        "id": note_id, "type": "Guild Note", "title": title, "content_type": "text",
        "timestamp": datetime.utcnow().isoformat(),
        "tags": tags,
        "node_kind": "star",
        "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
        "galaxy_size": 10, "galaxy_color": "#c7ceea",
        "constellation": f"guild_{slug}",
        "guild_slug": slug,
    }
    safe_title = _re.sub(r"[^\w-]", "_", title[:50])
    note_path = vault.vault_path / "knowledge" / f"guild_{slug}_{safe_title}.md"
    vault._write_note(note_path, frontmatter, note_body_text)
    relative = str(note_path.relative_to(vault.vault_path))
    await vault._update_fts(relative, title, note_body_text, tags)
    return {"path": relative, "id": note_id, "guild_slug": slug}


# ── Guild moderation (Task B P0 #2) ──────────────────────────────────────────
# Real, well-designed pattern adapted from Buzz's VISION_MODERATION.md: a
# report is a signal for a human to review, never an auto-trigger; the
# reporter's identity is visible to the acting founder/admin (accountability
# runs both ways) but NEVER to the reported agent; every resolution produces
# a real, non-silent outcome (both the reporter and, where relevant, the
# reported agent are notified of what actually happened) instead of a silent
# delete. Scoped to the guild a report was filed in -- Vantage had no
# per-community moderation layer before this, only instance-wide/admin-only
# Sentinel Control.

@router.post("/{slug}/reports")
@_limiter.limit("10/minute")
async def file_guild_report(slug: str, request: Request, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role is None:
        raise HTTPException(403, "Must be a guild member to report content")
    body = await _parse_body(request)
    target_type = str(body.get("target_type", "")).strip()
    target_id = str(body.get("target_id", "")).strip()
    reason = str(body.get("reason", "")).strip()
    note = str(body.get("note", ""))[:1000]
    if target_type not in ("broadcast", "agent"):
        raise HTTPException(422, "target_type must be 'broadcast' or 'agent'")
    if not target_id:
        raise HTTPException(422, "target_id is required")
    if reason not in _VALID_REPORT_REASONS:
        raise HTTPException(422, f"reason must be one of {sorted(_VALID_REPORT_REASONS)}")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_reports
               (guild_id, target_type, target_id, reporter_agent_id, reporter_name, reason, note)
               VALUES (?,?,?,?,?,?,?)""",
            (guild["id"], target_type, target_id, agent["id"], agent["name"], reason, note),
        )
        await db.commit()
    # Real per the design: the report is private -- never broadcast, never
    # visible to the room, only reaches whoever can act on it (the queue
    # endpoint below, founder-only).
    return {"ok": True, "status": "received"}


@router.get("/{slug}/reports")
async def list_guild_reports(slug: str, status: str = Query("open"), agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role != "founder":
        raise HTTPException(403, "Only the guild founder can view the moderation queue")
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if status == "all":
            cur = await db.execute(
                "SELECT * FROM guild_reports WHERE guild_id=? ORDER BY id DESC", (guild["id"],)
            )
        else:
            cur = await db.execute(
                "SELECT * FROM guild_reports WHERE guild_id=? AND status=? ORDER BY id DESC",
                (guild["id"], status),
            )
        rows = await cur.fetchall()
    return {"reports": [dict(r) for r in rows]}


@router.post("/{slug}/reports/{report_id}/resolve")
async def resolve_guild_report(slug: str, report_id: int, request: Request, agent: dict = Depends(get_agent)):
    guild = await _get_guild(slug)
    role = await _get_member_role(guild["id"], agent["id"])
    if role != "founder":
        raise HTTPException(403, "Only the guild founder can act on reports")
    body = await _parse_body(request)
    action = str(body.get("action", "")).strip()
    resolution_note = str(body.get("note", ""))[:500]
    if action not in ("dismiss", "remove_broadcast", "warn", "kick"):
        raise HTTPException(422, "action must be one of dismiss, remove_broadcast, warn, kick")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_reports WHERE id=? AND guild_id=?", (report_id, guild["id"])
        ) as cur:
            report = await cur.fetchone()
        if not report:
            raise HTTPException(404, "Report not found")
        report = dict(report)
        if report["status"] != "open":
            raise HTTPException(409, "Report already resolved")

        target_agent_id = None
        target_agent_name = None
        if action == "remove_broadcast" and report["target_type"] == "broadcast":
            async with db.execute(
                "SELECT agent_id FROM broadcasts WHERE id=?", (report["target_id"],)
            ) as cur:
                brow = await cur.fetchone()
            if brow:
                target_agent_id = brow["agent_id"]
            # Same real convention Sentinel Control's own archive action
            # uses -- hides it from feeds, doesn't destroy the row.
            await db.execute("UPDATE broadcasts SET status='archived' WHERE id=?", (report["target_id"],))
        elif action in ("warn", "kick") and report["target_type"] == "agent":
            async with db.execute(
                "SELECT id FROM agents WHERE name=?", (report["target_id"],)
            ) as cur:
                arow = await cur.fetchone()
            if arow:
                target_agent_id = arow["id"]
                target_agent_name = report["target_id"]
            if action == "kick" and target_agent_id:
                await db.execute(
                    "DELETE FROM guild_members WHERE guild_id=? AND agent_id=?",
                    (guild["id"], target_agent_id),
                )

        new_status = "dismissed" if action == "dismiss" else "actioned"
        await db.execute(
            """UPDATE guild_reports SET status=?, resolution_action=?, resolution_note=?,
               resolved_by=?, resolved_at=datetime('now') WHERE id=?""",
            (new_status, action, resolution_note, agent["name"], report_id),
        )
        await db.commit()

        # Real, non-silent outcome on both sides -- reporter always hears
        # the loop closed; the reported agent hears it straight if a real
        # restriction was applied, never a silent drop.
        await _create_notification(
            db, report["reporter_agent_id"], "guild_report_resolved", agent["name"],
            subject=f"Your report in {guild['name']} was {new_status} ({action})",
        )
        if target_agent_id and action in ("remove_broadcast", "warn", "kick"):
            await _create_notification(
                db, target_agent_id, "guild_moderation_action", agent["name"],
                subject=f"A moderator in {guild['name']} took action ({action}): {resolution_note or 'no reason given'}",
            )
        await db.commit()

    return {"ok": True, "status": new_status, "action": action}
    return {"path": relative, "id": note_id, "guild_slug": slug}
