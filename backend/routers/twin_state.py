"""twin_state.py — TwinStateVector endpoint for the sovereign agent runtime.

Stores the TwinStateVector emitted by organism-core/bridge/twin-state.ts
(which mirrors If-Script/src/seven_bridge.rs TwinStateVector).

Schema (own table, never touches the agents table):
  - agent_id:          TEXT — sovereign agent identifier
  - identity_odu:      INTEGER — Odù governing identity/constitution domain
  - memory_odu:        INTEGER — Odù governing memory/residue domain
  - field_odu:         INTEGER — Odù governing interaction/consent domain
  - simulation_odu:    INTEGER — Odù governing simulation/vision domain
  - dominant_function: INTEGER — SevenFunction index (0-6) most represented
  - composed_signature:TEXT — hex fingerprint of the 4 Odù bytes
  - computed_at:       INTEGER — unix timestamp when the TS side built the vector
  - received_at:       TIMESTAMP — server wall-clock for the ingest

Only one row per agent_id is kept (UPSERT); the previous vector is overwritten.
The endpoint is authenticated via the standard Vantage agent token (Depends(get_agent))
and enforces that the caller's agent_id matches the path parameter.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["twin-state"])

# ── schema ────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_twin_state (
    agent_id            TEXT PRIMARY KEY,
    identity_odu        INTEGER NOT NULL DEFAULT 0,
    memory_odu          INTEGER NOT NULL DEFAULT 0,
    field_odu           INTEGER NOT NULL DEFAULT 0,
    simulation_odu      INTEGER NOT NULL DEFAULT 0,
    dominant_function   INTEGER NOT NULL DEFAULT 0,
    composed_signature  TEXT    NOT NULL DEFAULT '',
    computed_at         INTEGER NOT NULL DEFAULT 0,
    received_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


async def _ensure_table() -> None:
    async with get_db() as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ats_dominant "
            "ON agent_twin_state(dominant_function)"
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


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_fields(body: dict) -> dict:
    """Validate and extract TwinStateVector fields from the request body."""

    def _int_field(key: str, lo: int = 0, hi: int = 255) -> int:
        val = body.get(key)
        if val is None:
            raise HTTPException(status_code=422, detail={"error": f"'{key}' is required"})
        try:
            v = int(val)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail={"error": f"'{key}' must be an integer"})
        if not (lo <= v <= hi):
            raise HTTPException(
                status_code=422,
                detail={"error": f"'{key}' must be in [{lo}, {hi}], got {v}"},
            )
        return v

    return {
        "identity_odu":       _int_field("identity_odu"),
        "memory_odu":         _int_field("memory_odu"),
        "field_odu":          _int_field("field_odu"),
        "simulation_odu":     _int_field("simulation_odu"),
        "dominant_function":  _int_field("dominant_function", lo=0, hi=6),
        "composed_signature": str(body.get("composed_signature", "")),
        "computed_at":        int(body.get("computed_at", 0)),
    }


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.put(
    "/api/agents/{agent_id}/twin-state",
    summary="Upsert agent TwinStateVector",
)
async def update_twin_state(
    agent_id: str,
    request: Request,
    agent: dict = Depends(get_agent),
) -> dict:
    """Store (or replace) the TwinStateVector for an agent.

    The authenticated caller must own the agent_id in the path.
    The body mirrors TwinStateVector from organism-core/bridge/twin-state.ts.
    """
    await _ensure_table()

    # Enforce ownership: the agent in the auth token must match the path.
    caller_name = agent.get("name", "")
    if caller_name != agent_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": f"Authenticated agent '{caller_name}' cannot write "
                         f"twin-state for '{agent_id}'"
            },
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail={"error": "Request body must be valid JSON"})

    fields = _extract_fields(body)
    received_at = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_twin_state
                   (agent_id, identity_odu, memory_odu, field_odu,
                    simulation_odu, dominant_function, composed_signature,
                    computed_at, received_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                   identity_odu       = excluded.identity_odu,
                   memory_odu         = excluded.memory_odu,
                   field_odu          = excluded.field_odu,
                   simulation_odu     = excluded.simulation_odu,
                   dominant_function  = excluded.dominant_function,
                   composed_signature = excluded.composed_signature,
                   computed_at        = excluded.computed_at,
                   received_at        = excluded.received_at
            """,
            (
                agent_id,
                fields["identity_odu"],
                fields["memory_odu"],
                fields["field_odu"],
                fields["simulation_odu"],
                fields["dominant_function"],
                fields["composed_signature"],
                fields["computed_at"],
                received_at,
            ),
        )
        await db.commit()

    logger.debug(
        "twin_state: upserted agent=%s dominant=%d sig=%s",
        agent_id, fields["dominant_function"], fields["composed_signature"],
    )

    return {
        "status":      "ok",
        "agent_id":    agent_id,
        "received_at": received_at,
    }


@router.get(
    "/api/agents/{agent_id}/twin-state",
    summary="Retrieve agent TwinStateVector",
)
async def get_twin_state(
    agent_id: str,
    agent: dict = Depends(get_agent),
) -> dict:
    """Return the stored TwinStateVector for an agent. 404 if not yet set."""
    await _ensure_table()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_twin_state WHERE agent_id=?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail={"error": f"No twin-state for agent '{agent_id}'"})

    return dict(row)
