"""Public REST index over the existing runtime_receipts table.

This router does NOT modify receipts.py — it only provides read access to
the tables that receipts.py creates and populates.  Writes (submission,
key registration) remain in the existing receipts-submission endpoint.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/receipts", tags=["receipts"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get("", summary="List runtime receipts")
async def list_receipts(
    agent: dict = Depends(get_agent),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    work_ref: Optional[str] = Query(None, description="Filter by work_ref"),
    principal_id: Optional[int] = Query(None, description="Filter by principal_id"),
):
    """List accepted runtime receipts. Supports optional filtering by work_ref
    and principal_id."""
    query = "SELECT * FROM runtime_receipts WHERE 1=1"
    count_query = "SELECT COUNT(*) FROM runtime_receipts WHERE 1=1"
    params: list = []
    count_params: list = []

    if work_ref is not None:
        query += " AND work_ref=?"
        count_query += " AND work_ref=?"
        params.append(work_ref)
        count_params.append(work_ref)

    if principal_id is not None:
        query += " AND principal_id=?"
        count_query += " AND principal_id=?"
        params.append(principal_id)
        count_params.append(principal_id)

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(count_query, count_params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    return {"receipts": [_row_to_dict(r) for r in rows], "total": total}


@router.get("/chain/{agent_ref}", summary="Get receipt chain for an agent_ref")
async def receipt_chain(
    agent_ref: str,
    agent: dict = Depends(get_agent),
    limit: int = Query(50, ge=1, le=200),
):
    """Return all receipts for a given agent_ref ordered by timestamp ascending,
    so callers can walk the hash chain from genesis to head."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT receipt_id, action, previous_hash, merkle_root, timestamp,
                      work_ref, artifact_event_id, attestation_event_id, accepted_at,
                      principal_id, agent_ref
                 FROM runtime_receipts
                WHERE agent_ref=?
                ORDER BY timestamp ASC
                LIMIT ?""",
            (agent_ref, limit),
        ) as cur:
            rows = await cur.fetchall()

    chain = [_row_to_dict(r) for r in rows]
    return {
        "agent_ref": agent_ref,
        "chain": chain,
        "length": len(chain),
    }


@router.get("/{receipt_id}", summary="Get a single receipt by receipt_id")
async def get_receipt(receipt_id: str, agent: dict = Depends(get_agent)):
    """Look up one receipt by its receipt_id (BLAKE3 hex)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM runtime_receipts WHERE receipt_id=?", (receipt_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return _row_to_dict(row)
