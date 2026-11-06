"""UCX job visibility endpoints for Vantage.

Routes:
  POST /api/ucx/jobs              — broker submits a new job record
  POST /api/ucx/jobs/{id}/status  — broker updates job status / attaches receipt
  GET  /api/ucx/jobs              — agent lists their compute jobs
  GET  /api/ucx/jobs/{job_id}     — agent retrieves a single job
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Query
from typing import Optional

from ..db import get_db
from ..deps import get_agent
from ..config import settings
from ..ucx_job_visibility import (
    ensure_table, record_job, update_job_status, list_jobs, get_job,
)

router = APIRouter(prefix="/api/ucx", tags=["ucx-jobs"])

_table_ready = False


async def _ensure() -> None:
    global _table_ready
    if _table_ready:
        return
    async with get_db() as db:
        await ensure_table(db)
    _table_ready = True


@router.post("/jobs", summary="Record a new UCX compute job")
async def create_job(request: Request, agent: dict = Depends(get_agent)):
    await _ensure()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, {"error": "invalid JSON"})
    async with get_db() as db:
        result = await record_job(db, agent["name"], body)
    return result


@router.post("/jobs/{job_id}/status", summary="Update UCX job status")
async def update_status(
    job_id: str,
    request: Request,
    agent: dict = Depends(get_agent),
    x_vantage_tool_key: Optional[str] = Header(None),
):
    """Broker posts job status transitions.

    Completion attestation (status='Complete' with a receipt) requires the
    X-Vantage-Tool-Key header containing the TOOL_TRADING secret so that
    only the UCX broker — not the agent that submitted the job — can confirm
    that compute work was delivered and settle the provider obligation.
    """
    await _ensure()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, {"error": "invalid JSON"})
    status = body.get("status", "Pending")
    receipt = body.get("receipt")
    price_cents = body.get("price_cents")

    # Provider payment obligations must be attested by the broker, not by the
    # agent that placed the job.  Any agent can transition to Pending/Running,
    # but only the broker (authenticated with TOOL_TRADING key) may confirm
    # Complete + attach a receipt.
    if status == "Complete" and receipt is not None:
        expected_key = getattr(settings, "TOOL_TRADING", None)
        if not expected_key or x_vantage_tool_key != expected_key:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Job completion with a receipt requires X-Vantage-Tool-Key "
                    "— only the UCX broker may attest provider obligations."
                ),
            )

    async with get_db() as db:
        result = await update_job_status(db, job_id, status, receipt, price_cents)
    return result


@router.get("/jobs", summary="List this agent's UCX compute jobs")
async def list_agent_jobs(
    agent: dict = Depends(get_agent),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query(None),
):
    await _ensure()
    async with get_db() as db:
        return await list_jobs(db, agent["name"], limit, offset, status)


@router.get("/jobs/{job_id}", summary="Get a single UCX compute job")
async def get_single_job(job_id: str, agent: dict = Depends(get_agent)):
    await _ensure()
    async with get_db() as db:
        job = await get_job(db, job_id)
    if not job:
        raise HTTPException(404, {"error": "job not found"})
    if job["agent_id"] != agent["name"]:
        raise HTTPException(403, {"error": "forbidden"})
    return job
