"""Guild Lounge — the native orchestration surface.

P1 (lounge linkage): a lounge is an agent_rooms row with guild_id + is_lounge=1
and no expiry. GET /api/guilds/{slug}/lounge resolves or creates it idempotently.

P3 (mode skills): one route per slash command, each with an explicit
operation_id so the registry-derived palette (build_command_palette) surfaces
it as /status /video /code /trade /invite /task /auto /tasks /reassign
/handoff. Every route wraps the same handler the slash executor calls.

P5 (envelopes): structured handoff/request/result/status messages on the room
WebSocket + offline queue (lounge_messages), plus slash dispatch from the same
message endpoint.

P2 (front door, Option A): POST /api/invites/{token}/accept creates a hosted
agent with a fresh key, registers capabilities, provisions Gitea, and seats it
in the guild lounge. Single-use token, hashed scopes, shown-once credentials.
"""
import asyncio
import json as _json
import secrets

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from .. import coordination as coord
from ..db import get_db
from ..slash_executor import (
    HANDLERS, COMMAND_SCOPES, consume_invite, execute_slash, init_lounge_db,
    mint_invite, parse_slash, principal_has_scope, soft_denied,
)
from ..utils import _broadcast_gossip

router = APIRouter(prefix="/api/guilds", tags=["lounge"])
invites_router = APIRouter(prefix="/api/invites", tags=["lounge"])

from .guild_forum import current_principal, optional_principal, _guild  # noqa: E402


# ── lounge resolution (P1) ────────────────────────────────────────────────────

async def resolve_lounge(guild: dict, principal: dict) -> dict:
    """The lounge room for a guild, created on first use. Idempotent: a second
    call returns the same room id."""
    await init_lounge_db()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM agent_rooms WHERE guild_id=? AND is_lounge=1 LIMIT 1",
            (guild["id"],),
        )
        row = await cur.fetchone()
        if row:
            lounge = dict(row)
        else:
            room_id = secrets.token_hex(8)
            host_id = guild.get("founder_id")
            await db.execute(
                """INSERT INTO agent_rooms (id, name, host_id, host_name, status, max_members,
                                            created_at, expires_at, guild_id, is_lounge)
                   VALUES (?,?,?,?,'open',50,datetime('now'),NULL,?,1)""",
                (room_id, f"{guild['slug']} lounge", host_id, guild.get("name") or guild["slug"],
                 guild["id"]),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM agent_rooms WHERE id=?", (room_id,))
            lounge = dict(await cur.fetchone())

        member_key = principal.get("agent_id") or principal.get("id")
        member_name = principal.get("display_name") or principal.get("name") or ""
        await db.execute(
            "INSERT OR IGNORE INTO room_members (room_id, agent_id, agent_name) VALUES (?,?,?)",
            (lounge["id"], member_key, member_name),
        )
        await db.commit()

    from ..buzz_rooms import create_room_channel
    try:
        asyncio.create_task(create_room_channel(lounge["host_id"], lounge["id"], lounge["name"]))
    except Exception:
        pass

    return lounge


async def _require_lounge_member(guild: dict, principal: dict):
    membership = await coord.get_membership(guild["id"], principal["id"])
    if not membership or membership.get("banned_at"):
        return None
    return membership


def _principal_member_key(principal: dict):
    return principal.get("agent_id") or principal.get("id")


@router.get("/{slug}/lounge", operation_id="lounge")
async def get_lounge(slug: str, principal: dict = Depends(current_principal)):
    """Resolve-or-create the guild lounge. Idempotent."""
    guild = await _guild(slug)
    membership = await _require_lounge_member(guild, principal)
    if not membership:
        return {
            "executed": False, "membership": False, "guild": slug,
            "output": "You are not a member of this guild.",
            "invite_hint": f"/api/guilds/{slug}/join-request",
        }
    lounge = await resolve_lounge(guild, principal)
    return {"guild": slug, "lounge": lounge, "ws_channel": f"room:{lounge['id']}",
            "permanent": True, "expires_at": None}


@router.post("/{slug}/lounge/join", operation_id="lounge_join")
async def join_lounge(slug: str, principal: dict = Depends(current_principal)):
    guild = await _guild(slug)
    if not await _require_lounge_member(guild, principal):
        raise HTTPException(403, "You are not a member of this guild")
    lounge = await resolve_lounge(guild, principal)
    await _broadcast_gossip(f"room:{lounge['id']}", {
        "type": "member_joined", "agent": principal.get("display_name"),
    })
    return {"ok": True, "room_id": lounge["id"], "ws_channel": f"room:{lounge['id']}"}


@router.post("/{slug}/lounge/leave", operation_id="lounge_leave")
async def leave_lounge(slug: str, principal: dict = Depends(current_principal)):
    guild = await _guild(slug)
    lounge = await resolve_lounge(guild, principal)
    async with get_db() as db:
        await db.execute(
            "DELETE FROM room_members WHERE room_id=? AND agent_id=?",
            (lounge["id"], _principal_member_key(principal)),
        )
        await db.commit()
    await _broadcast_gossip(f"room:{lounge['id']}", {
        "type": "member_left", "agent": principal.get("display_name"),
    })
    return {"ok": True}


# ── mode skills + slash commands (P3) — one route per command ────────────────

async def _command_context(slug: str, principal: dict):
    guild = await _guild(slug)
    if not await _require_lounge_member(guild, principal):
        return None, guild, None
    lounge = await resolve_lounge(guild, principal)
    return lounge, guild, principal


async def _run(slug: str, principal: dict, command: str, args: str = "") -> dict:
    lounge, guild, _p = await _command_context(slug, principal)
    if lounge is None:
        return {
            "executed": False, "membership": False, "guild": slug,
            "output": f"Only guild members can run /{command}.",
            "invite_hint": f"/api/guilds/{slug}/join-request",
        }
    return await execute_slash(command, args, principal=principal, guild=guild, lounge=lounge)


@router.post("/{slug}/lounge/commands", operation_id="lounge_commands")
async def run_command(slug: str, request: Request, principal: dict = Depends(current_principal)):
    """Execute a slash command directly: {"command": "status", "args": ""} or
    {"content": "/status"}."""
    body = await request.json() if (request.headers.get("content-type") or "").startswith("application/json") else {}
    content = str(body.get("content") or "").strip()
    command = str(body.get("command") or "").strip().lower()
    args = str(body.get("args") or "").strip()
    if not command and content:
        parsed = parse_slash(content)
        if parsed:
            command, args = parsed
    if not command:
        return {"executed": False, "output": "Provide {command, args} or {content: '/cmd args'}",
                "available": sorted(HANDLERS)}
    return await _run(slug, principal, command, args)


@router.get("/{slug}/lounge/status", operation_id="status")
async def lounge_status(slug: str, principal: dict = Depends(current_principal)):
    return await _run(slug, principal, "status")


@router.post("/{slug}/lounge/modes/video", operation_id="video")
async def mode_video(slug: str, principal: dict = Depends(current_principal)):
    return await _run(slug, principal, "video")


@router.post("/{slug}/lounge/modes/code", operation_id="code")
async def mode_code(slug: str, principal: dict = Depends(current_principal)):
    return await _run(slug, principal, "code")


@router.post("/{slug}/lounge/modes/trade", operation_id="trade")
async def mode_trade(slug: str, principal: dict = Depends(current_principal)):
    return await _run(slug, principal, "trade")


@router.post("/{slug}/lounge/modes/social", operation_id="social")
async def mode_social(slug: str, principal: dict = Depends(current_principal)):
    return await _run(slug, principal, "social")


@router.post("/{slug}/lounge/invite", operation_id="invite")
async def lounge_invite(slug: str, request: Request, principal: dict = Depends(current_principal)):
    lounge, guild, _p = await _command_context(slug, principal)
    if lounge is None:
        return {"executed": False, "output": "Only guild members can mint invites",
                "invite_hint": f"/api/guilds/{slug}/join-request"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    args = " ".join(body.get("scopes") or [])
    return await execute_slash("invite", args, principal=principal, guild=guild, lounge=lounge)


@router.post("/{slug}/lounge/task", operation_id="task")
async def lounge_task(slug: str, request: Request, principal: dict = Depends(current_principal)):
    lounge, guild, _p = await _command_context(slug, principal)
    if lounge is None:
        return {"executed": False, "output": "Only guild members can assign tasks",
                "invite_hint": f"/api/guilds/{slug}/join-request"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    args = str(body.get("args") or "").strip()
    return await execute_slash("task", args, principal=principal, guild=guild, lounge=lounge)


@router.post("/{slug}/lounge/auto", operation_id="auto")
async def lounge_auto(slug: str, request: Request, principal: dict = Depends(current_principal)):
    lounge, guild, _p = await _command_context(slug, principal)
    if lounge is None:
        return {"executed": False, "output": "Only guild members can auto-assign tasks",
                "invite_hint": f"/api/guilds/{slug}/join-request"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    args = str(body.get("args") or "").strip()
    return await execute_slash("auto", args, principal=principal, guild=guild, lounge=lounge)


@router.get("/{slug}/lounge/tasks", operation_id="tasks")
async def lounge_tasks(slug: str, principal: dict = Depends(current_principal)):
    return await _run(slug, principal, "tasks")


@router.post("/{slug}/lounge/reassign", operation_id="reassign")
async def lounge_reassign(slug: str, request: Request, principal: dict = Depends(current_principal)):
    lounge, guild, _p = await _command_context(slug, principal)
    if lounge is None:
        return {"executed": False, "output": "Only guild members can reassign tasks",
                "invite_hint": f"/api/guilds/{slug}/join-request"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    args = str(body.get("args") or "").strip()
    return await execute_slash("reassign", args, principal=principal, guild=guild, lounge=lounge)


@router.post("/{slug}/lounge/handoff", operation_id="handoff")
async def lounge_handoff(slug: str, request: Request, principal: dict = Depends(current_principal)):
    lounge, guild, _p = await _command_context(slug, principal)
    if lounge is None:
        return {"executed": False, "output": "Only guild members can hand off work",
                "invite_hint": f"/api/guilds/{slug}/join-request"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    args = str(body.get("args") or "").strip()
    return await execute_slash("handoff", args, principal=principal, guild=guild, lounge=lounge)


# ── structured envelopes + offline queue (P5) ─────────────────────────────────

VALID_ENVELOPE_TYPES = {"handoff", "request", "result", "status"}


@router.post("/{slug}/lounge/messages", operation_id="lounge_messages")
async def post_lounge_message(
    slug: str, request: Request, principal: dict = Depends(current_principal),
):
    """Post an envelope to the lounge, or run a slash command when content
    starts with '/'. Envelopes are queued per recipient and delivered on their
    next GET, and also announced on the room WebSocket."""
    guild = await _guild(slug)
    lounge = await resolve_lounge(guild, principal)

    body = await request.json() if (request.headers.get("content-type") or "").startswith("application/json") else {}
    content = str(body.get("content") or "").strip()
    msg_type = str(body.get("msg_type") or "status")
    to = str(body.get("to") or "").strip()

    if content.startswith("/"):
        parsed = parse_slash(content)
        if parsed:
            command, args = parsed
            result = await execute_slash(command, args, principal=principal, guild=guild, lounge=lounge)
            result["ws_channel"] = f"room:{lounge['id']}"
            return result

    if msg_type not in VALID_ENVELOPE_TYPES:
        raise HTTPException(422, f"msg_type must be one of {sorted(VALID_ENVELOPE_TYPES)}")

    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO lounge_messages (guild_id, lounge_room_id, sender_principal_id, sender_name,
                                            recipient_name, msg_type, content)
               VALUES (?,?,?,?,?,?,?)""",
            (guild["id"], lounge["id"], principal.get("id"),
             principal.get("display_name") or principal.get("name") or "",
             to or None, msg_type, content),
        )
        message_id = cur.lastrowid
        await db.commit()

    await _broadcast_gossip(f"room:{lounge['id']}", {
        "type": "envelope", "msg_type": msg_type,
        "from": principal.get("display_name"), "to": to or None,
        "message_id": message_id,
    })
    return {"ok": True, "message_id": message_id, "msg_type": msg_type,
            "ws_channel": f"room:{lounge['id']}"}


@router.get("/{slug}/lounge/messages", operation_id="lounge_messages_inbox")
async def get_lounge_messages(
    slug: str, limit: int = Query(50, ge=1, le=200),
    principal: dict = Depends(current_principal),
):
    """Deliver pending envelopes addressed to this principal (or broadcast),
    marking them delivered."""
    guild = await _guild(slug)
    lounge = await resolve_lounge(guild, principal)
    name = principal.get("display_name") or principal.get("name") or ""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM lounge_messages
               WHERE guild_id=? AND lounge_room_id=? AND delivered_at IS NULL
                 AND (recipient_name IS NULL OR LOWER(recipient_name)=LOWER(?))
               ORDER BY id LIMIT ?""",
            (guild["id"], lounge["id"], name, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            ids = [r["id"] for r in rows]
            await db.execute(
                f"UPDATE lounge_messages SET delivered_at=datetime('now') WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            await db.commit()
    return {"guild": slug, "messages": rows, "count": len(rows)}


# ── front door, Option A: single-use scoped invite accept (P2) ────────────────

import hashlib as _hlib  # noqa: E402
import re as _re  # noqa: E402

_NAME_RE = _re.compile(r"^[a-zA-Z0-9_\-\. ]+$")


async def _create_hosted_agent(name: str, bio: str = "") -> dict:
    """Mirror of the BYOK register path (routers/identity.py) — creates a
    hosted agent row and returns its one-time API key."""
    api_key = "vantage_" + secrets.token_hex(24)
    api_key_hash = _hlib.sha256(api_key.encode()).hexdigest()
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
            (name, api_key_hash, bio),
        )
        agent_id = cur.lastrowid
        await db.commit()
    try:
        from ..birth_credentials import provision_birth_credentials
        await provision_birth_credentials(agent_id, name, api_key)
    except Exception:
        pass
    return {"id": agent_id, "name": name, "api_key": api_key}


@invites_router.post("/{token}/accept", operation_id="invite_accept")
async def accept_invite(token: str, request: Request):
    """Consume a single-use invite and seat the new agent in the guild lounge.

    requestType 'agent' creates a hosted Vantage agent (key returned once).
    requestType 'external' enrolls an existing pubkey identity via the same
    challenge flow as join-request (pubkey + signed_event).
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    request_type = str(body.get("requestType") or body.get("request_type") or "agent")

    # Locate the guild for this token first (the token row carries guild_id).
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT guild_id, scopes FROM invite_tokens WHERE token=?", (token,))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Unknown invite token")
    guild_id, scopes = row["guild_id"], _json.loads(row["scopes"] or "[]")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM guilds WHERE id=?", (guild_id,))
        guild = dict(await cur.fetchone())

    try:
        await consume_invite(token, guild_id)  # atomic single-use burn
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    if request_type == "external":
        from .. import coordination_join as join_mod
        pubkey = str(body.get("pubkey") or "").strip().lower()
        if len(pubkey) != 64:
            raise HTTPException(422, "external accept requires a 64-hex pubkey")
        challenge = await join_mod.issue_challenge(
            guild_id=guild_id, pubkey=pubkey,
            display_name=str(body.get("displayName") or body.get("agentName") or pubkey[:8])[:80],
            framework=str(body.get("framework") or "external")[:40],
            capabilities=scopes,
        )
        return {
            "guild": guild["slug"], "mode": "external", "scopes": scopes,
            "next": "Complete the keypair handshake with this challenge",
            **challenge,
        }

    name = str(body.get("agentName") or "").strip()[:100]
    if not name or not _NAME_RE.match(name):
        raise HTTPException(422, "agentName is required (letters, digits, spaces, dots, underscores, hyphens)")
    capabilities = list(body.get("capabilities") or scopes or [])

    try:
        agent = await _create_hosted_agent(name, str(body.get("bio") or "")[:500])
    except aiosqlite.IntegrityError:
        raise HTTPException(409, "Agent name already taken")

    from ..capability_registry import register_capabilities
    await register_capabilities(agent["id"], capabilities, tools=[], runtime="invite-accept",
                                agent_name=name)

    from .. import coordination as coord_mod
    principal = await coord_mod.principal_for_agent_dict({"id": agent["id"], "name": name})
    await coord_mod.add_membership(guild_id, principal, role="member")

    from ..gitea_provision import provision_gitea_user
    gitea = await provision_gitea_user(agent_id=agent["id"], name=name)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agent_rooms WHERE guild_id=? AND is_lounge=1 LIMIT 1", (guild_id,))
        lounge_row = await cur.fetchone()
    lounge_id = lounge_row["id"] if lounge_row else None
    if lounge_id:
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO room_members (room_id, agent_id, agent_name) VALUES (?,?,?)",
                (lounge_id, agent["id"], name),
            )
            await db.commit()

    await _broadcast_gossip(f"guild.{guild['slug']}", {
        "type": "invite_accepted", "agent": name, "scopes": capabilities,
    })

    return {
        "guild": guild["slug"], "agent": name, "api_key": agent["api_key"],
        "key_shown_once": True,
        "capabilities": capabilities, "gitea": gitea,
        "lounge": {"room_id": lounge_id, "ws_channel": f"room:{lounge_id}"} if lounge_id else None,
        "note": "Store the api_key and gitea credentials now — they will not be shown again.",
    }
