"""Durable voice-session state: sessions, transcripts, and tool-call logs.

Deliberately named apart from voice_session.py (singular), which owns the
legacy HuggingFace-S2S subprocess and keeps its state in a module-level dict.
This module owns the persisted model that every voice surface writes into --
the Gemini Live path in Vantage-Voice-, the cascade fallback, and eventually
the legacy S2S path too -- so a conversation survives a restart and is
visible to the dashboard, the memory vault, and MCP.

Auth model: creating and reading sessions needs the agent's real X-Agent-Key.
Writing turns and tool calls into an open session only needs that session's
own vvoice_ token, which is scoped to exactly one session and can do nothing
else -- same shape as the vault_connectors token, and the replacement for the
voice app's shared owner PIN.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from typing import Any, Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "vvoice_"
DEFAULT_TTL_SECONDS = 1800
MAX_TTL_SECONDS = 86400
VALID_ENGINES = {"gemini_live", "cascade", "huggingface_s2s"}
VALID_ROLES = {"user", "assistant", "system", "tool"}
# Matches the vault ingest caps so a turn that is accepted here is also
# accepted when it is written through to external_conversations.
MAX_TEXT_CHARS = 20000
_MAX_TOOL_JSON_CHARS = 20000


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def derive_exec_token(ws_token: str) -> str:
    """The credential the relay uses to call tools as the agent.

    Derived from the ws token rather than stored or handed out, so it never
    leaves the server: the relay can recompute it because it holds the raw ws
    token for the life of the connection, but a leaked ws URL (query params end
    up in browser history and access logs) does not confer tool execution.

    Keyed on SEED_MASTER_KEY with its own info label, so this can't collide
    with anything else derived from that key.
    """
    from .config import settings
    secret = (getattr(settings, "SEED_MASTER_KEY", "") or "vantage-voice-exec").encode()
    return "vexec_" + hmac.new(secret, b"voice-exec|" + ws_token.encode(), hashlib.sha256).hexdigest()


def _mint_token() -> tuple[str, str]:
    raw = TOKEN_PREFIX + secrets.token_hex(24)
    return raw, _hash_token(raw)


def _clip(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value or "")[:limit]


def _public(row: aiosqlite.Row | dict) -> dict:
    """Session row minus its secret. ws_token_hash never leaves this module."""
    d = dict(row)
    d.pop("ws_token_hash", None)
    d.pop("exec_token_hash", None)
    for key in ("tools_allowlist_json", "metadata_json"):
        if key in d:
            try:
                d[key.removesuffix("_json")] = json.loads(d.pop(key) or "null")
            except (json.JSONDecodeError, TypeError):
                d[key.removesuffix("_json")] = None
    return d


# ── Lifecycle ────────────────────────────────────────────────────────────────

async def create_session(
    agent_id: int,
    engine: str = "gemini_live",
    framework: str = "native",
    persona: str = "",
    voice: str = "",
    tools: Optional[list[str]] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    metadata: Optional[dict] = None,
) -> dict:
    """Open a session and mint its one-time ws token. The raw token is
    returned exactly once and only its hash is stored, matching how agent
    keys and vault connector tokens are handled."""
    if engine not in VALID_ENGINES:
        raise ValueError(f"engine must be one of {sorted(VALID_ENGINES)}")
    try:
        ttl_seconds = int(ttl_seconds)
    except (TypeError, ValueError):
        raise ValueError("ttl_seconds must be an integer")
    if not 60 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be between 60 and {MAX_TTL_SECONDS}")

    session_id = f"vsess_{uuid.uuid4().hex}"
    raw_token, token_hash = _mint_token()
    exec_hash = _hash_token(derive_exec_token(raw_token))

    async with get_db() as db:
        await db.execute(
            """INSERT INTO voice_sessions
               (id, agent_id, engine, framework, persona, voice, tools_allowlist_json,
                status, ttl_seconds, ws_token_hash, exec_token_hash, metadata_json)
               VALUES (?,?,?,?,?,?,?,'active',?,?,?,?)""",
            (
                session_id, agent_id, engine, framework or "native",
                _clip(persona, 100), _clip(voice, 100),
                json.dumps(tools) if tools else None,
                ttl_seconds, token_hash, exec_hash,
                json.dumps(metadata or {}),
            ),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM voice_sessions WHERE id=?", (session_id,))).fetchone()

    out = _public(row)
    out["token"] = raw_token
    return out


async def get_session(session_id: str, agent_id: Optional[int] = None) -> Optional[dict]:
    """Fetch one session. Pass agent_id to scope the lookup to its owner --
    without it a caller who guesses an id could read another agent's session."""
    sql = "SELECT * FROM voice_sessions WHERE id=?"
    params: list = [session_id]
    if agent_id is not None:
        sql += " AND agent_id=?"
        params.append(agent_id)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(sql, tuple(params))).fetchone()
    return _public(row) if row else None


async def list_sessions(agent_id: int, status: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM voice_sessions WHERE agent_id=?"
    params: list = [agent_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(sql, tuple(params))).fetchall()
    return [_public(r) for r in rows]


async def stop_session(session_id: str, agent_id: Optional[int] = None, reason: str = "client_stopped") -> dict:
    """Close a session and burn its ws token. Idempotent: stopping an already
    stopped session reports was_active=False rather than failing, so a client
    retrying after a dropped response doesn't get an error."""
    session = await get_session(session_id, agent_id)
    if not session:
        raise LookupError(session_id)
    if session["status"] in ("stopped", "failed"):
        return {"ok": True, "was_active": False, "session_id": session_id}

    async with get_db() as db:
        await db.execute(
            """UPDATE voice_sessions
               SET status='stopped', stopped_at=datetime('now'), stop_reason=?,
                   ws_token_hash=NULL, exec_token_hash=NULL, last_activity_at=datetime('now')
               WHERE id=?""",
            (_clip(reason, 200), session_id),
        )
        await db.commit()
    return {"ok": True, "was_active": True, "session_id": session_id}


async def resolve_ws_token(token: str) -> Optional[dict]:
    """Map a raw vvoice_ token back to its session, or None.

    Returns None for an unknown, burned, stopped, or idle-expired token --
    callers reject rather than guessing a session, exactly as the legacy
    Responses-API shim does with its own tokens.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT * FROM voice_sessions
               WHERE ws_token_hash=? AND status='active'
                 AND datetime(last_activity_at, '+' || ttl_seconds || ' seconds') > datetime('now')""",
            (_hash_token(token),),
        )).fetchone()
    return _public(row) if row else None


async def resolve_exec_token(token: str) -> Optional[dict]:
    """Map a server-side exec token back to its session, or None.

    Same liveness rules as the ws token: unknown, burned, stopped or
    idle-expired all resolve to None, so a session that has ended cannot keep
    running tools.
    """
    if not token or not token.startswith("vexec_"):
        return None
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT * FROM voice_sessions
               WHERE exec_token_hash=? AND status='active'
                 AND datetime(last_activity_at, '+' || ttl_seconds || ' seconds') > datetime('now')""",
            (_hash_token(token),),
        )).fetchone()
    return _public(row) if row else None


async def touch(session_id: str) -> None:
    """Push the idle deadline out. Called on every accepted write so an
    actively used session doesn't expire mid-conversation."""
    async with get_db() as db:
        await db.execute(
            "UPDATE voice_sessions SET last_activity_at=datetime('now') WHERE id=? AND status='active'",
            (session_id,),
        )
        await db.commit()


async def expire_idle_sessions() -> int:
    """Mark active sessions whose idle deadline has passed as stopped, and burn
    their tokens. Safe to call repeatedly; returns how many it closed."""
    async with get_db() as db:
        cur = await db.execute(
            """UPDATE voice_sessions
               SET status='stopped', stopped_at=datetime('now'), stop_reason='idle_timeout',
                   ws_token_hash=NULL, exec_token_hash=NULL
               WHERE status='active'
                 AND datetime(last_activity_at, '+' || ttl_seconds || ' seconds') <= datetime('now')"""
        )
        await db.commit()
        return cur.rowcount or 0


# ── Transcript ───────────────────────────────────────────────────────────────

async def append_turn(
    session_id: str,
    agent_id: int,
    role: str,
    content_text: str = "",
    content_audio_transcript: str = "",
    content_audio_path: str = "",
    tool_call_id: Optional[str] = None,
) -> dict:
    """Append one turn and index it for search.

    sequence_num is assigned here rather than by the client: two turns racing
    to claim the same position hit the UNIQUE(session_id, sequence_num)
    constraint, and the loser retries against the new maximum instead of
    silently overwriting or forking the transcript.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
    turn_id = f"vturn_{uuid.uuid4().hex}"
    text = _clip(content_text)
    transcript = _clip(content_audio_transcript)
    # What search should match: the spoken words for a user turn, the written
    # reply for an assistant turn.
    indexed = text or transcript

    for attempt in range(5):
        try:
            async with get_db() as db:
                row = await (await db.execute(
                    "SELECT COALESCE(MAX(sequence_num), 0) + 1 FROM voice_session_turns WHERE session_id=?",
                    (session_id,),
                )).fetchone()
                seq = row[0]
                await db.execute(
                    """INSERT INTO voice_session_turns
                       (id, session_id, agent_id, role, content_text, content_audio_transcript,
                        content_audio_path, tool_call_id, sequence_num)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (turn_id, session_id, agent_id, role, text, transcript,
                     _clip(content_audio_path, 500), tool_call_id, seq),
                )
                if indexed:
                    await db.execute(
                        """INSERT INTO voice_session_turns_fts
                           (session_id, agent_id, turn_id, role, content_text)
                           VALUES (?,?,?,?,?)""",
                        (session_id, agent_id, turn_id, role, indexed),
                    )
                await db.commit()
        except aiosqlite.IntegrityError:
            # Lost the race for this sequence_num; retry against the new max.
            if attempt == 4:
                raise
            continue
        except aiosqlite.OperationalError as exc:
            # "database is locked" under concurrent writers. Previously this
            # escaped the retry loop and was swallowed by the caller's broad
            # except, so a turn was lost outright with only a log line -- in
            # the feature whose entire point is that transcripts are durable.
            # Transient by nature, so back off and try again.
            if attempt == 4 or "locked" not in str(exc).lower():
                raise
            await asyncio.sleep(0.05 * (attempt + 1))
            continue
        await touch(session_id)
        return {"turn_id": turn_id, "sequence_num": seq, "session_id": session_id}
    raise RuntimeError("could not assign a sequence number")  # pragma: no cover


async def get_transcript(session_id: str, limit: int = 500, offset: int = 0) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, role, content_text, content_audio_transcript, content_audio_path,
                      tool_call_id, sequence_num, created_at
               FROM voice_session_turns WHERE session_id=?
               ORDER BY sequence_num LIMIT ? OFFSET ?""",
            (session_id, max(1, min(int(limit), 1000)), max(0, int(offset))),
        )).fetchall()
    return [dict(r) for r in rows]


async def search_transcripts(agent_id: int, query: str, limit: int = 25) -> list[dict]:
    """FTS5 search across this agent's voice transcripts.

    The query goes to fts5 as a MATCH expression, so a stray quote or operator
    from a user's spoken search would be a syntax error rather than a result
    set; it is wrapped as a quoted phrase to keep it literal.
    """
    q = (query or "").strip()
    if not q:
        return []
    phrase = '"' + q.replace('"', '""') + '"'
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                """SELECT f.session_id, f.turn_id, f.role,
                          snippet(voice_session_turns_fts, 4, '[', ']', '…', 12) AS snippet,
                          t.created_at, t.sequence_num
                   FROM voice_session_turns_fts f
                   JOIN voice_session_turns t ON t.id = f.turn_id
                   WHERE voice_session_turns_fts MATCH ? AND f.agent_id = ?
                   ORDER BY t.created_at DESC LIMIT ?""",
                (phrase, agent_id, max(1, min(int(limit), 100))),
            )).fetchall()
        except aiosqlite.OperationalError as exc:
            logger.debug("voice transcript search rejected %r: %s", q, exc)
            return []
    return [dict(r) for r in rows]


# ── Tool calls ───────────────────────────────────────────────────────────────

async def record_tool_call(
    session_id: str,
    agent_id: int,
    tool_name: str,
    tool_source: str = "vantage_mcp",
    arguments: Any = None,
    turn_id: Optional[str] = None,
) -> str:
    """Log a dispatched tool call. Written before the call runs so a tool that
    hangs or crashes the session still leaves a record that it was attempted."""
    call_id = f"vtc_{uuid.uuid4().hex}"
    async with get_db() as db:
        await db.execute(
            """INSERT INTO voice_session_tool_calls
               (id, session_id, turn_id, agent_id, tool_name, tool_source, arguments_json)
               VALUES (?,?,?,?,?,?,?)""",
            (call_id, session_id, turn_id, agent_id, _clip(tool_name, 200),
             _clip(tool_source, 50), _clip(json.dumps(arguments, default=str), _MAX_TOOL_JSON_CHARS)),
        )
        await db.commit()
    await touch(session_id)
    return call_id


async def complete_tool_call(call_id: str, result: Any = None, is_error: bool = False,
                             duration_ms: Optional[int] = None) -> None:
    async with get_db() as db:
        await db.execute(
            """UPDATE voice_session_tool_calls
               SET result_json=?, is_error=?, duration_ms=? WHERE id=?""",
            (_clip(json.dumps(result, default=str), _MAX_TOOL_JSON_CHARS),
             1 if is_error else 0, duration_ms, call_id),
        )
        await db.commit()


async def list_tool_calls(session_id: str, limit: int = 200) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, turn_id, tool_name, tool_source, arguments_json, result_json,
                      is_error, duration_ms, created_at
               FROM voice_session_tool_calls WHERE session_id=?
               ORDER BY created_at LIMIT ?""",
            (session_id, max(1, min(int(limit), 500))),
        )).fetchall()
    return [dict(r) for r in rows]


async def session_stats(session_id: str) -> dict:
    async with get_db() as db:
        turns = await (await db.execute(
            "SELECT COUNT(*) FROM voice_session_turns WHERE session_id=?", (session_id,))).fetchone()
        calls = await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_error),0) FROM voice_session_tool_calls WHERE session_id=?",
            (session_id,))).fetchone()
    return {"turn_count": turns[0], "tool_call_count": calls[0], "tool_error_count": calls[1]}
