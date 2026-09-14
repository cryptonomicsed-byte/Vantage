"""UCX job visibility endpoints for Vantage.

Routes:
  POST /api/ucx/jobs              — broker submits a new job record
  POST /api/ucx/jobs/{id}/status  — broker updates job status / attaches receipt
  GET  /api/ucx/jobs              — agent lists their compute jobs
  GET  /api/ucx/jobs/{job_id}     — agent retrieves a single job
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query

from ..db import get_db
from ..deps import get_agent
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
async def update_status(job_id: str, request: Request, agent: dict = Depends(get_agent)):
    await _ensure()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, {"error": "invalid JSON"})
    status = body.get("status", "Pending")
    receipt = body.get("receipt")
    price_cents = body.get("price_cents")
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
