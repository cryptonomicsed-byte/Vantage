"""Per-workspace roles, and the templates that hand them out.

Guild membership answers "is this principal in the room". It does not answer
"may this principal bind the repository", which is a different question with
a different answer per workspace: an agent can be trusted to comment on the
engineering workspace and not to push to it, and the same agent may be the
lead on a research one.

Without this, "put that agent on the engineering workspace" is a figure of
speech -- there is nowhere to write it down. With it, it is a row, and the
write paths read that row.

Two design points worth stating.

**Roles are ordered, and permissions derive from the order.** A role is not a
bag of flags; it is a rank, and each capability names the rank it needs. That
means adding a capability cannot accidentally grant it to observers, and the
permission check is a comparison rather than a lookup table that drifts.

**A template is a row, not a subsystem.** It carries a starting role, a skill
list, an allowed-tool list and a budget. Applying one is an insert. There is
no template engine, no inheritance, and no runtime -- if that is ever wanted,
it should be argued for on its own rather than smuggled in here.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

# Ordered least to most. The integer is the rank; the name is the API.
ROLES = ["observer", "contributor", "operator", "maintainer", "lead"]
RANK = {name: i for i, name in enumerate(ROLES)}

DEFAULT_ROLE = "contributor"

# Each capability names the minimum rank that holds it. Read this table to
# answer "what does operator actually let me do" -- it is the whole answer.
CAPABILITIES: dict[str, str] = {
    "read": "observer",
    "post": "contributor",       # say / propose in the workspace channel
    "claim": "contributor",      # take a unit of work
    "deliver": "contributor",    # post an artifact closing your own claim
    "run_sandbox": "operator",   # execute in the workspace sandbox
    "push_branch": "operator",   # write to the bound repository
    "bind_repo": "maintainer",   # choose which repository this workspace is
    "manage_members": "maintainer",
    "set_flow": "maintainer",    # change the channel's turn-taking mode
    "archive": "lead",
}


class RoleError(PermissionError):
    """Raised where a caller lacks the rank a capability needs. Carries both
    sides so the message can say what would have been enough."""

    def __init__(self, capability: str, held: Optional[str], needed: str):
        self.capability, self.held, self.needed = capability, held, needed
        held_text = f"'{held}'" if held else "no workspace role"
        super().__init__(
            f"{capability} needs '{needed}' on this workspace; you hold {held_text}"
        )


def rank_of(role: Optional[str]) -> int:
    """-1 for a principal with no role at all, which is below observer."""
    if not role:
        return -1
    return RANK.get(role, -1)


def allows(role: Optional[str], capability: str) -> bool:
    needed = CAPABILITIES.get(capability)
    if needed is None:
        # An unknown capability is denied rather than allowed. A typo in a
        # permission check must fail closed.
        return False
    return rank_of(role) >= RANK[needed]


def capabilities_of(role: Optional[str]) -> list[str]:
    return [cap for cap, needed in CAPABILITIES.items() if rank_of(role) >= RANK[needed]]


# ── schema ───────────────────────────────────────────────────────────────────

async def init_workspace_roles_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS workspace_memberships (
                channel_id INTEGER NOT NULL REFERENCES guild_channels(id),
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                role TEXT NOT NULL DEFAULT 'contributor',
                granted_by INTEGER REFERENCES principals(id),
                template TEXT DEFAULT '',
                budget_usdc REAL NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (channel_id, principal_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_wsm_principal ON workspace_memberships(principal_id)"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS role_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER REFERENCES guilds(id),
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                workspace_role TEXT NOT NULL DEFAULT 'contributor',
                skills TEXT NOT NULL DEFAULT '[]',
                allowed_tools TEXT NOT NULL DEFAULT '[]',
                budget_usdc REAL NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE (guild_id, name)
            )
        """)
        await db.commit()


# ── memberships ──────────────────────────────────────────────────────────────

async def get_role(channel_id: int, principal_id: int) -> Optional[str]:
    """The explicit role, or None where there is no row.

    A missing table is treated as a missing row rather than an error. An
    instance part-way through the migration that added this table would
    otherwise 500 on every post -- and the answer it needs, "this principal
    has no explicit role", is exactly what an absent table means.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                "SELECT role FROM workspace_memberships WHERE channel_id=? AND principal_id=?",
                (channel_id, principal_id),
            )
            row = await cur.fetchone()
        except Exception as exc:
            logger.debug("workspace_roles: memberships unavailable: %s", exc)
            return None
    return dict(row)["role"] if row else None


async def effective_role(channel: dict, principal: Optional[dict], guild_role: Optional[str]) -> Optional[str]:
    """The role a principal actually holds here.

    A workspace row wins where one exists. Where none does, a guild founder or
    admin is treated as maintainer -- otherwise creating a workspace would
    lock its own creator out of binding a repository to it, which is the kind
    of correctness that reads as a bug to everyone who hits it.

    An ordinary guild member with no row falls back to contributor: they can
    talk, claim and deliver, and they cannot run the sandbox, push a branch
    or bind the repository. Making the *default* an exclusion instead would
    have silently locked every existing guild out of its own workspaces the
    moment this table appeared, which is not a permission model, it is an
    outage. So "put that agent on the workspace" means promoting it to
    operator or above, and an explicit `observer` row is a real demotion
    below the default -- both directions are a state transition, which is
    what this table exists to make possible.
    """
    if principal is None:
        return None
    explicit = await get_role(channel["id"], principal["id"])
    if explicit:
        return explicit
    if guild_role in ("founder", "admin"):
        return "maintainer"
    return DEFAULT_ROLE if guild_role else None


def require(role: Optional[str], capability: str) -> None:
    if not allows(role, capability):
        raise RoleError(capability, role, CAPABILITIES.get(capability, "lead"))


async def set_role(
    *, channel_id: int, principal_id: int, role: str,
    granted_by: Optional[int] = None, template: str = "", budget_usdc: float = 0.0,
) -> dict:
    if role not in RANK:
        raise ValueError(f"unknown workspace role: {role!r}")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO workspace_memberships
                 (channel_id, principal_id, role, granted_by, template, budget_usdc)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(channel_id, principal_id) DO UPDATE SET
                 role=excluded.role, granted_by=excluded.granted_by,
                 template=excluded.template, budget_usdc=excluded.budget_usdc,
                 updated_at=datetime('now')""",
            (channel_id, principal_id, role, granted_by, template, budget_usdc),
        )
        await db.commit()
    return {"channel_id": channel_id, "principal_id": principal_id, "role": role,
            "template": template, "budget_usdc": budget_usdc,
            "capabilities": capabilities_of(role)}


async def remove_role(channel_id: int, principal_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM workspace_memberships WHERE channel_id=? AND principal_id=?",
            (channel_id, principal_id),
        )
        await db.commit()
    return bool(cur.rowcount)


async def list_members(channel_id: int) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                """SELECT w.*, p.display_name, p.kind AS principal_kind, p.framework,
                          p.pubkey, p.key_custody
                     FROM workspace_memberships w
                     JOIN principals p ON p.id = w.principal_id
                    WHERE w.channel_id=?""",
                (channel_id,),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        except Exception as exc:
            logger.debug("workspace_roles: memberships unavailable: %s", exc)
            return []
    for row in rows:
        row["capabilities"] = capabilities_of(row["role"])
    # Sort by actual rank, not by the string -- 'observer' > 'lead'
    # alphabetically, which is the wrong order and a plausible thing to miss.
    rows.sort(key=lambda r: (-rank_of(r["role"]), r["display_name"] or ""))
    return rows


# ── templates ────────────────────────────────────────────────────────────────

BUILTIN_TEMPLATES = [
    {
        "name": "reviewer",
        "description": "Reads the workspace and comments. Cannot run or push.",
        "workspace_role": "observer",
        "skills": ["code-review"],
        "allowed_tools": ["read_file", "search"],
        "budget_usdc": 0.0,
    },
    {
        "name": "engineer",
        "description": "Takes work, runs it in the sandbox, and pushes a branch.",
        "workspace_role": "operator",
        "skills": ["code-review", "run-tests"],
        "allowed_tools": ["read_file", "search", "write_file", "run_sandbox", "git_push"],
        "budget_usdc": 5.0,
    },
    {
        "name": "analyst",
        "description": "Takes research work and delivers artifacts. No repository access.",
        "workspace_role": "contributor",
        "skills": ["research"],
        "allowed_tools": ["read_file", "search", "fetch"],
        "budget_usdc": 2.0,
    },
    {
        "name": "workspace-lead",
        "description": "Binds the repository, sets flow, and manages the roster.",
        "workspace_role": "lead",
        "skills": [],
        "allowed_tools": ["*"],
        "budget_usdc": 25.0,
    },
]


async def seed_builtin_templates() -> int:
    """Install the instance-wide templates (guild_id NULL). Idempotent.

    Instance-wide rather than per-guild so a new guild is usable immediately;
    a guild that wants its own overrides one by name.
    """
    installed = 0
    async with get_db() as db:
        for tpl in BUILTIN_TEMPLATES:
            cur = await db.execute(
                """INSERT OR IGNORE INTO role_templates
                     (guild_id, name, description, workspace_role, skills, allowed_tools, budget_usdc)
                   VALUES (NULL,?,?,?,?,?,?)""",
                (tpl["name"], tpl["description"], tpl["workspace_role"],
                 json.dumps(tpl["skills"]), json.dumps(tpl["allowed_tools"]), tpl["budget_usdc"]),
            )
            installed += cur.rowcount or 0
        await db.commit()
    return installed


async def get_template(name: str, guild_id: Optional[int] = None) -> Optional[dict]:
    """A guild's own template shadows the instance-wide one of the same name."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM role_templates
                WHERE name=? AND (guild_id=? OR guild_id IS NULL)
                ORDER BY guild_id IS NULL ASC LIMIT 1""",
            (name, guild_id),
        )
        row = await cur.fetchone()
    if not row:
        return None
    row = dict(row)
    row["skills"] = json.loads(row["skills"] or "[]")
    row["allowed_tools"] = json.loads(row["allowed_tools"] or "[]")
    return row


async def list_templates(guild_id: Optional[int] = None) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM role_templates WHERE guild_id IS NULL OR guild_id=? ORDER BY name",
            (guild_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: (r["name"], r["guild_id"] is None)):
        if row["name"] in seen:
            continue  # the guild-specific one sorted first and wins
        seen.add(row["name"])
        row["skills"] = json.loads(row["skills"] or "[]")
        row["allowed_tools"] = json.loads(row["allowed_tools"] or "[]")
        out.append(row)
    return out


async def apply_template(
    *, channel_id: int, principal_id: int, template_name: str,
    guild_id: Optional[int] = None, granted_by: Optional[int] = None,
) -> dict:
    tpl = await get_template(template_name, guild_id)
    if tpl is None:
        raise ValueError(f"no such role template: {template_name!r}")
    membership = await set_role(
        channel_id=channel_id, principal_id=principal_id, role=tpl["workspace_role"],
        granted_by=granted_by, template=tpl["name"], budget_usdc=tpl["budget_usdc"],
    )
    membership["skills"] = tpl["skills"]
    membership["allowed_tools"] = tpl["allowed_tools"]
    return membership
