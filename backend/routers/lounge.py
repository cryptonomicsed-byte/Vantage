"""Guild Lounge — persistent orchestration rooms with slash commands and mode dispatch."""
from __future__ import annotations

import asyncio
import json
import logging
import os as _os
import secrets
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.db import get_db
from backend.deps import get_agent as get_current_agent
from backend.justice_layer import anchor_receipt as _anchor_justice

logger = logging.getLogger(__name__)
_SUPERVISOR_URL = _os.environ.get("SWARM_SUPERVISOR_URL", "")

router = APIRouter(prefix="/api/guilds/{guild_slug}/lounge", tags=["lounge"])

# ── Models ────────────────────────────────────────────────────────────────────

class LoungeCreate(BaseModel):
    name: str = "Lounge"
    mode: str = "mixed"
    max_members: int = 20

class LoungeMode(BaseModel):
    mode: str  # video | code | trade | mixed
    context: dict = {}

class InviteCreate(BaseModel):
    lounge_id: Optional[str] = None
    max_uses: int = 1
    ttl_seconds: Optional[int] = None  # None = never expires

class InviteAccept(BaseModel):
    token: str

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    mode: str = "mixed"
    priority: int = 5

class SlashCommand(BaseModel):
    text: str  # raw message text starting with /

VALID_MODES = {"video", "code", "trade", "mixed"}

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_lounge(db, guild_slug: str, lounge_id: str) -> dict:
    row = await (await db.execute(
        "SELECT * FROM guild_lounges WHERE id=? AND guild_slug=?",
        (lounge_id, guild_slug)
    )).fetchone()
    if not row:
        raise HTTPException(404, "Lounge not found")
    return dict(row)


async def _assert_member(db, lounge_id: str, agent_id: int, role: Optional[str] = None):
    row = await (await db.execute(
        "SELECT role FROM guild_lounge_members WHERE lounge_id=? AND agent_id=?",
        (lounge_id, agent_id)
    )).fetchone()
    if not row:
        raise HTTPException(403, "Not a lounge member")
    if role and row["role"] != role:
        raise HTTPException(403, f"Requires role: {role}")
    return row["role"]


async def _capability_score(db, agent_id: int, mode: str) -> float:
    """Score an agent for a given mode (0-1). Higher = better fit."""
    agent = await (await db.execute(
        "SELECT capabilities, status FROM agents WHERE id=?", (agent_id,)
    )).fetchone()
    if not agent or agent["status"] not in ("idle", "active"):
        return 0.0
    caps = json.loads(agent["capabilities"] or "[]")
    mode_cap_map = {
        "video": ["video", "media", "render"],
        "code": ["code", "dev", "engineer", "debug"],
        "trade": ["trade", "finance", "market", "quant"],
        "mixed": [],
    }
    keywords = mode_cap_map.get(mode, [])
    if not keywords:
        return 0.5
    hits = sum(1 for c in caps if any(k in str(c).lower() for k in keywords))
    return min(hits / max(len(keywords), 1), 1.0)


async def _notify_supervisor(agent_id, task_id: str, mode: str) -> None:
    """Fire-and-forget: POST assign to Elixir Creation supervisor. Fail-open."""
    if not _SUPERVISOR_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as _c:
            await _c.post(
                f"{_SUPERVISOR_URL}/agents/{agent_id}/assign",
                json={"task_id": task_id, "mode": mode},
            )
    except Exception as _e:
        logger.warning("[lounge] supervisor assign failed (agent=%s task=%s): %s", agent_id, task_id, _e)


async def _pick_best_agent(db, lounge_id: str, guild_slug: str, mode: str) -> Optional[int]:
    """Return best-fit idle member for a task, or None."""
    rows = await (await db.execute(
        """SELECT glm.agent_id FROM guild_lounge_members glm
           JOIN agents a ON a.id = glm.agent_id
           WHERE glm.lounge_id=? AND a.status='idle'
           ORDER BY glm.joined_ts ASC""",
        (lounge_id,)
    )).fetchall()
    # Swarm supervisor: filter agents with stale heartbeat (>120s)
    import os as _os, time as _t
    _sup_url = _os.environ.get("SWARM_SUPERVISOR_URL", "")
    if _sup_url and rows:
        import httpx as _hx
        _stale = set()
        for _r in rows:
            try:
                async with _hx.AsyncClient(timeout=2) as _c:
                    _rs = await _c.get(f"{_sup_url}/agents/{_r['agent_id']}/status")
                    if _rs.status_code == 200 and _t.time() - _rs.json().get("last_heartbeat_ts", 0) > 120:
                        _stale.add(_r["agent_id"])
            except Exception:
                pass
        rows = [r for r in rows if r["agent_id"] not in _stale]
    if not rows:
        return None
    scored = []
    for r in rows:
        score = await _capability_score(db, r["agent_id"], mode)
        scored.append((score, r["agent_id"]))
    scored.sort(reverse=True)
    return scored[0][1] if scored else None

# ── Lounge CRUD ───────────────────────────────────────────────────────────────

@router.post("")
async def create_lounge(
    guild_slug: str,
    body: LoungeCreate,
    agent=Depends(get_current_agent),
):
    """Create a new persistent lounge room in the guild."""
    async with get_db() as db:
        # agent must be guild member
        mem = await (await db.execute(
            "SELECT 1 FROM guild_members WHERE guild_slug=? AND agent_id=?",
            (guild_slug, agent["id"])
        )).fetchone()
        if not mem:
            raise HTTPException(403, "Must be a guild member")

        lounge_id = str(uuid.uuid4())
        try:
            await db.execute(
                """INSERT INTO guild_lounges
                   (id, guild_slug, name, mode, created_by_agent_id, max_members)
                   VALUES (?,?,?,?,?,?)""",
                (lounge_id, guild_slug, body.name, body.mode, agent["id"], body.max_members)
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, f"Lounge '{body.name}' already exists in this guild")
            raise
        # creator auto-joins as host
        await db.execute(
            "INSERT INTO guild_lounge_members (lounge_id, agent_id, role) VALUES (?,?,?)",
            (lounge_id, agent["id"], "host")
        )
        await db.execute(
            "INSERT OR IGNORE INTO lounge_mode_state (lounge_id, mode, changed_by_agent_id) VALUES (?,?,?)",
            (lounge_id, body.mode, agent["id"])
        )
        await db.commit()
    return {"lounge_id": lounge_id, "name": body.name, "mode": body.mode}


@router.get("")
async def list_lounges(guild_slug: str, agent=Depends(get_current_agent)):
    async with get_db() as db:
        rows = await (await db.execute(
            """SELECT gl.*, COUNT(glm.agent_id) as member_count
               FROM guild_lounges gl
               LEFT JOIN guild_lounge_members glm ON glm.lounge_id = gl.id
               WHERE gl.guild_slug=?
               GROUP BY gl.id ORDER BY gl.created_ts DESC""",
            (guild_slug,)
        )).fetchall()
    return [dict(r) for r in rows]


@router.get("/{lounge_id}")
async def get_lounge(guild_slug: str, lounge_id: str, agent=Depends(get_current_agent)):
    async with get_db() as db:
        lounge = await _get_lounge(db, guild_slug, lounge_id)
        members = await (await db.execute(
            """SELECT glm.agent_id, glm.role, glm.joined_ts, a.name
               FROM guild_lounge_members glm
               JOIN agents a ON a.id = glm.agent_id
               WHERE glm.lounge_id=?""",
            (lounge_id,)
        )).fetchall()
        mode_state = await (await db.execute(
            "SELECT * FROM lounge_mode_state WHERE lounge_id=?", (lounge_id,)
        )).fetchone()
    return {
        **lounge,
        "members": [dict(m) for m in members],
        "mode_state": dict(mode_state) if mode_state else None,
    }

# ── Membership ────────────────────────────────────────────────────────────────

@router.post("/{lounge_id}/join")
async def join_lounge(guild_slug: str, lounge_id: str, agent=Depends(get_current_agent)):
    async with get_db() as db:
        lounge = await _get_lounge(db, guild_slug, lounge_id)
        count = (await (await db.execute(
            "SELECT COUNT(*) FROM guild_lounge_members WHERE lounge_id=?", (lounge_id,)
        )).fetchone())[0]
        if count >= lounge["max_members"]:
            raise HTTPException(400, "Lounge is full")
        await db.execute(
            "INSERT OR IGNORE INTO guild_lounge_members (lounge_id, agent_id) VALUES (?,?)",
            (lounge_id, agent["id"])
        )
        await db.commit()
    return {"joined": lounge_id}


@router.delete("/{lounge_id}/join")
async def leave_lounge(guild_slug: str, lounge_id: str, agent=Depends(get_current_agent)):
    async with get_db() as db:
        await db.execute(
            "DELETE FROM guild_lounge_members WHERE lounge_id=? AND agent_id=?",
            (lounge_id, agent["id"])
        )
        await db.commit()
    return {"left": lounge_id}

# ── Invite tokens ─────────────────────────────────────────────────────────────

@router.post("/invite")
async def mint_invite(guild_slug: str, body: InviteCreate, agent=Depends(get_current_agent)):
    """Mint a single-use (or multi-use) invite token for the guild (optionally a specific lounge)."""
    async with get_db() as db:
        mem = await (await db.execute(
            "SELECT 1 FROM guild_members WHERE guild_slug=? AND agent_id=?",
            (guild_slug, agent["id"])
        )).fetchone()
        if not mem:
            raise HTTPException(403, "Must be a guild member to mint invites")

        if body.lounge_id:
            await _get_lounge(db, guild_slug, body.lounge_id)

        token = secrets.token_hex(32)
        expires_ts = int(time.time()) + body.ttl_seconds if body.ttl_seconds else None
        await db.execute(
            """INSERT INTO guild_invite_tokens
               (token, guild_slug, lounge_id, created_by_agent_id, max_uses, expires_ts)
               VALUES (?,?,?,?,?,?)""",
            (token, guild_slug, body.lounge_id, agent["id"], body.max_uses, expires_ts)
        )
        await db.commit()
    return {"token": token, "guild_slug": guild_slug, "lounge_id": body.lounge_id, "expires_ts": expires_ts}


@router.post("/invite/accept")
async def accept_invite(guild_slug: str, body: InviteAccept, agent=Depends(get_current_agent)):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM guild_invite_tokens WHERE token=? AND guild_slug=?",
            (body.token, guild_slug)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Invalid invite token")
        if row["uses"] >= row["max_uses"]:
            raise HTTPException(410, "Invite token exhausted")
        if row["expires_ts"] and int(time.time()) > row["expires_ts"]:
            raise HTTPException(410, "Invite token expired")

        # join guild if not already a member
        await db.execute(
            "INSERT OR IGNORE INTO guild_members (guild_slug, agent_id) VALUES (?,?)",
            (guild_slug, agent["id"])
        )
        # join lounge if token is lounge-scoped
        if row["lounge_id"]:
            await db.execute(
                "INSERT OR IGNORE INTO guild_lounge_members (lounge_id, agent_id) VALUES (?,?)",
                (row["lounge_id"], agent["id"])
            )
        # increment use count
        await db.execute(
            "UPDATE guild_invite_tokens SET uses=uses+1 WHERE token=?", (body.token,)
        )
        await db.commit()
    return {"joined_guild": guild_slug, "joined_lounge": row["lounge_id"]}

# ── Mode switching ────────────────────────────────────────────────────────────

@router.put("/{lounge_id}/mode")
async def set_mode(
    guild_slug: str,
    lounge_id: str,
    body: LoungeMode,
    agent=Depends(get_current_agent),
):
    if body.mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {VALID_MODES}")
    async with get_db() as db:
        await _get_lounge(db, guild_slug, lounge_id)
        await _assert_member(db, lounge_id, agent["id"])
        await db.execute(
            """INSERT INTO lounge_mode_state (lounge_id, mode, mode_context, changed_by_agent_id, changed_ts)
               VALUES (?,?,?,?,unixepoch())
               ON CONFLICT(lounge_id) DO UPDATE SET
                   mode=excluded.mode,
                   mode_context=excluded.mode_context,
                   changed_by_agent_id=excluded.changed_by_agent_id,
                   changed_ts=excluded.changed_ts""",
            (lounge_id, body.mode, json.dumps(body.context), agent["id"])
        )
        await db.execute(
            "UPDATE guild_lounges SET mode=?, updated_ts=unixepoch() WHERE id=?",
            (body.mode, lounge_id)
        )
        await db.commit()
    return {"lounge_id": lounge_id, "mode": body.mode}

# ── Task dispatch ─────────────────────────────────────────────────────────────

@router.post("/{lounge_id}/tasks")
async def create_task(
    guild_slug: str,
    lounge_id: str,
    body: TaskCreate,
    agent=Depends(get_current_agent),
):
    async with get_db() as db:
        await _get_lounge(db, guild_slug, lounge_id)
        await _assert_member(db, lounge_id, agent["id"])
        task_id = str(uuid.uuid4())
        assigned = await _pick_best_agent(db, lounge_id, guild_slug, body.mode)
        await db.execute(
            """INSERT INTO lounge_tasks
               (id, lounge_id, guild_slug, title, description, mode, assigned_agent_id,
                priority, created_by_agent_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (task_id, lounge_id, guild_slug, body.title, body.description,
             body.mode, assigned, body.priority, agent["id"])
        )
        await db.commit()
    # Fire-and-forget: notify Elixir Creation supervisor of assignment
    if assigned:
        asyncio.create_task(_notify_supervisor(assigned, task_id, body.mode))
    # Fire-and-forget: anchor receipt to justice layer
    asyncio.create_task(_anchor_justice(
        principal_id=str(agent.get("id", "")),
        action_kind="lounge_task_create",
        capability="lounge",
        evidence={"task_id": task_id, "lounge_id": lounge_id,
                  "assigned_agent_id": assigned, "mode": body.mode},
        gate_alignment=1.0,
    ))
    return {"task_id": task_id, "assigned_agent_id": assigned}


@router.get("/{lounge_id}/tasks")
async def list_tasks(
    guild_slug: str,
    lounge_id: str,
    status: Optional[str] = Query(None),
    agent=Depends(get_current_agent),
):
    async with get_db() as db:
        await _get_lounge(db, guild_slug, lounge_id)
        q = "SELECT * FROM lounge_tasks WHERE lounge_id=?"
        params: list = [lounge_id]
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY priority DESC, created_ts ASC"
        rows = await (await db.execute(q, params)).fetchall()
    return [dict(r) for r in rows]


@router.patch("/{lounge_id}/tasks/{task_id}")
async def update_task(
    guild_slug: str,
    lounge_id: str,
    task_id: str,
    body: dict,
    agent=Depends(get_current_agent),
):
    allowed = {"status", "assigned_agent_id", "priority"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    async with get_db() as db:
        await _get_lounge(db, guild_slug, lounge_id)
        await _assert_member(db, lounge_id, agent["id"])
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [task_id, lounge_id]
        await db.execute(
            f"UPDATE lounge_tasks SET {sets}, updated_ts=unixepoch() WHERE id=? AND lounge_id=?",
            vals
        )
        await db.commit()
    return {"updated": task_id}

# ── Slash command handler ─────────────────────────────────────────────────────

SLASH_HELP = """
Available slash commands:
  /mode <video|code|trade|mixed>   — switch lounge mode
  /task <title>                    — create and auto-assign a task
  /invite [lounge]                 — mint a single-use guild invite token
  /members                         — list lounge members
  /help                            — show this message
"""

async def _check_mode_skill(db, guild_slug: str, lounge_id: str, mode: str, agent: dict):
    """Check if a guild skill is registered for this mode. Returns skill dict or None."""
    try:
        import json as _json
        row = await (await db.execute(
            "SELECT * FROM guild_skills WHERE guild_slug=? AND mode=? AND enabled=1 LIMIT 1",
            (guild_slug, mode)
        )).fetchone()
        if not row:
            return None
        skill = dict(row)
        if skill.get("capability_required"):
            caps = agent.get("capabilities", [])
            if isinstance(caps, str):
                try:
                    caps = _json.loads(caps)
                except Exception:
                    caps = []
            if skill["capability_required"] not in caps:
                return None
        return skill
    except Exception:
        return None


async def handle_slash(
    guild_slug: str,
    lounge_id: str,
    text: str,
    agent: dict,
) -> dict:
    """Parse and execute a slash command. Returns a reply payload."""
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    async with get_db() as db:
        if cmd == "/help":
            return {"reply": SLASH_HELP}

        elif cmd == "/mode":
            if arg not in VALID_MODES:
                return {"reply": f"Unknown mode '{arg}'. Valid: {', '.join(VALID_MODES)}"}
            # Check for registered guild skill before native mode switch
            _skill = await _check_mode_skill(db, guild_slug, lounge_id, arg, agent)
            if _skill:
                return {
                    "reply": f"Mode {arg} → skill '{_skill['name']}' invoked.",
                    "skill_invoked": True,
                    "skill_name": _skill["name"],
                    "skill_id": _skill["id"],
                }
            await db.execute(
                """INSERT INTO lounge_mode_state (lounge_id, mode, changed_by_agent_id, changed_ts)
                   VALUES (?,?,?,unixepoch())
                   ON CONFLICT(lounge_id) DO UPDATE SET
                       mode=excluded.mode, changed_by_agent_id=excluded.changed_by_agent_id,
                       changed_ts=excluded.changed_ts""",
                (lounge_id, arg, agent["id"])
            )
            await db.execute(
                "UPDATE guild_lounges SET mode=?, updated_ts=unixepoch() WHERE id=?",
                (arg, lounge_id)
            )
            await db.commit()
            # Fire-and-forget: re-assign active task with new mode if supervisor is configured
            if _SUPERVISOR_URL:
                active_task = await (await db.execute(
                    "SELECT id, assigned_agent_id FROM lounge_tasks WHERE lounge_id=? AND status='pending' ORDER BY priority DESC LIMIT 1",
                    (lounge_id,)
                )).fetchone()
                if active_task and active_task["assigned_agent_id"]:
                    asyncio.create_task(_notify_supervisor(
                        active_task["assigned_agent_id"], active_task["id"], arg
                    ))
            return {"reply": f"Mode switched to **{arg}**"}

        elif cmd == "/task":
            if not arg:
                return {"reply": "Usage: /task <title>"}
            task_id = str(uuid.uuid4())
            mode_row = await (await db.execute(
                "SELECT mode FROM lounge_mode_state WHERE lounge_id=?", (lounge_id,)
            )).fetchone()
            mode = mode_row["mode"] if mode_row else "mixed"
            assigned = await _pick_best_agent(db, lounge_id, guild_slug, mode)
            await db.execute(
                """INSERT INTO lounge_tasks
                   (id, lounge_id, guild_slug, title, mode, assigned_agent_id, created_by_agent_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (task_id, lounge_id, guild_slug, arg, mode, assigned, agent["id"])
            )
            await db.commit()
            # Fire-and-forget: notify supervisor + anchor receipt
            if assigned:
                asyncio.create_task(_notify_supervisor(assigned, task_id, mode))
            asyncio.create_task(_anchor_justice(
                principal_id=str(agent.get("id", "")),
                action_kind="lounge_slash_task",
                capability="lounge",
                evidence={"task_id": task_id, "lounge_id": lounge_id,
                          "assigned_agent_id": assigned, "mode": mode},
                gate_alignment=1.0,
            ))
            assigned_name = "(unassigned)"
            if assigned:
                row = await (await db.execute("SELECT name FROM agents WHERE id=?", (assigned,))).fetchone()
                assigned_name = row["name"] if row else str(assigned)
            return {"reply": f"Task created → assigned to **{assigned_name}**", "task_id": task_id}

        elif cmd == "/invite":
            lounge_scope = lounge_id if arg == "lounge" else None
            token = secrets.token_hex(32)
            await db.execute(
                """INSERT INTO guild_invite_tokens
                   (token, guild_slug, lounge_id, created_by_agent_id, max_uses)
                   VALUES (?,?,?,?,1)""",
                (token, guild_slug, lounge_scope, agent["id"])
            )
            await db.commit()
            return {"reply": f"Invite token: ", "token": token}

        elif cmd == "/members":
            rows = await (await db.execute(
                """SELECT a.name, glm.role FROM guild_lounge_members glm
                   JOIN agents a ON a.id = glm.agent_id
                   WHERE glm.lounge_id=? ORDER BY glm.joined_ts""",
                (lounge_id,)
            )).fetchall()
            lines = [f"- **{r['name']}** ({r['role']})" for r in rows]
            return {"reply": "**Members:**\n" + "\n".join(lines)}

        else:
            return {"reply": f"Unknown command . Try /help"}


@router.post("/{lounge_id}/slash")
async def slash_command(
    guild_slug: str,
    lounge_id: str,
    body: SlashCommand,
    agent=Depends(get_current_agent),
):
    """Execute a slash command in the lounge context."""
    async with get_db() as db:
        await _get_lounge(db, guild_slug, lounge_id)
        await _assert_member(db, lounge_id, agent["id"])
    return await handle_slash(guild_slug, lounge_id, body.text, agent)


# ── Channel link ──────────────────────────────────────────────────────────────

class ChannelLink(BaseModel):
    channel_slug: str  # slug of the guild_channel to bind to this lounge


@router.put("/{lounge_id}/channel")
async def link_channel(
    guild_slug: str,
    lounge_id: str,
    body: ChannelLink,
    agent=Depends(get_current_agent),
):
    """Bind a guild channel to this lounge by storing its id as nostr_channel_id.
    Once set, messages starting with '/' posted to that channel are routed to
    the lounge slash handler instead of the Nostr relay."""
    async with get_db() as db:
        lounge = await _get_lounge(db, guild_slug, lounge_id)
        await _assert_member(db, lounge_id, agent["id"])

        # Resolve channel slug → integer id via guild membership
        guild_row = await (await db.execute(
            "SELECT id FROM guilds WHERE slug=?", (guild_slug,)
        )).fetchone()
        if not guild_row:
            raise HTTPException(404, "Guild not found")

        channel_row = await (await db.execute(
            "SELECT id FROM guild_channels WHERE guild_id=? AND slug=?",
            (guild_row["id"], body.channel_slug),
        )).fetchone()
        if not channel_row:
            raise HTTPException(404, f"Channel '{body.channel_slug}' not found in guild")

        channel_id_str = str(channel_row["id"])

        # Prevent two lounges sharing the same channel
        existing = await (await db.execute(
            "SELECT id FROM guild_lounges WHERE guild_slug=? AND nostr_channel_id=? AND id!=?",
            (guild_slug, channel_id_str, lounge_id),
        )).fetchone()
        if existing:
            raise HTTPException(409, f"Channel '{body.channel_slug}' is already linked to another lounge")

        await db.execute(
            "UPDATE guild_lounges SET nostr_channel_id=?, updated_ts=unixepoch() WHERE id=?",
            (channel_id_str, lounge_id),
        )
        await db.commit()

    return {"lounge_id": lounge_id, "linked_channel_slug": body.channel_slug, "channel_id": channel_id_str}


@router.delete("/{lounge_id}/channel")
async def unlink_channel(
    guild_slug: str,
    lounge_id: str,
    agent=Depends(get_current_agent),
):
    """Remove the channel binding from this lounge."""
    async with get_db() as db:
        await _get_lounge(db, guild_slug, lounge_id)
        await _assert_member(db, lounge_id, agent["id"])
        await db.execute(
            "UPDATE guild_lounges SET nostr_channel_id='', updated_ts=unixepoch() WHERE id=?",
            (lounge_id,),
        )
        await db.commit()
    return {"lounge_id": lounge_id, "unlinked": True}
