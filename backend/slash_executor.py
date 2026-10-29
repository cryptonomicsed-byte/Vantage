"""Slash-command executor for guild lounges and guild channels.

P0 finding: the palette (GET /api/chat/commands) is autocomplete-only — the
frontend inserts `/cmd` as text and posts it as a plain message; nothing ever
executes a command. This module is the missing execution layer.

Commands are registered in COMMANDS. Lounge routes in routers/lounge.py wrap
the same handlers 1:1 so the palette (registry-derived) surfaces them, and
guild_forum.post_channel_message delegates any `/`-prefixed message here.

Scope enforcement: a command may declare a required scope prefix (video, code,
trade). A principal holds capabilities from principals.capabilities plus, for
hosted agents, capability_registry. "all" / "*" / "scope" / "scope:*" / any
"scope:..." capability grants the scope. Members lacking the scope get a soft
error carrying the join/invite link, never a 403-shaped wall.
"""
import asyncio
import json
import logging
import secrets
import uuid

import aiosqlite

from .db import get_db
from .utils import _broadcast_gossip

logger = logging.getLogger(__name__)

MODES = ("video", "code", "trade", "social")

# The scope gate. None = any member of the guild may run it.
COMMAND_SCOPES = {
    "video": "video",
    "code": "code",
    "trade": "trade",
    "social": None,
    "status": None,
    "invite": None,
    "task": None,
    "auto": None,
    "tasks": None,
    "reassign": None,
    "handoff": None,
}


def parse_slash(content: str):
    """Return (command, args) for a `/cmd args...` message, else None."""
    text = (content or "").strip()
    if not text.startswith("/"):
        return None
    parts = text[1:].split(None, 1)
    if not parts:
        return None
    cmd = parts[0].strip().lower()[:48]
    args = parts[1].strip() if len(parts) > 1 else ""
    return cmd, args


async def principal_capabilities(principal: dict) -> set:
    caps: set = set()
    raw = principal.get("capabilities")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            raw = []
    if isinstance(raw, list):
        caps.update(str(c) for c in raw)
    agent_id = principal.get("agent_id")
    if agent_id:
        try:
            from .capability_registry import get_agent_capabilities

            record = await get_agent_capabilities(agent_id)
            caps.update(str(c) for c in (record.get("capabilities") or []))
        except Exception as exc:  # registry unavailable must not block chat
            logger.debug("slash_executor: capability lookup skipped: %s", exc)
    return caps


async def principal_has_scope(principal: dict, scope: str | None) -> bool:
    if not scope:
        return True
    caps = await principal_capabilities(principal)
    if "all" in caps or "*" in caps:
        return True
    if scope in caps or f"{scope}:*" in caps:
        return True
    return any(c.startswith(f"{scope}:") for c in caps)


def soft_denied(scope: str, slug: str) -> dict:
    return {
        "executed": False,
        "denied": scope,
        "output": (
            f"You need the '{scope}' scope for this mode. Ask a guild admin for "
            f"a scoped invite, or join via /api/guilds/{slug}/join-request."
        ),
        "invite_hint": f"/api/guilds/{slug}/join-request",
    }


# ── invite tokens (Option A — revocable one-shot URLs) ───────────────────────

async def init_lounge_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS invite_tokens (
                token TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                scopes TEXT DEFAULT '[]',
                created_by_id INTEGER,
                created_by_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT DEFAULT (datetime('now', '+7 days')),
                consumed_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lounge_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                lounge_room_id TEXT NOT NULL,
                sender_principal_id INTEGER,
                sender_name TEXT NOT NULL,
                recipient_name TEXT,
                msg_type TEXT NOT NULL DEFAULT 'status',
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                delivered_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_gitea (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER,
                principal_id INTEGER,
                gitea_username TEXT NOT NULL,
                gitea_uid INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_lounge_messages_pending "
                         "ON lounge_messages(guild_id, recipient_name, delivered_at)")
        await db.commit()


async def mint_invite(
    guild_id: int, created_by_id: int | None, created_by_name: str,
    scopes: list | None = None,
) -> dict:
    await init_lounge_db()
    token = "vlg_" + secrets.token_hex(16)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO invite_tokens (token, guild_id, scopes, created_by_id, created_by_name)
               VALUES (?,?,?,?,?)""",
            (token, guild_id, json.dumps(scopes or []), created_by_id, created_by_name or ""),
        )
        await db.commit()
    return {"token": token, "url": f"/invite/{token}", "scopes": scopes or []}


async def consume_invite(token: str, guild_id: int) -> dict:
    """Atomically burn a single-use invite. Returns its row or raises ValueError."""
    await init_lounge_db()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM invite_tokens WHERE token=?", (token,))
        row = await cur.fetchone()
        if not row:
            raise ValueError("unknown invite token")
        row = dict(row)
        if row["guild_id"] != guild_id:
            raise ValueError("invite was issued for a different guild")
        if row["consumed_at"]:
            raise ValueError("invite has already been used")
        if row["expires_at"] and row["expires_at"] < (await _now_iso()):
            raise ValueError("invite has expired")
        cur = await db.execute(
            "UPDATE invite_tokens SET consumed_at=datetime('now') WHERE token=? AND consumed_at IS NULL",
            (token,),
        )
        await db.commit()
        if cur.rowcount != 1:
            raise ValueError("invite has already been used")
    return row


async def _now_iso() -> str:
    async with get_db() as db:
        cur = await db.execute("SELECT datetime('now') AS now")
        return (await cur.fetchone())[0]


# ── handlers (shared by the executor and the lounge routes) ──────────────────

async def _lounge_for_guild(guild: dict, principal: dict) -> dict:
    from .routers import lounge as lounge_mod

    return await lounge_mod.resolve_lounge(guild, principal)


async def cmd_status(ctx: dict, args: str = "") -> dict:
    guild = ctx["guild"]
    lounge = ctx["lounge"]
    from .capability_registry import get_agent_capabilities

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT agent_name, joined_at FROM room_members WHERE room_id=? ORDER BY joined_at",
            (lounge["id"],),
        )
        members = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT status, COUNT(*) AS n FROM guild_tasks WHERE guild_id=? GROUP BY status",
            (guild["id"],),
        )
        task_rows = {r["status"]: r["n"] for r in await cur.fetchall()}
        cur = await db.execute(
            "SELECT key, value FROM agent_state WHERE key LIKE ?",
            (f"room:{lounge['id']}:lounge:active_modes",),
        )
        modes_row = await cur.fetchone()
    modes = []
    if modes_row:
        try:
            modes = json.loads(modes_row["value"] or "[]")
        except ValueError:
            modes = []
    caps = await principal_capabilities(ctx["principal"])
    output = (
        f"Lounge: {guild['slug']} ({lounge['id']})\n"
        f"Modes active: {', '.join(modes) or 'none'}\n"
        f"Members ({len(members)}): {', '.join(m['agent_name'] for m in members)}\n"
        f"Tasks: {task_rows or {'none': 0}}\n"
        f"Your capabilities: {', '.join(sorted(caps)) or 'none declared'}"
    )
    return {"executed": True, "command": "status", "output": output, "modes": modes, "members": members}


async def cmd_mode(ctx: dict, args: str = "", mode: str = "") -> dict:
    guild = ctx["guild"]
    lounge = ctx["lounge"]
    scope = COMMAND_SCOPES.get(mode)
    if scope and not await principal_has_scope(ctx["principal"], scope):
        return soft_denied(scope, guild["slug"])

    # ── P7 optional connectors (additive) ────────────────────────────────────
    # /video --connector higgsfield <prompt>  → external generation backend
    # /trade --connector tradingview <symbol> → external signal inbox
    connector = None
    args = (args or "").strip()
    if "--connector" in args:
        parts = args.split()
        idx = parts.index("--connector")
        if idx + 1 < len(parts):
            connector = parts[idx + 1].lower()
            rest = [p for i, p in enumerate(parts) if i not in (idx, idx + 1)]
            args = " ".join(rest)
    if connector and connector not in ("higgsfield", "tradingview"):
        return {"executed": False, "output": f"Unknown connector '{connector}' — known: higgsfield, tradingview"}

    if mode == "video" and connector == "higgsfield":
        from .connectors import higgsfield
        gen = await higgsfield.generate_text_to_video(args or "default lounge scene")
        await _broadcast_gossip(f"room:{lounge['id']}", {
            "type": "connector_result", "connector": "higgsfield", "result": gen,
            "actor": ctx["principal"].get("display_name"),
        })
        if gen.get("configured") is False:
            output = f"Higgsfield not configured — {gen.get('note')}. Native AgentTV pipeline remains active."
        elif gen.get("ok"):
            output = f"Higgsfield job {gen.get('job_id')} complete: {gen.get('video_url')}"
        else:
            output = f"Higgsfield job failed: {gen.get('error')}"
        return {"executed": True, "command": mode, "connector": "higgsfield",
                "connector_result": gen, "output": output}

    if mode == "trade" and connector == "tradingview":
        from .connectors import tradingview
        symbol = args.split()[0] if args.split() else ""
        sig = await tradingview.forward_signal(symbol)
        await _broadcast_gossip(f"room:{lounge['id']}", {
            "type": "connector_result", "connector": "tradingview", "result": sig,
            "actor": ctx["principal"].get("display_name"),
        })
        if sig.get("configured") is False:
            output = f"TradingView connector not configured — {sig.get('note')}"
        elif sig.get("ok"):
            output = f"Signal forwarded for {sig.get('symbol')} {sig.get('side')}"
        else:
            output = f"Signal forward failed: {sig.get('error')}"
        return {"executed": True, "command": mode, "connector": "tradingview",
                "connector_result": sig, "output": output}

    key = f"lounge:active_modes"
    full_key = f"room:{lounge['id']}:{key}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT value FROM agent_state WHERE agent_id=? AND key=?",
            (lounge["host_id"], full_key),
        )
        row = await cur.fetchone()
        modes = json.loads(row["value"]) if row else []
        if mode not in modes:
            modes.append(mode)
        await db.execute(
            """INSERT INTO agent_state (agent_id, key, value, updated_at)
               VALUES (?,?,?,datetime('now'))
               ON CONFLICT(agent_id, key)
               DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
            (lounge["host_id"], full_key, json.dumps(modes)),
        )
        await db.commit()
    await _broadcast_gossip(f"room:{lounge['id']}", {
        "type": "mode_changed", "mode": mode, "active_modes": modes,
        "actor": ctx["principal"].get("display_name"),
    })
    return {"executed": True, "command": mode, "mode": mode, "active_modes": modes,
            "output": f"Mode '{mode}' active. Active modes: {', '.join(modes)}"}


async def cmd_invite(ctx: dict, args: str) -> dict:
    guild = ctx["guild"]
    principal = ctx["principal"]
    scopes = [s for s in (args or "").split() if s in MODES] or ["lounge"]
    inv = await mint_invite(
        guild["id"], principal.get("agent_id") or principal.get("id"),
        principal.get("display_name") or principal.get("name") or "", scopes,
    )
    output = (
        f"Invite minted (single-use, 7 days): {inv['url']}\n"
        f"Scopes: {', '.join(inv['scopes'])}\n"
        f"Accept: POST /api/invites/{inv['token']}/accept"
    )
    await _broadcast_gossip(f"guild.{guild['slug']}", {
        "type": "invite_minted", "scopes": inv["scopes"],
        "by": ctx["principal"].get("display_name"),
    })
    return {"executed": True, "command": "invite", "output": output, **inv}


def _parse_mention(text: str) -> tuple[str | None, str]:
    for word in (text or "").split():
        if word.startswith("@"):
            return word[1:], text.replace(word, "", 1).strip()
    return None, (text or "").strip()


async def _find_member_principal(guild_id: int, name: str) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.* FROM principals p
                 JOIN guild_memberships m ON m.principal_id = p.id
                WHERE m.guild_id = ? AND m.banned_at IS NULL
                  AND LOWER(p.display_name) = LOWER(?) LIMIT 1""",
            (guild_id, name),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def cmd_task(ctx: dict, args: str) -> dict:
    guild = ctx["guild"]
    principal = ctx["principal"]
    target_name, text = _parse_mention(args)
    if not target_name or not text:
        return {"executed": False, "output": "Usage: /task @agent do X (a mention and a description are required)"}
    target = await _find_member_principal(guild["id"], target_name)
    if not target:
        return {"executed": False, "output": f"@{target_name} is not a member of this guild"}
    task_id = uuid.uuid4().hex[:16]
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_tasks (id, guild_id, guild_slug, title, description, status, created_by_id, created_by_name)
               VALUES (?,?,?,?,?,?,?,?)""",
            (task_id, guild["id"], guild["slug"], text[:80], text, "proposed",
             principal.get("agent_id") or principal.get("id"),
             principal.get("display_name") or ""),
        )
        await db.execute(
            """INSERT INTO guild_task_delegations (id, task_id, guild_slug, from_agent_id, from_agent_name,
                                                   to_agent_id, to_agent_name, to_principal_id, instructions, status)
               VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
            (uuid.uuid4().hex[:16], task_id, guild["slug"],
             principal.get("agent_id") or 0, principal.get("display_name") or "",
             target.get("agent_id") or 0, target.get("display_name"), target.get("id"), text),
        )
        await db.commit()
    await _broadcast_gossip(f"guild.{guild['slug']}", {
        "type": "task_assigned", "task_id": task_id, "to": target.get("display_name"),
        "by": principal.get("display_name"),
    })
    return {"executed": True, "command": "task", "task_id": task_id,
            "output": f"Task {task_id} assigned to @{target.get('display_name')}: {text}"}


async def cmd_auto(ctx: dict, args: str) -> dict:
    guild = ctx["guild"]
    principal = ctx["principal"]
    desc = (args or "").strip()
    if not desc:
        return {"executed": False, "output": "Usage: /auto task <description> [tags: video, code, trade, ...]"}
    tags = [w for w in desc.split() if w.startswith(("video:", "code:", "trade:"))]
    required = tags or None
    mode = tags[0].split(":")[0] if len(tags) == 1 else None
    task_id = uuid.uuid4().hex[:16]
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guild_tasks (id, guild_id, guild_slug, title, description, status, created_by_id, created_by_name)
               VALUES (?,?,?,?,?,?,?,?)""",
            (task_id, guild["id"], guild["slug"], desc[:80], desc, "proposed",
             principal.get("agent_id") or principal.get("id"),
             principal.get("display_name") or ""),
        )
        await db.commit()
    from .task_router import auto_assign

    best = await auto_assign(task_id, required or [], mode=mode)
    if not best:
        return {"executed": False, "task_id": task_id,
                "output": f"Task {task_id} created but no capable agent is available (required: {required or 'any'})"}
    async with get_db() as db:
        await db.execute(
            "UPDATE guild_tasks SET status='claimed', claimed_by_id=?, claimed_by_name=? WHERE id=?",
            (best.get("agent_id"), best.get("agent_name") or best.get("name"), task_id),
        )
        await db.execute(
            """INSERT INTO guild_task_delegations (id, task_id, guild_slug, from_agent_id, from_agent_name,
                                                   to_agent_id, to_agent_name, instructions, status)
               VALUES (?,?,?,?,?,?,?,?,'pending')""",
            (uuid.uuid4().hex[:16], task_id, guild["slug"],
             principal.get("agent_id") or 0, principal.get("display_name") or "",
             best.get("agent_id") or 0, best.get("agent_name") or best.get("name"), desc),
        )
        await db.commit()
    await _broadcast_gossip(f"guild.{guild['slug']}", {
        "type": "task_auto_assigned", "task_id": task_id,
        "to": best.get("agent_name") or best.get("name"), "mode": mode,
    })
    return {"executed": True, "command": "auto", "task_id": task_id,
            "assigned": best.get("agent_name") or best.get("name"),
            "output": f"Task {task_id} auto-assigned to @{best.get('agent_name') or best.get('name')}: {desc}"}


async def cmd_tasks(ctx: dict, args: str = "") -> dict:
    guild = ctx["guild"]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, title, status, claimed_by_name, created_by_name, created_at "
            "FROM guild_tasks WHERE guild_id=? ORDER BY created_at DESC LIMIT 20",
            (guild["id"],),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    lines = [f"- {r['id']} [{r['status']}] {r['title']} (by {r['created_by_name']}"
             + (f", claimed: {r['claimed_by_name']}" if r["claimed_by_name"] else "") + ")"
             for r in rows]
    return {"executed": True, "command": "tasks", "tasks": rows,
            "output": "Open tasks:\n" + ("\n".join(lines) if lines else "(none)")}


async def cmd_reassign(ctx: dict, args: str) -> dict:
    guild = ctx["guild"]
    principal = ctx["principal"]
    old_name, rest = _parse_mention(args)
    new_name, _ = _parse_mention(rest)
    if not old_name or not new_name:
        return {"executed": False, "output": "Usage: /reassign @old @new"}
    new_target = await _find_member_principal(guild["id"], new_name)
    if not new_target:
        return {"executed": False, "output": f"@{new_name} is not a member of this guild"}
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM guild_task_delegations WHERE guild_slug=? AND to_agent_name=? "
            "AND status IN ('pending','accepted') ORDER BY created_at DESC LIMIT 1",
            (guild["slug"], old_name),
        )
        row = await cur.fetchone()
        if not row:
            return {"executed": False, "output": f"No open task assigned to @{old_name}"}
        row = dict(row)
        await db.execute(
            "UPDATE guild_task_delegations SET status='cancelled' WHERE id=?",
            (row["id"],),
        )
        await db.execute(
            """INSERT INTO guild_task_delegations (id, task_id, guild_slug, from_agent_id, from_agent_name,
                                                   to_agent_id, to_agent_name, to_principal_id, instructions, status)
               VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
            (uuid.uuid4().hex[:16], row["task_id"], guild["slug"],
             principal.get("agent_id") or 0, principal.get("display_name") or "",
             new_target.get("agent_id") or 0, new_target.get("display_name"),
             new_target.get("id"), row["instructions"]),
        )
        await db.commit()
    await _broadcast_gossip(f"guild.{guild['slug']}", {
        "type": "task_reassigned", "task_id": row["task_id"],
        "from": old_name, "to": new_target.get("display_name"),
    })
    return {"executed": True, "command": "reassign", "task_id": row["task_id"],
            "output": f"Task {row['task_id']} reassigned @{old_name} -> @{new_target.get('display_name')}"}


async def cmd_handoff(ctx: dict, args: str) -> dict:
    guild = ctx["guild"]
    lounge = ctx["lounge"]
    principal = ctx["principal"]
    target_name, text = _parse_mention(args)
    if not target_name:
        return {"executed": False, "output": "Usage: /handoff @agent <summary>"}
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO lounge_messages (guild_id, lounge_room_id, sender_principal_id, sender_name,
                                            recipient_name, msg_type, content)
               VALUES (?,?,?,?,?,?,?)""",
            (guild["id"], lounge["id"], principal.get("id"),
             principal.get("display_name") or principal.get("name") or "",
             target_name, "handoff", text or "handoff"),
        )
        await db.commit()
    await _broadcast_gossip(f"room:{lounge['id']}", {
        "type": "envelope", "msg_type": "handoff",
        "from": principal.get("display_name"), "to": target_name,
    })
    return {"executed": True, "command": "handoff",
            "output": f"Handoff queued for @{target_name}: {text or '(no summary)'}"}


HANDLERS = {
    "status": (cmd_status, {}),
    "video": (cmd_mode, {"mode": "video"}),
    "code": (cmd_mode, {"mode": "code"}),
    "trade": (cmd_mode, {"mode": "trade"}),
    "social": (cmd_mode, {"mode": "social"}),
    "invite": (cmd_invite, {}),
    "task": (cmd_task, {}),
    "auto": (cmd_auto, {}),
    "tasks": (cmd_tasks, {}),
    "reassign": (cmd_reassign, {}),
    "handoff": (cmd_handoff, {}),
}


async def execute_slash(command: str, args: str, *, principal: dict, guild: dict, lounge: dict) -> dict:
    """Run one slash command. Never raises for command-level failures."""
    entry = HANDLERS.get(command)
    if not entry:
        return {
            "executed": False, "unknown": True, "command": command,
            "output": f"Unknown command /{command}. Try /status, /video, /code, /trade, /invite, "
                      f"/task, /auto, /tasks, /reassign, /handoff",
        }
    scope = COMMAND_SCOPES.get(command)
    if scope and not await principal_has_scope(principal, scope):
        return soft_denied(scope, guild["slug"])
    handler, static = HANDLERS[command]
    ctx = {"principal": principal, "guild": guild, "lounge": lounge}
    try:
        result = await handler(ctx, args, **static)
    except Exception as exc:
        logger.exception("slash_executor: /%s failed", command)
        return {"executed": False, "command": command, "error": str(exc),
                "output": f"/{command} failed: {exc}"}
    return result


async def handle_slash_message(content: str, *, principal: dict, guild: dict, lounge: dict) -> dict | None:
    """Full entry point used by message handlers: parse and execute if it is a
    slash command; return None if the content is not a command."""
    parsed = parse_slash(content)
    if not parsed:
        return None
    command, args = parsed
    return await execute_slash(command, args, principal=principal, guild=guild, lounge=lounge)
