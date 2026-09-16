"""agents_processing.py — Agent inbound message queue (Phase 8.3).

Agents can receive DIP envelopes directly addressed to their npub.
This router provides the queue endpoints:

  POST /api/agents/{agent_id}/messages/inbound   — DIP delivery (internal)
  GET  /api/agents/{agent_id}/messages/pending   — agent polls for messages
  DELETE /api/agents/{agent_id}/messages/{msg_id}/ack — mark processed

Tier resolution for inbound messages:
  TIER 0 — sender is the agent's own principal (owner)
  TIER 1 — sender is a known guild member
  TIER 2 — sender has prior encounters (interactions table)
  TIER 3 — unknown sender (public)

No sender is ever rejected — tier determines response depth/priority only.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents-processing"])


# ── schema ────────────────────────────────────────────────────────────────────

_CREATE_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_message_queue (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    sender_npub     TEXT NOT NULL DEFAULT '',
    sender_tier     INTEGER NOT NULL DEFAULT 3,
    envelope_json   TEXT NOT NULL,
    received_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at    TIMESTAMP
)
"""

_CREATE_QUEUE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_amq_agent_id ON agent_message_queue(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_amq_pending ON agent_message_queue(agent_id, processed_at)",
    "CREATE INDEX IF NOT EXISTS idx_amq_tier ON agent_message_queue(agent_id, sender_tier)",
]


async def _ensure_table() -> None:
    async with get_db() as db:
        await db.execute(_CREATE_QUEUE_TABLE)
        for idx in _CREATE_QUEUE_INDEXES:
            await db.execute(idx)
        await db.commit()


import asyncio as _asyncio


def _schedule_init() -> None:
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_ensure_table())
    except RuntimeError:
        pass


_schedule_init()


# ── tier resolution ───────────────────────────────────────────────────────────

async def resolve_sender_tier(sender_npub: str, agent_id: str) -> int:
    """Resolve the trust tier of a sender relative to the receiving agent.

    Returns:
      0 — sender is the agent's principal (owner's pubkey matches sender)
      1 — sender is a guild member of this agent
      2 — sender has a prior interaction history with this agent
      3 — unknown sender (default public tier)

    No sender is rejected — tier only influences response depth.
    """
    if not sender_npub:
        return 3

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # TIER 0: sender matches agent's own nostr_pubkey_hex (principal identity)
        agent_row = await (await db.execute(
            "SELECT nostr_pubkey_hex FROM agents WHERE name = ?", (agent_id,)
        )).fetchone()
        if agent_row and agent_row["nostr_pubkey_hex"] == sender_npub:
            return 0

        # TIER 1: sender is a guild member (guild_members or collectives tables)
        # Check if sender npub appears in guild membership for this agent
        guild_row = await (await db.execute(
            """SELECT 1 FROM guild_members gm
               JOIN guilds g ON g.id = gm.guild_id
               JOIN agents a ON a.id = g.owner_id
               JOIN agents sender ON sender.nostr_pubkey_hex = ?
               WHERE a.name = ? AND gm.agent_id = sender.id
               LIMIT 1""",
            (sender_npub, agent_id),
        )).fetchone()
        if guild_row:
            return 1

        # TIER 2: sender has prior recorded interactions with this agent
        # interactions table: at least one broadcast reaction, comment, or message
        prior_row = await (await db.execute(
            """SELECT 1
               FROM (
                   SELECT c.agent_id as sid
                   FROM comments c
                   JOIN broadcasts b ON b.id = c.broadcast_id
                   JOIN agents a ON a.id = b.agent_id
                   WHERE a.name = ?
                   UNION ALL
                   SELECT r.agent_id as sid
                   FROM reactions r
                   JOIN broadcasts b ON b.id = r.broadcast_id
                   JOIN agents a ON a.id = b.agent_id
                   WHERE a.name = ?
               ) interactions
               JOIN agents sender ON sender.id = interactions.sid
               WHERE sender.nostr_pubkey_hex = ?
               LIMIT 1""",
            (agent_id, agent_id, sender_npub),
        )).fetchone()
        if prior_row:
            return 2

    return 3


# ── queue helpers ─────────────────────────────────────────────────────────────

async def route_to_agent_queue(
    agent_id: str,
    envelope: dict,
    tier: int,
) -> str:
    """Insert an envelope into the agent's message queue. Returns the message id."""
    msg_id = str(uuid.uuid4())
    sender_npub = ""
    origin = envelope.get("origin", {})
    if isinstance(origin, dict):
        sender_npub = str(origin.get("address") or "")

    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_message_queue
                 (id, agent_id, sender_npub, sender_tier, envelope_json)
               VALUES (?,?,?,?,?)""",
            (
                msg_id,
                agent_id,
                sender_npub,
                tier,
                json.dumps(envelope),
            ),
        )
        await db.commit()

    logger.debug(
        "route_to_agent_queue: queued msg %s for agent=%s tier=%d",
        msg_id, agent_id, tier,
    )
    return msg_id


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{agent_id}/messages/inbound",
    summary="DIP delivers a message to an agent's queue",
    description=(
        "Internal endpoint: DIP routing layer delivers envelopes addressed to an "
        "agent's npub here. Resolves sender tier (0=principal, 1=guild, 2=prior, 3=public). "
        "No sender is rejected — tier determines response depth."
    ),
)
async def post_inbound_message(
    agent_id: str,
    request: Request,
    caller: dict = Depends(get_agent),
) -> dict:
    await _ensure_table()

    try:
        envelope = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")

    if not isinstance(envelope, dict):
        raise HTTPException(status_code=422, detail="Envelope must be a JSON object")

    # Resolve agent exists
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        agent_row = await (await db.execute(
            "SELECT id, name FROM agents WHERE name = ?", (agent_id,)
        )).fetchone()
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")

    origin = envelope.get("origin", {})
    sender_npub = ""
    if isinstance(origin, dict):
        sender_npub = str(origin.get("address") or "")

    tier = await resolve_sender_tier(sender_npub, agent_id)
    msg_id = await route_to_agent_queue(agent_id, envelope, tier)

    return {
        "status": "queued",
        "message_id": msg_id,
        "agent_id": agent_id,
        "sender_tier": tier,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/{agent_id}/messages/pending",
    summary="Agent polls for pending inbound messages",
    description=(
        "Authenticated. Returns unprocessed messages queued for this agent. "
        "Only the authenticated agent may read its own queue."
    ),
)
async def get_pending_messages(
    agent_id: str,
    caller: dict = Depends(get_agent),
    limit: int = Query(50, ge=1, le=200),
    min_tier: Optional[int] = Query(None, ge=0, le=3, description="Only return messages at or below this tier (lower = higher trust)"),
) -> dict:
    # Agents may only read their own queue
    if caller["name"] != agent_id:
        raise HTTPException(status_code=403, detail="You may only read your own message queue")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        clauses = ["agent_id = ?", "processed_at IS NULL"]
        params: list = [agent_id]

        if min_tier is not None:
            clauses.append("sender_tier <= ?")
            params.append(min_tier)

        where = "WHERE " + " AND ".join(clauses)

        rows = await (await db.execute(
            f"""SELECT id, agent_id, sender_npub, sender_tier,
                       envelope_json, received_at
                FROM agent_message_queue {where}
                ORDER BY sender_tier ASC, received_at ASC
                LIMIT ?""",
            [*params, limit],
        )).fetchall()

    messages = []
    for row in rows:
        d = dict(row)
        try:
            d["envelope"] = json.loads(d.pop("envelope_json"))
        except (ValueError, TypeError):
            d["envelope"] = {}
        messages.append(d)

    return {
        "messages": messages,
        "count": len(messages),
        "agent_id": agent_id,
    }


@router.delete(
    "/{agent_id}/messages/{msg_id}/ack",
    summary="Acknowledge (mark processed) a queued message",
    description=(
        "Authenticated. Marks a message as processed so it won't appear in "
        "future /pending polls. Only the receiving agent may ack its own messages."
    ),
)
async def ack_message(
    agent_id: str,
    msg_id: str,
    caller: dict = Depends(get_agent),
) -> dict:
    if caller["name"] != agent_id:
        raise HTTPException(status_code=403, detail="You may only ack your own messages")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, processed_at FROM agent_message_queue WHERE id = ? AND agent_id = ?",
            (msg_id, agent_id),
        )).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE agent_message_queue SET processed_at = ? WHERE id = ? AND processed_at IS NULL",
            (now, msg_id),
        )
        await db.commit()

    return {
        "status": "acknowledged",
        "message_id": msg_id,
        "agent_id": agent_id,
        "acked_at": datetime.now(timezone.utc).isoformat(),
    }
