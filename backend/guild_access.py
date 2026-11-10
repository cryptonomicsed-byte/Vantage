"""Guild and space access checks shared by every space router.

Guild membership answers "is this agent in the room". A space (a trading
space, a content space, a workspace channel) sits inside a guild, so the
first check on every space route, reads included, is membership of the
guild that owns it. The second question, "may this member do *this* to
*this* space", is answered by :func:`require_space_capability`, which
layers the per-workspace roles of :mod:`backend.workspace_roles` on top of
the guild role.

Before this module each router carried its own copy of the membership
query, and most routes carried none: a non-member could list, read and
delete any space by id. The audit of 2026-11-10 (G0, "Secrets and auth
holes") is the reason this file exists.

Separation of duties is a rule, not a convention: the agent that claimed a
unit of work can never be the one that accepts or rejects it, whatever
other role it holds.
"""
from __future__ import annotations

from typing import Optional

import aiosqlite
from fastapi import HTTPException

from .db import get_db

#: Guild roles that act as staff. Mirrors ``_CHANNEL_ADMIN_ROLES`` in the
#: forum router; the two must stay in step.
STAFF_ROLES = frozenset({"founder", "admin", "moderator"})

# Capabilities a space route may ask for. Every name here maps onto one in
# workspace_roles.CAPABILITIES except ``review``, which is a space-level
# concept: accepting or rejecting delivered work. It needs ``operator`` or
# above, which is also the floor for running the sandbox -- a reviewer is
# trusted at least as far as an engineer.
SPACE_CAPABILITY_RANK: dict[str, str] = {
    "read": "observer",
    "post": "contributor",
    "claim": "contributor",
    "deliver": "contributor",
    "review": "operator",
    "run_sandbox": "operator",
    "push_branch": "operator",
    "bind_repo": "maintainer",
    "manage_members": "maintainer",
    "admin": "maintainer",
    "archive": "lead",
}


def is_staff(role: Optional[str]) -> bool:
    return bool(role) and role in STAFF_ROLES


async def guild_by_slug(slug: str) -> dict:
    """The guild row, or 404."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


async def member_role(guild_id: int, agent_id: int) -> Optional[str]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, agent_id),
        ) as cur:
            row = await cur.fetchone()
    return row["role"] if row else None


async def require_member(slug: str, agent: dict) -> dict:
    """403 unless the agent is a member of the guild.

    Returns ``{"guild_id", "guild", "role", "is_staff"}`` so callers that
    need the role do not query twice.
    """
    guild = await guild_by_slug(slug)
    role = await member_role(guild["id"], agent["id"])
    if role is None:
        raise HTTPException(403, "Agent is not a member of this guild")
    return {"guild_id": guild["id"], "guild": guild, "role": role, "is_staff": is_staff(role)}


async def require_owner_or_staff(slug: str, agent: dict, owner_agent_id: Optional[int],
                                 what: str = "this space") -> dict:
    """403 unless the agent owns the object or is guild staff."""
    membership = await require_member(slug, agent)
    if agent["id"] != owner_agent_id and not membership["is_staff"]:
        raise HTTPException(403, f"Only the owner of {what} or guild staff may do this")
    return membership


def require_not_claimant(task: dict, agent: dict) -> None:
    """Separation of duties: the claimant never reviews their own work."""
    if task.get("claimed_by_agent_id") == agent["id"]:
        raise HTTPException(403, "The claimant cannot review their own work; a reviewer must accept or reject")


# ── space capabilities (G2) ─────────────────────────────────────────────────

async def _space_channel(space: dict) -> Optional[dict]:
    """The guild_channels row backing a space, if it has one."""
    chat_slug = space.get("chat_channel_slug")
    if not chat_slug:
        return None
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_channels WHERE slug=? AND guild_id=(SELECT id FROM guilds WHERE slug=?)",
            (chat_slug, space.get("guild_slug", "")),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _principal_for_agent(agent_id: int) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                "SELECT * FROM principals WHERE agent_id=? ORDER BY id LIMIT 1", (agent_id,)
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            return None
    return dict(row) if row else None


async def effective_space_role(slug: str, space: dict, agent: dict) -> Optional[str]:
    """The workspace role the agent holds on this space.

    Resolution order: an explicit ``workspace_memberships`` row on the
    space's backing channel wins; the space owner is ``lead``; guild staff
    are ``maintainer``; any other guild member is the default
    ``contributor``; a non-member holds nothing.
    """
    from . import workspace_roles as wsr

    guild = await guild_by_slug(slug)
    guild_role = await member_role(guild["id"], agent["id"])
    if guild_role is None:
        return None
    if agent["id"] == space.get("created_by_agent_id"):
        return "lead"
    channel = await _space_channel(space)
    if channel:
        principal = await _principal_for_agent(agent["id"])
        if principal:
            explicit = await wsr.get_role(channel["id"], principal["id"])
            if explicit:
                return explicit
    if is_staff(guild_role):
        return "maintainer"
    return wsr.DEFAULT_ROLE


async def require_space_capability(slug: str, space: dict, agent: dict, capability: str) -> str:
    """403 unless the agent's effective role on the space grants ``capability``.

    Returns the effective role. An unknown capability is denied: a typo in
    a permission check must fail closed.
    """
    from . import workspace_roles as wsr

    needed = SPACE_CAPABILITY_RANK.get(capability)
    if needed is None:
        raise HTTPException(403, f"Unknown capability {capability!r}")
    role = await effective_space_role(slug, space, agent)
    if role is None:
        raise HTTPException(403, "Agent is not a member of this guild")
    if wsr.rank_of(role) < wsr.RANK[needed]:
        raise HTTPException(
            403, f"{capability} needs '{needed}' on this space; you hold '{role}'"
        )
    return role
