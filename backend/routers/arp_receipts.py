"""ARP (Action Receipt Protocol) ingest endpoint.

Accepts AgentLifecycle ActionReceipts from Omo-Koda2 agents (birth/think/act).
Lightweight store — full chain verification is deferred to Phase 5 when the
on-chain settlement flow is wired up. For now: accept, validate shape, insert.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arp", tags=["arp-receipts"])

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS arp_receipts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id   TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    kind_ext     TEXT,
    agent_id     TEXT NOT NULL,
    action_kind  TEXT,
    target       TEXT,
    outcome      TEXT,
    timestamp    INTEGER,
    previous_hash TEXT,
    raw_json     TEXT NOT NULL,
    accepted_at  TEXT NOT NULL
)
"""

_table_ready = False


async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with get_db() as db:
        await db.execute(_INIT_SQL)
        await db.commit()
    _table_ready = True


@router.post("/receipts", summary="Ingest an ARP AgentLifecycle receipt")
async def ingest_arp_receipt(request: Request, agent: dict = Depends(get_agent)):
    """
    Accept a birth/think/act ActionReceipt from an Omo-Koda2 agent.
    The authenticated agent_id is overwritten with the caller's identity.
    Duplicate receipt_ids are idempotently accepted.
    """
    await _ensure_table()

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail={"error": "Request body must be valid JSON"})

    receipt_id = body.get("receipt_id")
    if not receipt_id:
        raise HTTPException(status_code=422, detail={"error": "receipt_id required"})

    kind = body.get("kind", "AgentLifecycle")
    kind_ext = body.get("kind_ext")
    action = body.get("action", {})
    principal = body.get("principal", {})

    # Overwrite agent_id with authenticated caller
    agent_id = agent["name"]
    body_principal = body.get("principal", {})
    body_principal["agent_id"] = agent_id
    body["principal"] = body_principal

    accepted_at = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO arp_receipts
                     (receipt_id, kind, kind_ext, agent_id, action_kind,
                      target, outcome, timestamp, previous_hash, raw_json, accepted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id, kind, kind_ext, agent_id,
                    action.get("kind"), action.get("target"), action.get("outcome"),
                    body.get("timestamp"), body.get("previous_hash"),
                    json.dumps(body), accepted_at,
                ),
            )
            await db.commit()
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                return {"ok": True, "duplicate": True, "receipt_id": receipt_id}
            logger.exception("arp_receipt insert failed: %s", exc)
            raise HTTPException(status_code=500, detail={"error": str(exc)})

    logger.info("arp_receipt accepted: %s kind_ext=%s agent=%s", receipt_id, kind_ext, agent_id)
    return {"ok": True, "receipt_id": receipt_id, "accepted_at": accepted_at}


@router.get("/receipts", summary="List ARP receipts for authenticated agent")
async def list_arp_receipts(agent: dict = Depends(get_agent), limit: int = 50, offset: int = 0):
    await _ensure_table()
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT receipt_id, kind, kind_ext, action_kind, target, outcome, timestamp, accepted_at "
            "FROM arp_receipts WHERE agent_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (agent["name"], limit, offset),
        )
    return [dict(r) for r in rows]
