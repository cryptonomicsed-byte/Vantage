"""
Gaussian Splat Pipeline — GPU.ai compute backend.

Vantage agents submit splat training jobs (from Witness-captured scene data)
to GPU.ai (A100 40/80 GB).  The pipeline:

  1. Agent POSTs a splat job with scene_hash / colmap_manifest_url
  2. Vantage provisions a GPU.ai instance (A100 preferred)
  3. Job runs splat training (gsplat / nerfstudio)
  4. On completion, a twin_receipt (kind 31030 SceneReceipt) is created via
     the twin_receipt_index router
  5. ARP receipt is submitted as provenance chain

Routes:
  POST /api/splat/jobs            — submit a new splat training job
  GET  /api/splat/jobs            — list jobs for this agent
  GET  /api/splat/jobs/{job_id}   — job status + GPU.ai operation
  POST /api/splat/jobs/{job_id}/complete  — mark completed + create twin receipt
  DELETE /api/splat/jobs/{job_id} — cancel / terminate GPU instance
  GET  /api/splat/pricing         — live GPU.ai pricing proxy

Environment:
  GPUAI_API_KEY    — GPU.ai bearer token (default: live key from memory)
  GPUAI_BASE_URL   — GPU.ai API base (default: https://api.gpu.ai/v1)
  GPUAI_SSH_KEY_ID — pre-registered SSH key ID for instance launch
  GPUAI_GPU_TYPE   — preferred GPU type (default: a100_40gb)
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/splat", tags=["splat-pipeline"])

# ── Config ─────────────────────────────────────────────────────────────────────

_GPUAI_KEY     = os.environ.get("GPUAI_API_KEY", "")
_GPUAI_BASE    = os.environ.get("GPUAI_BASE_URL", "https://api.gpu.ai/v1").rstrip("/")
_GPU_TYPE      = os.environ.get("GPUAI_GPU_TYPE", "a100_40gb")
_SSH_KEY_ID    = os.environ.get("GPUAI_SSH_KEY_ID", "")

VANTAGE_URL    = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8000")

# ── Schema ─────────────────────────────────────────────────────────────────────

_TABLES_CREATED = False


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS splat_jobs (
            job_id             TEXT PRIMARY KEY,
            agent_id           INTEGER NOT NULL,
            scene_hash         TEXT NOT NULL,
            colmap_manifest    TEXT,
            gpu_type           TEXT NOT NULL,
            gpuai_instance_id  TEXT,
            gpuai_operation_id TEXT,
            status             TEXT NOT NULL DEFAULT 'pending',
            twin_receipt_id    TEXT,
            arp_receipt_id     TEXT,
            f1_score           REAL,
            error_msg          TEXT,
            created_at         REAL NOT NULL,
            updated_at         REAL NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sj_agent ON splat_jobs (agent_id, status, created_at DESC)"
    )
    await db.commit()
    _TABLES_CREATED = True


# ── GPU.ai HTTP helpers ────────────────────────────────────────────────────────

def _gpuai_request(method: str, path: str, body: Optional[dict] = None) -> dict:
    """Synchronous GPU.ai API call (used in executor-thread pattern)."""
    if not _GPUAI_KEY:
        raise RuntimeError(
            "GPUAI_API_KEY is not set — refusing to call GPU.ai with an empty bearer token. "
            "Set the key in the environment (never in source)."
        )
    url  = f"{_GPUAI_BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {_GPUAI_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    if method in ("POST", "PUT", "PATCH") and data:
        headers["Idempotency-Key"] = str(uuid.uuid4())

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        raise RuntimeError(f"GPU.ai {method} {path} → HTTP {e.code}: {body_bytes.decode()[:300]}")


import asyncio as _asyncio


async def _gpuai(method: str, path: str, body: Optional[dict] = None) -> dict:
    """Async wrapper — runs synchronous GPU.ai call in thread pool."""
    loop = _asyncio.get_event_loop()
    return await loop.run_in_executor(None, _gpuai_request, method, path, body)


# ── Request models ─────────────────────────────────────────────────────────────

class SplatJobCreate(BaseModel):
    scene_hash:          str
    colmap_manifest_url: Optional[str] = None
    gpu_type:            str = _GPU_TYPE
    offering_id:         Optional[str] = None   # specific GPU.ai offering; if None, auto-select


# ── Offering selection ─────────────────────────────────────────────────────────

async def _pick_offering(gpu_type: str) -> Optional[str]:
    """Return the cheapest available offering_id for the requested GPU type."""
    try:
        pricing = await _gpuai("GET", "/pricing")
        offerings = pricing if isinstance(pricing, list) else pricing.get("data", [])
        matching = [
            o for o in offerings
            if o.get("gpu_type", "").lower().replace(" ", "_") == gpu_type.lower()
               or gpu_type.lower() in o.get("gpu_type", "").lower()
        ]
        if not matching:
            return None
        cheapest = sorted(matching, key=lambda o: o.get("price_per_hour", 9999))[0]
        return cheapest.get("offering_id") or cheapest.get("id")
    except Exception as e:
        logger.debug("splat: offering lookup failed: %s", e)
        return None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/jobs", summary="Submit a Gaussian splat training job")
async def submit_splat_job(
    body:  SplatJobCreate,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    """
    Provisions a GPU.ai instance and queues a splat training run.
    Returns immediately with job_id and operation_id; poll /jobs/{id} for status.
    """
    await _ensure_tables(db)
    agent_id = agent["id"]

    job_id  = str(uuid.uuid4())
    now     = time.time()
    gpu_type = body.gpu_type or _GPU_TYPE

    instance_id  = None
    operation_id = None
    status       = "pending"
    error_msg    = None

    # Try to provision GPU.ai instance (fail-open — job is recorded regardless)
    try:
        offering_id = body.offering_id or await _pick_offering(gpu_type)
        if not offering_id:
            raise RuntimeError(f"No available offerings for GPU type '{gpu_type}'")

        launch_payload: dict[str, Any] = {
            "gpu_type":   gpu_type,
            "gpu_count":  1,
            "tier":       "on_demand",
            "offering_id": offering_id,
        }
        if _SSH_KEY_ID:
            launch_payload["ssh_key_ids"] = [_SSH_KEY_ID]

        resp = await _gpuai("POST", "/instances", launch_payload)
        instance_id  = resp.get("id") or resp.get("instance_id")
        operation_id = resp.get("operation_id") or resp.get("id")
        status       = "provisioning"
        logger.info("splat job %s: GPU.ai instance %s provisioning", job_id, instance_id)

    except Exception as e:
        error_msg = str(e)
        status    = "error"
        logger.warning("splat job %s: GPU.ai provisioning failed (fail-open): %s", job_id, e)

    await db.execute(
        """INSERT INTO splat_jobs
           (job_id, agent_id, scene_hash, colmap_manifest, gpu_type,
            gpuai_instance_id, gpuai_operation_id, status, error_msg, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (job_id, agent_id, body.scene_hash, body.colmap_manifest_url, gpu_type,
         instance_id, operation_id, status, error_msg, now, now),
    )
    await db.commit()

    return {
        "job_id":           job_id,
        "status":           status,
        "gpu_type":         gpu_type,
        "gpuai_instance_id": instance_id,
        "gpuai_operation_id": operation_id,
        "error_msg":        error_msg,
        "created_at":       now,
    }


@router.get("/jobs", summary="List splat jobs for this agent")
async def list_splat_jobs(
    status: Optional[str] = None,
    limit:  int = Query(50, le=200),
    offset: int = 0,
    agent:  dict = Depends(get_agent),
    db:     aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    args: list[Any] = [agent["id"]]
    q = "SELECT * FROM splat_jobs WHERE agent_id=?"
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    cur = await db.execute(q, args)
    rows = [dict(r) for r in await cur.fetchall()]
    return {"jobs": rows, "count": len(rows)}


@router.get("/jobs/{job_id}", summary="Splat job status")
async def get_splat_job(
    job_id: str,
    agent:  dict = Depends(get_agent),
    db:     aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM splat_jobs WHERE job_id=? AND agent_id=?",
                           (job_id, agent["id"]))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    job = dict(row)

    # Live-poll GPU.ai operation status if provisioning
    if job.get("gpuai_operation_id") and job["status"] == "provisioning":
        try:
            op = await _gpuai("GET", f"/operations/{job['gpuai_operation_id']}")
            op_status = op.get("status", "unknown")
            if op_status in ("completed", "done", "success"):
                new_status = "running"
            elif op_status in ("failed", "error"):
                new_status = "error"
            else:
                new_status = "provisioning"
            if new_status != job["status"]:
                await db.execute(
                    "UPDATE splat_jobs SET status=?, updated_at=? WHERE job_id=?",
                    (new_status, time.time(), job_id),
                )
                await db.commit()
                job["status"] = new_status
            job["gpuai_operation"] = op
        except Exception as e:
            job["gpuai_operation_error"] = str(e)

    return job


class SplatCompleteBody(BaseModel):
    f1_score:        Optional[float] = None   # quality gate ≥ 0.777 for SceneReceipt (kind 31030)
    output_hash:     Optional[str]   = None   # SHA-256 of the .splat file
    artifact_url:    Optional[str]   = None   # storage URL for the trained splat


@router.post("/jobs/{job_id}/complete", summary="Mark splat job complete + create twin receipt")
async def complete_splat_job(
    job_id: str,
    body:   SplatCompleteBody,
    agent:  dict = Depends(get_agent),
    db:     aiosqlite.Connection = Depends(get_db),
):
    """
    Called when the splat training completes (webhook or polling).
    Creates a kind:31030 SceneReceipt if f1_score >= 0.777.
    Terminates the GPU.ai instance.
    """
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM splat_jobs WHERE job_id=? AND agent_id=?",
                           (job_id, agent["id"]))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    job = dict(row)

    f1   = body.f1_score
    now  = time.time()
    twin_receipt_id = None

    # Submit kind:31030 SceneReceipt if quality gate passes
    if f1 is not None and f1 >= 0.777:
        try:
            receipt_payload = {
                "receipt_id":   str(uuid.uuid4()),
                "kind":         31030,
                "twin_id":      job["scene_hash"][:32],
                "agent_id":     str(agent["id"]),
                "merkle_root":  body.output_hash or job["scene_hash"],
                "f1_score":     f1,
                "outcome":      "captured",
                "raw_json":     json.dumps({
                    "job_id":       job_id,
                    "scene_hash":   job["scene_hash"],
                    "artifact_url": body.artifact_url,
                    "f1_score":     f1,
                    "gpu_type":     job["gpu_type"],
                }),
            }
            import urllib.request as _ur
            req = _ur.Request(
                f"{VANTAGE_URL}/api/twin-receipts",
                data=json.dumps(receipt_payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _ur.urlopen(req, timeout=10) as resp:
                r = json.loads(resp.read().decode())
                twin_receipt_id = r.get("receipt_id") or r.get("id")
        except Exception as e:
            logger.debug("splat complete: twin receipt submit failed (fail-open): %s", e)

    # Terminate the GPU.ai instance (fail-open)
    if job.get("gpuai_instance_id"):
        try:
            await _gpuai("DELETE", f"/instances/{job['gpuai_instance_id']}")
            logger.info("splat job %s: GPU.ai instance %s terminated", job_id, job["gpuai_instance_id"])
        except Exception as e:
            logger.debug("splat complete: instance termination failed (fail-open): %s", e)

    status = "completed" if (f1 is None or f1 >= 0.777) else "failed_quality_gate"
    await db.execute(
        """UPDATE splat_jobs
           SET status=?, f1_score=?, twin_receipt_id=?, updated_at=?
           WHERE job_id=?""",
        (status, f1, twin_receipt_id, now, job_id),
    )
    await db.commit()

    return {
        "job_id":           job_id,
        "status":           status,
        "f1_score":         f1,
        "twin_receipt_id":  twin_receipt_id,
        "f1_gate_passed":   f1 is not None and f1 >= 0.777,
        "completed_at":     now,
    }


@router.delete("/jobs/{job_id}", summary="Cancel splat job and terminate GPU instance")
async def cancel_splat_job(
    job_id: str,
    agent:  dict = Depends(get_agent),
    db:     aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM splat_jobs WHERE job_id=? AND agent_id=?",
                           (job_id, agent["id"]))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    job = dict(row)

    if job["status"] in ("completed", "failed_quality_gate"):
        raise HTTPException(400, f"Cannot cancel a job with status '{job['status']}'")

    if job.get("gpuai_instance_id"):
        try:
            await _gpuai("DELETE", f"/instances/{job['gpuai_instance_id']}")
        except Exception as e:
            logger.debug("splat cancel: instance termination failed (fail-open): %s", e)

    await db.execute(
        "UPDATE splat_jobs SET status='cancelled', updated_at=? WHERE job_id=?",
        (time.time(), job_id),
    )
    await db.commit()
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/pricing", summary="Live GPU.ai pricing proxy")
async def splat_pricing(agent: dict = Depends(get_agent)):
    """Returns live GPU.ai pricing — useful for agents choosing GPU tier."""
    try:
        pricing = await _gpuai("GET", "/pricing")
        return {"source": "gpu.ai", "pricing": pricing}
    except Exception as e:
        raise HTTPException(502, f"GPU.ai pricing unavailable: {e}")
