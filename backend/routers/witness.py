"""BlockMesh witness protocol endpoints — Phase B.

Witnesses are tier-weighted agents assigned to validate submitted work.
They cast approve/reject votes; quorum triggers finalization and reputation
changes via witness_store._finalize_round().
"""
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.db import get_db
from backend.deps import get_agent
from backend.witness_store import cast_vote, open_witness_round

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/witness", tags=["witness"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class VoteRequest(BaseModel):
    vote: str          # 'approve' | 'reject'
    comment: str = ""


class OpenRoundRequest(BaseModel):
    subject_type: str
    subject_id: int
    artifact_url: str = ""
    description: str = ""
    pool_size: int | None = None
    # P0-9: proof binding — link witness round to simulation and consensus receipts
    sim_receipt_id:      str | None = None
    consensus_output_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/queue")
async def witness_queue(agent: dict = Depends(get_agent)):
    """Return pending witness assignments for the calling agent."""
    agent_id = agent["id"]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT
                wa.id           AS assignment_id,
                wa.round_id,
                wr.subject_type,
                wr.subject_id,
                wr.artifact_url,
                wr.description,
                wr.opened_at,
                wr.quorum,
                wr.pool_size
            FROM witness_assignments wa
            JOIN witness_rounds wr ON wr.id = wa.round_id
            WHERE wa.witness_agent_id = ?
              AND wa.vote IS NULL
              AND wr.status = 'open'
            ORDER BY wr.opened_at DESC
        """, (agent_id,))
        rows = [dict(r) for r in await cur.fetchall()]
    return {"items": rows}


@router.get("/rounds")
async def list_rounds(
    status: str = Query("open"),
    subject_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Browse witness rounds, filterable by status and subject_type."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        where_clauses = ["wr.status = ?"]
        params: list = [status]

        if subject_type:
            where_clauses.append("wr.subject_type = ?")
            params.append(subject_type)

        where_sql = " AND ".join(where_clauses)

        # Total count
        count_cur = await db.execute(
            f"SELECT COUNT(*) AS n FROM witness_rounds wr WHERE {where_sql}",
            params,
        )
        total = (await count_cur.fetchone())["n"]

        # Page
        cur = await db.execute(
            f"""
            SELECT
                wr.id, wr.subject_type, wr.subject_id,
                wr.submitter_agent_id, wr.pool_size, wr.quorum,
                wr.status, wr.opened_at, wr.closed_at,
                wr.artifact_url, wr.description
            FROM witness_rounds wr
            WHERE {where_sql}
            ORDER BY wr.opened_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        rows = [dict(r) for r in await cur.fetchall()]

    return {"items": rows, "total": total}


@router.get("/rounds/{round_id}")
async def get_round(round_id: int):
    """Get round details with vote tally. Visible to anyone."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT * FROM witness_rounds WHERE id=?", (round_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Round not found")
        round_data = dict(row)

        # Tally
        cur = await db.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN vote='approve' THEN 1 ELSE 0 END), 0) AS approvals,
                COALESCE(SUM(CASE WHEN vote='reject'  THEN 1 ELSE 0 END), 0) AS rejections,
                COALESCE(SUM(CASE WHEN vote IS NULL   THEN 1 ELSE 0 END), 0) AS pending
            FROM witness_assignments
            WHERE round_id=?
        """, (round_id,))
        tally = dict(await cur.fetchone())

        # Assignments list (without exposing pending votes as content)
        cur = await db.execute("""
            SELECT id, witness_agent_id, vote, comment, voted_at
            FROM witness_assignments
            WHERE round_id=?
            ORDER BY id
        """, (round_id,))
        assignments = [dict(r) for r in await cur.fetchall()]

    return {**round_data, "tally": tally, "assignments": assignments}


@router.post("/rounds/{round_id}/vote")
async def vote(
    round_id: int,
    body: VoteRequest,
    agent: dict = Depends(get_agent),
):
    """Cast a witness vote on a round."""
    try:
        result = await cast_vote(round_id, agent["id"], body.vote, body.comment)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("witness vote error: round=%d agent=%d: %s", round_id, agent["id"], exc)
        raise HTTPException(status_code=500, detail="Vote failed")
    return result


@router.post("/rounds")
async def create_round(
    body: OpenRoundRequest,
    agent: dict = Depends(get_agent),
):
    """Open a new witness round for submitted work (called by the submitter)."""
    try:
        result = await open_witness_round(
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            submitter_agent_id=agent["id"],
            artifact_url=body.artifact_url,
            description=body.description,
            pool_size=body.pool_size,
            sim_receipt_id=body.sim_receipt_id,           # P0-9
            consensus_output_id=body.consensus_output_id, # P0-9
        )
    except Exception as exc:
        logger.error("open_witness_round failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to open witness round")
    return result
