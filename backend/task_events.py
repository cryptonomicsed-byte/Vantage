"""Chat is the audit log; the board is the index (G5).

Every task mutation, whatever route it came through, is announced in the
channel that backs the task's space as a system message carrying the
work reference. A reader of the room sees the whole loop (created,
claimed, delivered, accepted, rejected) without opening the board, and the
indexer records the reference on each message so the work ledger shows
the same steps.

System messages are relay-first like everything else: when the relay is
down the state change still happens and the announcement is logged as
missed, never written to the index alone (the index must not hold a
message the log does not).
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

from . import coordination as coord
from .db import get_db

logger = logging.getLogger(__name__)

EVENTS = {
    "created": "proposed",
    "claimed": "claimed",
    "released": "proposed",
    "delivered": "in review",
    "accepted": "accepted",
    "rejected": "sent back",
    "published": "published",
    "branched": "branched",
    "review-requested": "review requested",
    "closed": "closed",
}


async def _channel_for(guild_slug: str, channel_slug: Optional[str]) -> Optional[dict]:
    if not channel_slug:
        return None
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT c.* FROM guild_channels c JOIN guilds g ON g.id=c.guild_id WHERE g.slug=? AND c.slug=?",
            (guild_slug, channel_slug),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def space_channel_slug(table: str, space_id: str) -> Optional[str]:
    async with get_db() as db:
        async with db.execute(f"SELECT chat_channel_slug FROM {table} WHERE id=?", (space_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


def describe(event: str, kind: str, ref_id: str, actor: Optional[str], title: str = "", note: str = "") -> str:
    state = EVENTS.get(event, event)
    who = f" by {actor}" if actor else ""
    text = f"[task.{event}] {kind}:{ref_id} {state}{who}"
    if title:
        text += f" — {title[:120]}"
    if note:
        text += f" ({note[:200]})"
    return text


async def emit(
    *, guild_slug: str, channel_slug: Optional[str], event: str, kind: str, ref_id: str,
    actor: Optional[dict] = None, title: str = "", note: str = "",
) -> Optional[dict]:
    """Announce a task event in its channel. Never raises."""
    channel = await _channel_for(guild_slug, channel_slug)
    if channel is None:
        logger.debug("task_events: no channel %r in %s for %s:%s", channel_slug, guild_slug, kind, ref_id)
        return None
    text = describe(event, kind, ref_id, (actor or {}).get("name"), title, note)
    try:
        return await coord.publish_system_message(
            channel=channel, guild_slug=guild_slug, text=text, work_ref=f"{kind}:{ref_id}",
        )
    except Exception as exc:
        logger.info("task_events: %s not announced in %s/%s: %s", f"{kind}:{ref_id}", guild_slug, channel_slug, exc)
        return None


async def emit_for_space(*, guild_slug: str, table: str, space_id: str, event: str, kind: str,
                         ref_id: str, actor: Optional[dict] = None, title: str = "", note: str = ""):
    """Convenience for the space routers: resolve the space's channel first."""
    return await emit(guild_slug=guild_slug, channel_slug=await space_channel_slug(table, space_id),
                      event=event, kind=kind, ref_id=ref_id, actor=actor, title=title, note=note)
