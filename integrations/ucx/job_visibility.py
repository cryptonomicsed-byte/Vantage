"""
UCX job visibility layer.

Vantage stores lightweight job records so agents can see their compute history
in the dashboard.  The UCX broker (Rust) POSTs job events here; agents read
via GET /api/ucx/jobs.

  UCX Broker → POST /api/ucx/jobs             (job created)
  UCX Broker → POST /api/ucx/jobs/{id}/status (job status update)
  Agent      → GET  /api/ucx/jobs             (list this agent's jobs)
  Agent      → GET  /api/ucx/jobs/{id}        (single job detail)

Design: mirrors receipt_index.py pattern — thin read layer, writes come from
the Rust broker only.  Vantage does NOT control job dispatch.
"""
from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ucx_jobs (
    job_id       TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    provider_id  TEXT,
    workload     TEXT,
    status       TEXT NOT NULL DEFAULT 'Pending',
    price_cents  INTEGER,
    submitted_at REAL NOT NULL,
    updated_at   REAL NOT NULL,
    raw_job      TEXT,         -- JSON snapshot of the Job struct
    raw_receipt  TEXT          -- JSON snapshot of the ComputeReceipt once done
);
"""

JOB_COLUMNS = (
    "job_id", "agent_id", "provider_id", "workload", "status",
    "price_cents", "submitted_at", "updated_at",
)


async def ensure_table(db: aiosqlite.Connection) -> None:
    await db.execute(CREATE_TABLE)
    await db.commit()


# ── writes (broker-side) ──────────────────────────────────────────────────────

async def record_job(db: aiosqlite.Connection, agent_id: str, job: dict[str, Any]) -> dict:
    """Insert a new job record when the broker accepts a submission."""
    job_id = job.get("id") or job.get("job_id")
    if not job_id:
        return {"ok": False, "error": "job.id required"}

    now = time.time()
    await db.execute(
        """INSERT INTO ucx_jobs
             (job_id, agent_id, provider_id, workload, status,
              price_cents, submitted_at, updated_at, raw_job)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET
              status=excluded.status, updated_at=excluded.updated_at""",
        (
            job_id, agent_id,
            job.get("provider_id"),
            str(job.get("workload", "")),
            "Pending",
            None,
            now, now,
            json.dumps(job),
        ),
    )
    await db.commit()
    return {"ok": True, "job_id": job_id}


async def update_job_status(
    db: aiosqlite.Connection,
    job_id: str,
    status: str,
    receipt: dict[str, Any] | None = None,
    price_cents: int | None = None,
) -> dict:
    """Update job status and optionally attach a receipt."""
    receipt_json = json.dumps(receipt) if receipt else None
    now = time.time()
    await db.execute(
        """UPDATE ucx_jobs
           SET status=?, updated_at=?,
               raw_receipt=COALESCE(?, raw_receipt),
               price_cents=COALESCE(?, price_cents)
           WHERE job_id=?""",
        (status, now, receipt_json, price_cents, job_id),
    )
    await db.commit()
    return {"ok": True, "job_id": job_id, "status": status}


# ── reads (agent-side) ────────────────────────────────────────────────────────

def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(zip(JOB_COLUMNS, row))


async def list_jobs(
    db: aiosqlite.Connection,
    agent_id: str,
    limit: int = 50,
    offset: int = 0,
    status_filter: str | None = None,
) -> list[dict]:
    query = "SELECT " + ", ".join(JOB_COLUMNS) + " FROM ucx_jobs WHERE agent_id=?"
    params: list = [agent_id]
    if status_filter:
        query += " AND status=?"
        params.append(status_filter)
    query += " ORDER BY submitted_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_job(db: aiosqlite.Connection, job_id: str) -> dict | None:
    async with db.execute(
        "SELECT " + ", ".join(JOB_COLUMNS) + ", raw_job, raw_receipt "
        "FROM ucx_jobs WHERE job_id=?",
        (job_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    keys = list(JOB_COLUMNS) + ["raw_job", "raw_receipt"]
    d = dict(zip(keys, row))
    if d.get("raw_job"):
        d["job"] = json.loads(d.pop("raw_job"))
    if d.get("raw_receipt"):
        d["receipt"] = json.loads(d.pop("raw_receipt"))
    return d
