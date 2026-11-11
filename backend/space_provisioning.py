"""Space chat channels go through the coordination layer (G4).

A trading or content space has a backing chat channel. The space routers
used to create it with a raw INSERT into guild_channels and never asked
the relay for one, so every space channel sat with a NULL buzz_channel_id
and posting into it returned 503 forever. Worse, the INSERT could fail
quietly and leave chat_channel_slug NULL on the space itself, which is what
the audit of 2026-11-10 found on both of Bino's spaces.

This module is the one path: the channel row, the space pointer and the
relay channel are created together, the row carries the space it belongs
to, and the relay step is retried through the same function. Relay
provisioning stays best-effort (the relay may be down), but the result
says so, and a space is never left without its channel row.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

from . import coordination as coord
from .db import get_db
from .guild_access import guild_by_slug

logger = logging.getLogger(__name__)

SPACE_TABLES = {
    "trading": ("trading_spaces", "ts"),
    "content": ("content_spaces", "cs"),
}


async def init_space_provisioning_db() -> None:
    """guild_channels.space_id: which space a channel backs. Tolerant ALTER."""
    async with get_db() as db:
        try:
            await db.execute("ALTER TABLE guild_channels ADD COLUMN space_id TEXT DEFAULT NULL")
        except Exception:
            pass  # already present
        try:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gchannels_space ON guild_channels(space_id)")
        except Exception as exc:
            logger.debug("space_provisioning: index skipped: %s", exc)
        await db.commit()


def chat_slug_for(kind: str, space_id: str) -> str:
    return f"{SPACE_TABLES[kind][1]}-{space_id[:8]}"


async def provision_space_channel(
    *, slug: str, kind: str, space_id: str, name: str, agent: Optional[dict],
) -> dict:
    """Ensure the channel row, the space pointer and (best-effort) the relay
    channel exist for a space. Idempotent: safe to call again to retry the
    relay step.

    Returns ``{"chat_channel_slug", "channel_id", "relay_provisioned", "note"}``.
    """
    table, _ = SPACE_TABLES[kind]
    guild = await guild_by_slug(slug)
    chat_slug = chat_slug_for(kind, space_id)

    principal = None
    if agent is not None:
        try:
            principal = await coord.get_or_create_agent_principal(agent["id"])
        except Exception as exc:
            logger.warning("space_provisioning: no principal for agent %s: %s", agent.get("id"), exc)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT OR IGNORE INTO guild_channels
                 (guild_id, slug, name, topic, channel_kind, flow_mode, visibility,
                  collab_type, created_by_principal_id, space_id)
               VALUES (?, ?, ?, ?, 'forum', 'open', 'members', ?, ?, ?)""",
            (guild["id"], chat_slug, name, f"{kind.capitalize()} space chat: {name}",
             kind, principal["id"] if principal else None, space_id),
        )
        await db.execute(
            "UPDATE guild_channels SET space_id=? WHERE guild_id=? AND slug=? AND space_id IS NULL",
            (space_id, guild["id"], chat_slug),
        )
        await db.execute(f"UPDATE {table} SET chat_channel_slug=? WHERE id=?", (chat_slug, space_id))
        await db.commit()

    channel = await coord.get_channel(guild["id"], chat_slug)
    relay_id = channel.get("buzz_channel_id") if channel else None
    note = None
    if channel and not relay_id:
        if principal is None:
            note = "Channel row created; relay provisioning needs a principal that Vantage can sign for"
        else:
            relay_id = await coord.provision_channel_on_relay(channel, principal, slug)
            if not relay_id:
                note = ("Channel row created but the relay did not provision it; retry with "
                        f"POST /api/guilds/{slug}/{kind}-spaces/{space_id}/provision")
    return {
        "chat_channel_slug": chat_slug,
        "channel_id": channel["id"] if channel else None,
        "relay_provisioned": bool(relay_id),
        "note": note,
    }


async def backfill_space_channels() -> int:
    """Give every space without a chat channel its channel row at startup.

    Database only: the relay step is left to the provision route, so a relay
    outage cannot slow or fail startup. Returns the number of spaces fixed.
    """
    fixed = 0
    for kind, (table, _) in SPACE_TABLES.items():
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            try:
                cur = await db.execute(
                    f"SELECT id, guild_slug, name, created_by_agent_id FROM {table} "
                    "WHERE chat_channel_slug IS NULL OR chat_channel_slug=''"
                )
                rows = [dict(r) for r in await cur.fetchall()]
            except Exception as exc:
                logger.debug("space_provisioning: %s not ready: %s", table, exc)
                continue
        for row in rows:
            try:
                chat_slug = chat_slug_for(kind, row["id"])
                async with get_db() as db:
                    cur = await db.execute("SELECT id FROM guilds WHERE slug=?", (row["guild_slug"],))
                    g = await cur.fetchone()
                    if not g:
                        continue
                    await db.execute(
                        """INSERT OR IGNORE INTO guild_channels
                             (guild_id, slug, name, topic, channel_kind, flow_mode, visibility, collab_type, space_id)
                           VALUES (?, ?, ?, ?, 'forum', 'open', 'members', ?, ?)""",
                        (g[0], chat_slug, row["name"], f"{kind.capitalize()} space chat: {row['name']}", kind, row["id"]),
                    )
                    await db.execute(f"UPDATE {table} SET chat_channel_slug=? WHERE id=?", (chat_slug, row["id"]))
                    await db.commit()
                fixed += 1
            except Exception as exc:
                logger.warning("space_provisioning: backfill of %s %s failed: %s", kind, row["id"], exc)
    if fixed:
        logger.info("space_provisioning: backfilled %d space channel(s)", fixed)
    return fixed
