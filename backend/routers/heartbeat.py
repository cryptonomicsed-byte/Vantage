"""Sovereign node heartbeat router.

Sovereign nodes (Omarchy devices running the 3-pillar stack) periodically
POST their health status here so Vantage has a live map of the mesh.
Chain-hash fields (boot_id, sequence, previous_heartbeat_hash, chain_hash)
come from lifecycle/heartbeat.rs in Omo-Koda2, proving continuity of life.

Endpoints:
  POST /api/nodes/heartbeat — node reports itself alive
  GET  /api/nodes           — list known sovereign nodes (active + historical)
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

_ACTIVE_WINDOW_SECONDS = 300  # 5 minutes

_VALID_STATES = {"alive", "thinking", "working", "resting", "offline"}


def _compute_beat_hash(fields: dict[str, Any]) -> str:
    """Reproduce the canonical SHA-256 used by lifecycle/heartbeat.rs AgentHeartbeat::hash()."""
    canonical = {
        "agent_id":                fields.get("node_did", ""),
        "boot_id":                 fields.get("boot_id", ""),
        "sequence":                fields.get("sequence", 0),
        "timestamp":               fields.get("beat_timestamp", 0),
        "state":                   fields.get("state", "alive"),
        "tier":                    fields.get("tier", ""),
        "active_daemons":          fields.get("active_daemons", []),
        "current_work":            fields.get("current_work"),
        "previous_heartbeat_hash": fields.get("previous_heartbeat_hash"),
    }
    raw = json.dumps(canonical, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Schema init ───────────────────────────────────────────────────────────────

async def _ensure_table() -> None:
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS node_heartbeats (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                node_did                TEXT NOT NULL,
                agent_name              TEXT,
                uptime_secs             INTEGER,
                device_count            INTEGER,
                jobs_running            INTEGER,
                osovm_url               TEXT,
                boot_id                 TEXT,
                sequence                INTEGER,
                beat_timestamp          INTEGER,
                state                   TEXT DEFAULT 'alive',
                tier                    TEXT,
                active_daemons          TEXT,
                current_work            TEXT,
                previous_heartbeat_hash TEXT,
                chain_hash              TEXT,
                chain_valid             INTEGER DEFAULT 1,
                reported_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_node_hb_did_time ON node_heartbeats (node_did, reported_at DESC)"
        )
        # Migrate existing tables (idempotent)
        for col, defn in [
            ("boot_id", "TEXT"),
            ("sequence", "INTEGER"),
            ("beat_timestamp", "INTEGER"),
            ("state", "TEXT DEFAULT 'alive'"),
            ("tier", "TEXT"),
            ("active_daemons", "TEXT"),
            ("current_work", "TEXT"),
            ("previous_heartbeat_hash", "TEXT"),
            ("chain_hash", "TEXT"),
            ("chain_valid", "INTEGER DEFAULT 1"),
        ]:
            try:
                await db.execute(f"ALTER TABLE node_heartbeats ADD COLUMN {col} {defn}")
            except Exception:
                pass  # column already exists
        await db.commit()


# ── Heartbeat POST ────────────────────────────────────────────────────────────

@router.post(
    "/heartbeat",
    summary="Report a sovereign node as alive",
    description=(
        "Body: {node_did, uptime_secs, device_count, jobs_running, osovm_url, "
        "boot_id, sequence, beat_timestamp, state, tier, active_daemons, "
        "current_work, previous_heartbeat_hash, chain_hash}. "
        "Chain fields come from Omo-Koda2 lifecycle/heartbeat.rs. "
        "Returns {status, recorded_at, chain_hash, chain_valid}."
    ),
)
async def node_heartbeat(
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Record a heartbeat from a sovereign node, verifying the SHA-256 chain."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    node_did: str = (body.get("node_did") or "").strip()
    if not node_did:
        raise HTTPException(status_code=400, detail="'node_did' is required")

    uptime_secs: int | None      = body.get("uptime_secs")
    device_count: int | None     = body.get("device_count")
    jobs_running: int | None     = body.get("jobs_running")
    osovm_url: str | None        = (body.get("osovm_url") or "").strip() or None
    agent_name: str              = agent.get("name") or str(agent["id"])

    # Chain fields from Omo-Koda2 lifecycle/heartbeat.rs
    boot_id: str | None                  = body.get("boot_id")
    sequence: int | None                 = body.get("sequence")
    beat_timestamp: int | None           = body.get("beat_timestamp")
    state: str                           = (body.get("state") or "alive").lower()
    if state not in _VALID_STATES:
        state = "alive"
    tier: str | None                     = body.get("tier")
    active_daemons: list                 = body.get("active_daemons") or []
    current_work: str | None             = body.get("current_work")
    previous_heartbeat_hash: str | None  = body.get("previous_heartbeat_hash")
    client_chain_hash: str | None        = body.get("chain_hash")

    await _ensure_table()

    # Verify chain: recompute expected hash from canonical fields and compare.
    chain_valid = True
    if client_chain_hash:
        expected_hash = _compute_beat_hash({
            "node_did":                node_did,
            "boot_id":                 boot_id or "",
            "sequence":                sequence or 0,
            "beat_timestamp":          beat_timestamp or 0,
            "state":                   state,
            "tier":                    tier or "",
            "active_daemons":          active_daemons,
            "current_work":            current_work,
            "previous_heartbeat_hash": previous_heartbeat_hash,
        })
        chain_valid = (expected_hash == client_chain_hash)
        if not chain_valid:
            logger.warning(
                "heartbeat chain mismatch: node=%s seq=%s got=%s expected=%s",
                node_did, sequence, client_chain_hash[:16], expected_hash[:16],
            )

    recorded_at = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO node_heartbeats (
                node_did, agent_name, uptime_secs, device_count, jobs_running, osovm_url,
                boot_id, sequence, beat_timestamp, state, tier,
                active_daemons, current_work,
                previous_heartbeat_hash, chain_hash, chain_valid,
                reported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                node_did, agent_name, uptime_secs, device_count, jobs_running, osovm_url,
                boot_id, sequence, beat_timestamp, state, tier,
                json.dumps(active_daemons), current_work,
                previous_heartbeat_hash, client_chain_hash, int(chain_valid),
                recorded_at,
            ),
        )
        await db.commit()

    logger.debug(
        "heartbeat: node_did=%s seq=%s state=%s chain_valid=%s",
        node_did, sequence, state, chain_valid,
    )

    return {
        "status": "ok",
        "node_did": node_did,
        "recorded_at": recorded_at,
        "chain_hash": client_chain_hash,
        "chain_valid": chain_valid,
        "sequence": sequence,
    }


# ── List nodes GET ────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="List known sovereign nodes",
    description=(
        "Returns nodes seen in the last 5 minutes (active=true) and all "
        "historical nodes with their last-seen timestamp."
    ),
)
async def list_nodes(agent: dict = Depends(get_agent)):
    """Return all sovereign nodes, marking which are currently active."""
    await _ensure_table()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # One row per unique node_did: most-recent heartbeat + active flag
        async with db.execute(
            f"""
            SELECT
                node_did,
                MAX(reported_at)  AS last_seen,
                agent_name,
                uptime_secs,
                device_count,
                jobs_running,
                osovm_url,
                boot_id,
                MAX(sequence)     AS sequence,
                state,
                tier,
                active_daemons,
                current_work,
                chain_hash,
                chain_valid,
                CASE
                    WHEN MAX(reported_at) >= datetime('now', '-{_ACTIVE_WINDOW_SECONDS} seconds')
                    THEN 1 ELSE 0
                END AS active
            FROM node_heartbeats
            GROUP BY node_did
            ORDER BY last_seen DESC
            """
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    active_count = sum(1 for r in rows if r["active"])

    return {
        "nodes": rows,
        "total": len(rows),
        "active": active_count,
        "active_window_seconds": _ACTIVE_WINDOW_SECONDS,
    }
