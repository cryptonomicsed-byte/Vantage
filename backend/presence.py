"""Agent presence as protocol state, not socket liveness.

The Conductor already knew who was connected. That answers "is it there",
which is not the question a room full of agents needs answered. Before
handing anyone the next unit of work you need to know whether it is worth
waiting for -- and a principal that is blocked on a human review is present,
responsive, and exactly the wrong one to hand it to.

So presence here is a declared work state with a closed vocabulary. Free-text
status was rejected: a scheduler cannot act on "thinking hard about the
parser", and once arbitrary strings are accepted they can never be withdrawn.

**No new event kind.** NIP-38 already defines kind 30315 for exactly this,
and Vantage already publishes vibe statuses on it. Work state is the same
kind under a different `d` tag, so a Nostr client that knows NIP-38 renders
an agent's working state with no Vantage-specific code at all. See
`backend/nostr_kinds.py`, where `agent_presence` is recorded as considered
and refused for this reason.

The durable copy lives here because the Conductor holds no signing key and
loses its state when a channel process stops; the live copy stays in the
Conductor, which is the only thing fast enough to be authoritative about a
socket.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

from .db import get_db
from .nostr_kinds import kind as kind_number

logger = logging.getLogger(__name__)

KIND_USER_STATUS = kind_number("user_status")

#: The `d` tag that separates work state from the general vibe status that
#: buzz_status.py already publishes on this kind. Both are the principal's
#: current status; they are about different things and must not overwrite
#: each other -- which, for a parameterized replaceable event, is entirely
#: decided by this string.
STATUS_D_TAG = "vantage-work"

# Kept identical to Conductor.Flow's @work_states. The Elixir side is the
# one that refuses an unknown value at the socket; this list is what the
# HTTP side and the durable store agree to.
STATES = ["available", "thinking", "working", "blocked", "needs_review", "offline"]

#: States a scheduler may route new work to. `blocked` and `needs_review`
#: both mean somebody else has to move first.
ROUTABLE = {"available", "thinking"}

DEFAULT_STATE = "available"


async def init_presence_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS principal_presence (
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                channel_id INTEGER,
                state TEXT NOT NULL DEFAULT 'available',
                detail TEXT DEFAULT '',
                work_ref TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (principal_id, channel_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_presence_channel ON principal_presence(channel_id, state)"
        )
        await db.commit()


def is_valid(state: Optional[str]) -> bool:
    return state in STATES


async def set_state(
    *, principal_id: int, state: str, channel_id: Optional[int] = None,
    detail: str = "", work_ref: str = "", mirror: bool = True,
) -> dict:
    """Record a work state, and mirror it to the relay as a NIP-38 status.

    `channel_id` of None is the principal's instance-wide state; a channel id
    scopes it to one room, because an agent can be free in one workspace and
    blocked in another and collapsing those loses the only useful part.
    """
    if not is_valid(state):
        raise ValueError(f"unknown presence state: {state!r} (use one of {', '.join(STATES)})")

    async with get_db() as db:
        # channel_id is part of the primary key and may be NULL, which SQLite
        # does not treat as equal to itself -- so ON CONFLICT never fires for
        # the instance-wide row and it would insert a duplicate every time.
        # Update-then-insert is the portable way to say what is meant.
        if channel_id is None:
            cur = await db.execute(
                """UPDATE principal_presence
                      SET state=?, detail=?, work_ref=?, updated_at=datetime('now')
                    WHERE principal_id=? AND channel_id IS NULL""",
                (state, detail[:200], work_ref[:120], principal_id),
            )
            if not cur.rowcount:
                await db.execute(
                    """INSERT INTO principal_presence
                         (principal_id, channel_id, state, detail, work_ref)
                       VALUES (?,NULL,?,?,?)""",
                    (principal_id, state, detail[:200], work_ref[:120]),
                )
        else:
            await db.execute(
                """INSERT INTO principal_presence
                     (principal_id, channel_id, state, detail, work_ref)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(principal_id, channel_id) DO UPDATE SET
                     state=excluded.state, detail=excluded.detail,
                     work_ref=excluded.work_ref, updated_at=datetime('now')""",
                (principal_id, channel_id, state, detail[:200], work_ref[:120]),
            )
        await db.commit()

    if mirror:
        await _mirror_to_relay(principal_id, state, detail, work_ref)

    return {"principal_id": principal_id, "channel_id": channel_id, "state": state,
            "detail": detail, "work_ref": work_ref, "routable": state in ROUTABLE}


async def _mirror_to_relay(principal_id: int, state: str, detail: str, work_ref: str) -> None:
    """Publish the state as a NIP-38 status. Never raises.

    A principal that holds its own key is skipped rather than signed for --
    the whole point of self-custody is that this instance cannot speak as it,
    and publishing a status on its behalf would be exactly that.
    """
    try:
        from .buzz_client import BuzzSession
        from .buzz_registration import RELAY_WS_URL
        from .coordination import signing_key_for_principal, get_principal

        principal = await get_principal(principal_id)
        if principal is None:
            return
        pk = await signing_key_for_principal(principal)
        if pk is None:
            return

        tags = [["d", STATUS_D_TAG], ["status", state]]
        if work_ref:
            tags.append(["vw", work_ref])

        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(KIND_USER_STATUS, detail or state, tags=tags)
        finally:
            await sess.close()
    except Exception as exc:
        # A mirror that fails must never fail the state change: the durable
        # copy is already written, and the relay will carry the next one.
        logger.debug("presence: relay mirror skipped for %s: %s", principal_id, exc)


async def get_state(principal_id: int, channel_id: Optional[int] = None) -> dict:
    """A principal's state here, falling back to its instance-wide state.

    The fallback is what makes per-channel presence optional: a runtime that
    only ever sets one state still reads correctly in every room.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = None
        if channel_id is not None:
            cur = await db.execute(
                "SELECT * FROM principal_presence WHERE principal_id=? AND channel_id=?",
                (principal_id, channel_id),
            )
            row = await cur.fetchone()
        if row is None:
            cur = await db.execute(
                "SELECT * FROM principal_presence WHERE principal_id=? AND channel_id IS NULL",
                (principal_id,),
            )
            row = await cur.fetchone()
    if row is None:
        return {"principal_id": principal_id, "state": DEFAULT_STATE, "detail": "",
                "work_ref": "", "routable": True, "declared": False}
    row = dict(row)
    return {"principal_id": principal_id, "state": row["state"], "detail": row["detail"] or "",
            "work_ref": row["work_ref"] or "", "routable": row["state"] in ROUTABLE,
            "declared": True, "updated_at": row["updated_at"]}


async def channel_presence(channel_id: int) -> list[dict]:
    """Every principal with a state in this channel, plus their identity."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT pp.*, p.display_name, p.kind AS principal_kind, p.framework
                 FROM principal_presence pp
                 JOIN principals p ON p.id = pp.principal_id
                WHERE pp.channel_id=?
                ORDER BY p.display_name""",
            (channel_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["routable"] = row["state"] in ROUTABLE
    return rows


async def routable_in_guild(guild_id: int) -> list[dict]:
    """Guild members it is worth handing work to right now.

    A member who has never declared a state counts as available: silence is
    not evidence of being blocked, and treating it as such would make the
    scheduler useless on the day this shipped.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.id AS principal_id, p.display_name, p.kind AS principal_kind,
                      p.framework, p.key_custody,
                      COALESCE(pp.state, ?) AS state
                 FROM guild_memberships m
                 JOIN principals p ON p.id = m.principal_id
                 LEFT JOIN principal_presence pp
                   ON pp.principal_id = p.id AND pp.channel_id IS NULL
                WHERE m.guild_id=? AND m.banned_at IS NULL
                ORDER BY p.display_name""",
            (DEFAULT_STATE, guild_id),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return [r for r in rows if r["state"] in ROUTABLE]
