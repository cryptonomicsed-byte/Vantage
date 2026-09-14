"""
Vantage Broadcast Intent — Vantage → minipae NIP-AE (kind:30174) pipeline.

When a Vantage event reaches a milestone (agent skill published, trade settled,
governance proposal enacted, etc.) this router publishes a broadcast intent
as a kind:30174 engram on the Nostr relay so minipae subscribers can act on it.

Routes:
  POST /api/intents/broadcast       — publish an intent to the Nostr/minipae bus
  GET  /api/intents/recent          — recent broadcast intents (local log)

Intent kinds (a Vantage extension of the NIP-AE spec):
  skill_published       — new SkillCard available
  trade_settled         — economic settlement confirmed
  governance_enacted    — council proposal executed
  agent_milestone       — agent crossed a trust/tier threshold
  compute_completed     — UCX job finished with receipt
  witness_quorum        — witness round reached quorum
  ase_emission          — ASE tick broadcast (every 60s)
  custom                — caller-supplied intent_kind

Environment:
  NIPAE_NSEC     — Nostr private key (hex or nsec); if unset, broadcast is a no-op
  NIPAE_RELAY    — relay WebSocket URL (default wss://relay.damus.io)
  NIPAE_OWNER    — owner pubkey hex to encrypt to; defaults to agent self
"""

import logging
import time
import uuid
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intents", tags=["broadcast-intent"])

# ── DB ────────────────────────────────────────────────────────────────────────

_TABLE_CREATED = False

async def _ensure_table(db: aiosqlite.Connection) -> None:
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_intents (
            intent_id    TEXT PRIMARY KEY,
            kind         TEXT NOT NULL,
            agent_id     TEXT NOT NULL,
            payload      TEXT NOT NULL,
            nostr_event_id TEXT,
            published    INTEGER NOT NULL DEFAULT 0,
            created_at   REAL NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_bi_kind ON broadcast_intents (kind, created_at DESC)"
    )
    await db.commit()
    _TABLE_CREATED = True


# ── Nostr publish helper ──────────────────────────────────────────────────────

import os
import json as _json
import hashlib
import hmac as _hmac

def _nostr_enabled() -> bool:
    return bool(os.environ.get("NIPAE_NSEC"))

def _relay_url() -> str:
    return os.environ.get("NIPAE_RELAY", "wss://relay.damus.io")

async def _publish_engram(intent_kind: str, payload: dict, agent_id: str) -> Optional[str]:
    """Publish a kind:30174 engram for this broadcast intent.
    Returns the Nostr event_id on success, None if unavailable.
    Fail-open: never raises.
    """
    if not _nostr_enabled():
        return None
    try:
        from ..buzz_engrams import write_engram
        # Convert agent_id (str) to int for buzz_engrams — use hash if non-numeric
        agent_int = int(agent_id) if agent_id.isdigit() else int(hashlib.sha256(agent_id.encode()).hexdigest()[:8], 16)
        slug = f"intent:{intent_kind}:{int(time.time())}"
        value = _json.dumps(payload, default=str)
        result = await write_engram(agent_int, slug, value)
        return result.get("event_id")
    except Exception as e:
        logger.debug("broadcast_intent: engram publish failed (fail-open): %s", e)
        return None


# ── Request model ─────────────────────────────────────────────────────────────

class BroadcastRequest(BaseModel):
    intent_kind: str   # skill_published | trade_settled | … | custom
    payload:     dict  # arbitrary key-value intent data
    # Optional override for the Nostr publish target
    target_pubkey: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/broadcast",
    summary="Publish a broadcast intent to the Nostr/minipae bus",
)
async def broadcast_intent(
    body: BroadcastRequest,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Records the intent locally and (if NIPAE_NSEC is set) publishes a
    kind:30174 NIP-AE engram to the configured Nostr relay.

    minipae subscribers listening on that relay will receive the intent
    and can execute skills accordingly.
    """
    await _ensure_table(db)

    agent_id   = str(agent.get("id", agent.get("agent_id", "unknown")))
    intent_id  = str(uuid.uuid4())
    now        = time.time()

    # Enrich payload with standard fields
    full_payload = {
        **body.payload,
        "intent_kind": body.intent_kind,
        "agent_id":    agent_id,
        "intent_id":   intent_id,
        "timestamp":   now,
    }

    # Publish to Nostr/minipae (fail-open)
    event_id = await _publish_engram(body.intent_kind, full_payload, agent_id)

    await db.execute(
        """INSERT INTO broadcast_intents
           (intent_id, kind, agent_id, payload, nostr_event_id, published, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (intent_id, body.intent_kind, agent_id,
         _json.dumps(full_payload, default=str),
         event_id, 1 if event_id else 0, now),
    )
    await db.commit()

    return {
        "intent_id":     intent_id,
        "kind":          body.intent_kind,
        "agent_id":      agent_id,
        "nostr_event_id": event_id,
        "published":     event_id is not None,
        "relay":         _relay_url() if _nostr_enabled() else None,
        "timestamp":     now,
    }


@router.get(
    "/recent",
    summary="Recent broadcast intents from this Vantage instance",
)
async def recent_intents(
    kind:  Optional[str] = None,
    limit: int = 50,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_table(db)
    db.row_factory = aiosqlite.Row

    q    = "SELECT * FROM broadcast_intents"
    args: list[Any] = []
    if kind:
        q += " WHERE kind = ?"
        args.append(kind)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(min(limit, 500))

    async with db.execute(q, args) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return {"intents": rows, "count": len(rows)}
