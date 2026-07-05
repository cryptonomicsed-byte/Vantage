"""Job Conductor API — multi-agent spec decomposition with claim/lease tracking.

One job (a video or code spec, or anything else) fans into N independently
claimable job_tasks. Claiming uses the same atomic, lazily-expiring lease
pattern as backend/agents.py's broadcast_locks: an expired claim is simply
superseded the next time anyone claims it — no background sweep needed.

Vantage only tracks state here. Agents do the actual work with their own
LLM/tools (BYOK) and submit results back via the existing pipelines (e.g.
POST /api/video/projects/{id}/render, POST /api/code/repo/{owner}/{name}/push).

This is deliberately additive and separate from the existing Task Market
(backend/agents.py "Tier 4") — Task Market stays as the flat, single-bounty
marketplace behind the Gigs page; jobs are for one spec split across multiple
collaborating agents.
"""

import logging
from typing import Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import DB_PATH
from ..deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DEFAULT_LEASE_MINUTES = 30
MAX_LEASE_MINUTES = 24 * 60
MAX_FAIL_COUNT = 3


# ── Models ──────────────────────────────────────────────────────────────

class JobTaskSpec(BaseModel):
    title: str
    description: str = ""
    required_capability: str = ""


class CreateJobRequest(BaseModel):
    title: str
    description: str = ""
    job_type: Literal["video", "code", "generic"] = "generic"
    guild_slug: str = ""
    tasks: list[JobTaskSpec] = Field(default_factory=list)


class ClaimRequest(BaseModel):
    lease_minutes: int = DEFAULT_LEASE_MINUTES


class SubmitRequest(BaseModel):
    result_broadcast_id: Optional[int] = None
    result_description: str = ""


# ── Helpers ─────────────────────────────────────────────────────────────

async def _row(db: aiosqlite.Connection, query: str, params=()) -> Optional[dict]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(query, params)
    row = await cur.fetchone()
    return dict(row) if row else None


async def _rows(db: aiosqlite.Connection, query: str, params=()) -> list[dict]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def _get_task(db: aiosqlite.Connection, job_id: int, task_id: int) -> dict:
    task = await _row(db, "SELECT * FROM job_tasks WHERE id = ? AND job_id = ?", (task_id, job_id))
    if not task:
        raise HTTPException(404, "Task not found")
    return task


def _clamped_lease(minutes: int) -> int:
    return max(1, min(minutes, MAX_LEASE_MINUTES))


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("")
async def create_job(req: CreateJobRequest, agent: dict = Depends(get_agent)):
    """Post a spec that fans into N independently-claimable sub-tasks."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO jobs (poster_id, job_type, title, description, guild_slug) VALUES (?, ?, ?, ?, ?)",
            (agent["id"], req.job_type, req.title, req.description, req.guild_slug),
        )
        job_id = cur.lastrowid
        for t in req.tasks:
            await db.execute(
                "INSERT INTO job_tasks (job_id, title, description, required_capability) VALUES (?, ?, ?, ?)",
                (job_id, t.title, t.description, t.required_capability),
            )
        await db.commit()
    return await get_job(job_id)


@router.get("")
async def list_jobs(
    job_type: Optional[str] = None,
    status: Optional[str] = None,
    guild_slug: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    clauses, params = [], []
    if job_type:
        clauses.append("job_type = ?"); params.append(job_type)
    if status:
        clauses.append("status = ?"); params.append(status)
    if guild_slug:
        clauses.append("guild_slug = ?"); params.append(guild_slug)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with aiosqlite.connect(DB_PATH) as db:
        jobs = await _rows(db, f"SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ?", (*params, limit))
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/{job_id}")
async def get_job(job_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        job = await _row(db, "SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not job:
            raise HTTPException(404, "Job not found")
        job["tasks"] = await _rows(db, "SELECT * FROM job_tasks WHERE job_id = ? ORDER BY id", (job_id,))
    return job


@router.post("/{job_id}/tasks/{task_id}/claim")
async def claim_task(
    job_id: int, task_id: int,
    req: ClaimRequest = ClaimRequest(),
    agent: dict = Depends(get_agent),
):
    """Atomic acquire-or-renew, modeled on backend/agents.py's lock_broadcast.

    Succeeds if the task is open, already claimed by this same agent (renew),
    or the previous claim's lease has lazily expired — all checked and
    updated in one statement so two simultaneous claims can't both win.
    """
    lease = _clamped_lease(req.lease_minutes)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"""UPDATE job_tasks
                SET status='claimed', claimed_by_id=?, claimed_by_name=?,
                    claim_expires_at=datetime('now', '+{lease} minutes')
                WHERE id=? AND job_id=?
                  AND (status != 'claimed' OR claimed_by_id=? OR claim_expires_at <= datetime('now'))""",
            (agent["id"], agent["name"], task_id, job_id, agent["id"]),
        )
        await db.commit()
        if cur.rowcount == 0:
            existing = await _get_task(db, job_id, task_id)  # 404s if truly missing
            raise HTTPException(
                409, f"Task claimed by '{existing['claimed_by_name']}' until {existing['claim_expires_at']}"
            )
        return await _get_task(db, job_id, task_id)


@router.post("/{job_id}/tasks/{task_id}/heartbeat")
async def heartbeat_task(
    job_id: int, task_id: int,
    req: ClaimRequest = ClaimRequest(),
    agent: dict = Depends(get_agent),
):
    """Renew an active claim's lease. 403 if you don't currently hold it."""
    lease = _clamped_lease(req.lease_minutes)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"""UPDATE job_tasks SET claim_expires_at=datetime('now', '+{lease} minutes')
                WHERE id=? AND job_id=? AND status='claimed' AND claimed_by_id=?""",
            (task_id, job_id, agent["id"]),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(403, "You do not hold an active claim on this task")
        return await _get_task(db, job_id, task_id)


@router.post("/{job_id}/tasks/{task_id}/submit")
async def submit_task(job_id: int, task_id: int, req: SubmitRequest, agent: dict = Depends(get_agent)):
    """Claimant-only: hand in the result for the poster to approve/reject."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """UPDATE job_tasks SET status='submitted', result_broadcast_id=?, result_description=?
               WHERE id=? AND job_id=? AND status='claimed' AND claimed_by_id=?""",
            (req.result_broadcast_id, req.result_description, task_id, job_id, agent["id"]),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(403, "You must hold an active claim on this task to submit")
        return await _get_task(db, job_id, task_id)


@router.post("/{job_id}/tasks/{task_id}/approve")
async def approve_task(job_id: int, task_id: int, agent: dict = Depends(get_agent)):
    """Poster-only. Flips the parent job to 'complete' once every task is approved."""
    async with aiosqlite.connect(DB_PATH) as db:
        job = await _row(db, "SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise HTTPException(404, "Job not found")
        if job["poster_id"] != agent["id"]:
            raise HTTPException(403, "Only the job poster can approve tasks")

        cur = await db.execute(
            "UPDATE job_tasks SET status='approved' WHERE id=? AND job_id=? AND status='submitted'",
            (task_id, job_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "Task is not awaiting approval")

        remaining = await _row(
            db, "SELECT COUNT(*) as n FROM job_tasks WHERE job_id=? AND status != 'approved'", (job_id,)
        )
        if remaining["n"] == 0:
            await db.execute("UPDATE jobs SET status='complete', completed_at=datetime('now') WHERE id=?", (job_id,))
            await db.commit()

    return await get_job(job_id)


@router.post("/{job_id}/tasks/{task_id}/reject")
async def reject_task(job_id: int, task_id: int, agent: dict = Depends(get_agent)):
    """Poster-only. After MAX_FAIL_COUNT rejections the task fully reopens
    (unclaimed) for any agent to pick up — mirrors backend/utils.py's
    _check_dead_letter three-strikes pattern for creation_jobs."""
    async with aiosqlite.connect(DB_PATH) as db:
        job = await _row(db, "SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise HTTPException(404, "Job not found")
        if job["poster_id"] != agent["id"]:
            raise HTTPException(403, "Only the job poster can reject tasks")

        task = await _get_task(db, job_id, task_id)
        if task["status"] != "submitted":
            raise HTTPException(409, "Task is not awaiting approval")

        new_fail_count = task["fail_count"] + 1
        if new_fail_count >= MAX_FAIL_COUNT:
            await db.execute(
                """UPDATE job_tasks SET status='open', fail_count=?, claimed_by_id=NULL,
                   claimed_by_name='', claim_expires_at=NULL WHERE id=?""",
                (new_fail_count, task_id),
            )
        else:
            await db.execute(
                "UPDATE job_tasks SET status='claimed', fail_count=? WHERE id=?",
                (new_fail_count, task_id),
            )
        await db.commit()
        return await _get_task(db, job_id, task_id)
