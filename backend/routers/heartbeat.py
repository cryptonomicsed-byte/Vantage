"""Sovereign node heartbeat router.

Sovereign nodes (Omarchy devices running the 3-pillar stack) periodically
POST their health status here so Vantage has a live map of the mesh.
No sensitive data crosses this endpoint -- just uptime counters and an
optional OSOVM URL so the hub can proxy simulation requests to the right node.

Endpoints:
  POST /api/nodes/heartbeat — node reports itself alive
  GET  /api/nodes           — list known sovereign nodes (active + historical)
"""
import logging
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

# A node is considered "active" if it reported within this window (seconds).
_ACTIVE_WINDOW_SECONDS = 300  # 5 minutes


# ── Schema init ───────────────────────────────────────────────────────────────

async def _ensure_table() -> None:
    """Create node_heartbeats table if it doesn't exist yet."""
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS node_heartbeats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                node_did     TEXT NOT NULL,
                agent_name   TEXT,
                uptime_secs  INTEGER,
                device_count INTEGER,
                jobs_running INTEGER,
                osovm_url    TEXT,
                reported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Index for fast recent-node lookups
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_node_hb_did_time ON node_heartbeats (node_did, reported_at DESC)"
        )
        await db.commit()


# ── Heartbeat POST ────────────────────────────────────────────────────────────

@router.post(
    "/heartbeat",
    summary="Report a sovereign node as alive",
    description=(
        "Expected body: {node_did, uptime_secs, device_count, jobs_running, osovm_url (optional)}. "
        "Stores a row in node_heartbeats and returns {status, recorded_at}."
    ),
)
async def node_heartbeat(
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Record a heartbeat from a sovereign node."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    node_did: str = (body.get("node_did") or "").strip()
    if not node_did:
        raise HTTPException(status_code=400, detail="'node_did' is required")

    uptime_secs: int | None = body.get("uptime_secs")
    device_count: int | None = body.get("device_count")
    jobs_running: int | None = body.get("jobs_running")
    osovm_url: str = (body.get("osovm_url") or "").strip() or None  # type: ignore[assignment]
    agent_name: str = agent.get("name") or str(agent["id"])

    # Ensure schema exists (fast no-op after first call due to IF NOT EXISTS)
    await _ensure_table()

    recorded_at = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO node_heartbeats
                (node_did, agent_name, uptime_secs, device_count, jobs_running, osovm_url, reported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (node_did, agent_name, uptime_secs, device_count, jobs_running, osovm_url, recorded_at),
        )
        await db.commit()

    logger.debug("heartbeat: node_did=%s agent=%s uptime=%s", node_did, agent_name, uptime_secs)

    return {
        "status": "ok",
        "node_did": node_did,
        "recorded_at": recorded_at,
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
