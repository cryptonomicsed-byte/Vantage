"""Twin-protocol receipt indexer — kind-aware ingest and query.

Ingests CaptureReceipt (31020), SceneReceipt (31030), FirmwareObservationReceipt
(31040), and SimulationReceipt (31050) from sovereign-node and Scarabswarm.

Enforces the F1 quality gate (>= 0.777) on Capture and Scene receipts — the same
gate twin-protocol enforces in Rust. Observation and Simulation receipts are stored
unconditionally (they have their own outcome field).

Wire format: the receipt JSON is stored verbatim in `raw_json`. Indexed columns
(kind, twin_id, agent_id, merkle_root, f1_score, outcome) are extracted from the
envelope for efficient filtering without full JSON parsing on reads.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/twin-receipts", tags=["twin-receipts"])

# ── constants ─────────────────────────────────────────────────────────────────

# Valid twin-protocol receipt kinds (Nostr-adjacent 31000-series).
_VALID_KINDS = frozenset({31020, 31030, 31040, 31050})
_KIND_NAMES  = {31020: "capture", 31030: "scene", 31040: "observation", 31050: "simulation"}

# F1 gate — mirrors twin-protocol's quality::F1_GATE = 0.777.
# Enforced on Capture (31020) and Scene (31030) receipts.
_F1_GATE         = 0.777
_F1_GATED_KINDS  = frozenset({31020, 31030})

_HEX_RE = re.compile(r"^[0-9a-fA-F]{8,}$")


# ── schema ────────────────────────────────────────────────────────────────────


async def _ensure_table() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS twin_receipts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id   TEXT UNIQUE NOT NULL,
                kind         INTEGER NOT NULL,
                kind_name    TEXT NOT NULL,
                twin_id      TEXT,
                agent_id     TEXT,
                merkle_root  TEXT,
                f1_score     REAL,
                outcome      TEXT,
                raw_json     TEXT NOT NULL,
                received_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tr_kind    ON twin_receipts(kind)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tr_twin    ON twin_receipts(twin_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tr_agent   ON twin_receipts(agent_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tr_kind_f1 ON twin_receipts(kind, f1_score)"
        )
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


# ── validation & extraction ───────────────────────────────────────────────────


def _validate_and_extract(body: dict) -> dict:
    """
    Validate a twin-protocol receipt and extract indexed fields.
    Raises HTTPException(422) on any structural or quality violation.
    Returns a dict of extracted fields ready for DB insert.
    """
    kind = body.get("kind")
    if not isinstance(kind, int) or kind not in _VALID_KINDS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"'kind' must be one of {sorted(_VALID_KINDS)}, got {kind!r}. "
                         f"Did you pass the string 'observation' instead of 31040?"
            },
        )

    receipt_id = str(body.get("receipt_id", "")).strip()
    if not receipt_id:
        raise HTTPException(status_code=422, detail={"error": "'receipt_id' is required"})

    # F1 quality gate — only for Capture and Scene receipts.
    f1_score: Optional[float] = None
    raw_f1 = body.get("f1_score")
    if raw_f1 is not None:
        try:
            f1_score = float(raw_f1)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail={"error": "'f1_score' must be a number"})

    if kind in _F1_GATED_KINDS:
        if f1_score is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": f"'f1_score' is required for kind {kind} ({_KIND_NAMES[kind]})"
                },
            )
        if f1_score < _F1_GATE:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": f"F1 quality gate failed: score {f1_score:.4f} < {_F1_GATE} "
                             f"(twin-protocol F1_GATE). Receipt rejected."
                },
            )

    merkle_root = body.get("merkle_root")
    if merkle_root is not None:
        merkle_str = str(merkle_root)
        if not (_HEX_RE.match(merkle_str) or merkle_str.startswith("sha256:")):
            raise HTTPException(
                status_code=422,
                detail={"error": "'merkle_root' must be a hex string or 'sha256:<hex>'"},
            )

    # Extract twin_id — field name differs across receipt types.
    twin_id = (
        body.get("twin_id")
        or body.get("twin_asset_id")
        or None
    )

    # Extract agent_id — from identity chain or top-level.
    identity = body.get("identity", {})
    if isinstance(identity, dict):
        agent_id = identity.get("agent_id") or body.get("agent_id")
    else:
        agent_id = body.get("agent_id")

    # Outcome: snake_case string (validated/partial/falsified/success/failure/etc.)
    outcome = str(body.get("outcome", "")).lower() or None

    return {
        "receipt_id":  receipt_id,
        "kind":        kind,
        "kind_name":   _KIND_NAMES[kind],
        "twin_id":     str(twin_id) if twin_id else None,
        "agent_id":    str(agent_id) if agent_id else None,
        "merkle_root": str(merkle_root) if merkle_root else None,
        "f1_score":    f1_score,
        "outcome":     outcome,
    }


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.post("/ingest", summary="Ingest a twin-protocol receipt")
async def ingest_twin_receipt(request: Request, agent: dict = Depends(get_agent)):
    """
    Accept a CaptureReceipt (31020), SceneReceipt (31030),
    FirmwareObservationReceipt (31040), or SimulationReceipt (31050).

    Enforces the F1 quality gate (>= 0.777) on Capture and Scene receipts.
    Duplicate receipt_ids are silently accepted (idempotent).
    """
    await _ensure_table()

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail={"error": "Request body must be valid JSON"})

    fields = _validate_and_extract(body)
    raw_json = json.dumps(body)
    accepted_at = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO twin_receipts
                     (receipt_id, kind, kind_name, twin_id, agent_id,
                      merkle_root, f1_score, outcome, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    fields["receipt_id"], fields["kind"], fields["kind_name"],
                    fields["twin_id"], fields["agent_id"],
                    fields["merkle_root"], fields["f1_score"], fields["outcome"],
                    raw_json,
                ),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            logger.debug("twin_receipt_index: duplicate receipt_id %s ignored", fields["receipt_id"])

    return {
        "status":     "accepted",
        "receipt_id": fields["receipt_id"],
        "kind":       fields["kind"],
        "kind_name":  fields["kind_name"],
        "accepted_at": accepted_at,
    }


@router.get("", summary="List twin-protocol receipts")
async def list_twin_receipts(
    agent: dict = Depends(get_agent),
    limit:   int            = Query(50, ge=1, le=200),
    offset:  int            = Query(0, ge=0),
    kind:    Optional[int]  = Query(None, description="Filter by receipt kind (31020/31030/31040/31050)"),
    twin_id: Optional[str]  = Query(None, description="Filter by twin_id"),
    agent_id: Optional[str] = Query(None, description="Filter by agent_id"),
    f1_min:  Optional[float]= Query(None, description="Minimum F1 score (0.0–1.0)"),
):
    """List receipts ordered by received_at descending with optional filters."""
    if kind is not None and kind not in _VALID_KINDS:
        raise HTTPException(
            status_code=422,
            detail={"error": f"kind must be one of {sorted(_VALID_KINDS)}"},
        )

    clauses: list[str] = []
    params:  list      = []

    if kind is not None:
        clauses.append("kind=?");   params.append(kind)
    if twin_id:
        clauses.append("twin_id=?"); params.append(twin_id)
    if agent_id:
        clauses.append("agent_id=?"); params.append(agent_id)
    if f1_min is not None:
        clauses.append("f1_score >= ?"); params.append(f1_min)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT COUNT(*) FROM twin_receipts {where}", params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        async with db.execute(
            f"""SELECT receipt_id, kind, kind_name, twin_id, agent_id,
                       merkle_root, f1_score, outcome, received_at
                  FROM twin_receipts {where}
                 ORDER BY received_at DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ) as cur:
            rows = await cur.fetchall()

    return {"receipts": [dict(r) for r in rows], "total": total}


@router.get("/{receipt_id}", summary="Get a single twin-protocol receipt")
async def get_twin_receipt(receipt_id: str, agent: dict = Depends(get_agent)):
    """Return the full stored JSON for a receipt (raw_json field included)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM twin_receipts WHERE receipt_id=?", (receipt_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Twin receipt not found")

    d = dict(row)
    try:
        d["receipt"] = json.loads(d.get("raw_json", "{}"))
    except (ValueError, TypeError):
        pass
    return d
