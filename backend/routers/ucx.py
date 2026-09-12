"""
UCX Bootstrap Rendezvous + Dopamine mint ledger — Vantage as provider registry.

Vantage is the discovery layer, NOT the broker.
It stores capability advertisements and serves them to the UCX broker on demand.
It also records Dopamine allocations from VerifiedGPUWork submissions.

Routes:
  POST /api/ucx/providers/register          — Provider Agent announces itself
  POST /api/ucx/providers/{id}/heartbeat    — Provider keeps its registration alive
  GET  /api/ucx/providers                   — UCX Broker lists active providers
  GET  /api/ucx/providers/{id}              — UCX Broker fetches a single provider
  POST /api/ucx/dopamine/mint               — Credit Dopamine from VerifiedGPUWork
  GET  /api/ucx/dopamine/balance/{agent_id} — Agent Dopamine balance
  GET  /api/ucx/dopamine/ledger             — Recent allocation history

Auth: same X-Agent-Key as the rest of Vantage.
"""

import logging
import math
import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from ..db import DB_PATH, get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ucx", tags=["ucx"])

# ---- DB bootstrap -----------------------------------------------------------

_TABLE_CREATED = False

async def _ensure_table(db: aiosqlite.Connection) -> None:
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS ucx_providers (
            provider_id   TEXT PRIMARY KEY,
            capability    TEXT NOT NULL,
            registered_at REAL NOT NULL,
            last_seen_at  REAL NOT NULL
        )
    """)
    await db.commit()
    _TABLE_CREATED = True


# ---- Helpers ----------------------------------------------------------------

import json, time

_STALE_SECS = 120   # provider offline after 2 min without heartbeat


# ---- Routes -----------------------------------------------------------------

@router.post("/providers/register")
async def register_provider(
    body: dict[str, Any],
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Provider Agent announces its ProviderCapability."""
    await _ensure_table(db)
    provider_id = body.get("provider_id")
    if not provider_id:
        raise HTTPException(400, "capability.provider_id required")

    now = time.time()
    cap_json = json.dumps(body)
    await db.execute(
        """
        INSERT INTO ucx_providers (provider_id, capability, registered_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(provider_id) DO UPDATE SET
            capability   = excluded.capability,
            last_seen_at = excluded.last_seen_at
        """,
        (provider_id, cap_json, now, now),
    )
    await db.commit()
    logger.info("ucx: provider registered provider_id=%s agent=%s", provider_id, agent.get("name"))
    return {"ok": True, "provider_id": provider_id}


@router.post("/providers/{provider_id}/heartbeat")
async def provider_heartbeat(
    provider_id: str,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Keep a provider registration alive.  Must be called every <2 min."""
    await _ensure_table(db)
    await db.execute(
        "UPDATE ucx_providers SET last_seen_at = ? WHERE provider_id = ?",
        (time.time(), provider_id),
    )
    await db.commit()
    return {"ok": True, "provider_id": provider_id}


@router.get("/providers")
async def list_providers(
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return all non-stale ProviderCapability objects."""
    await _ensure_table(db)
    cutoff = time.time() - _STALE_SECS
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT capability FROM ucx_providers WHERE last_seen_at > ? ORDER BY last_seen_at DESC",
        (cutoff,)
    ) as cur:
        rows = await cur.fetchall()
    return {"providers": [json.loads(r["capability"]) for r in rows]}


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: str,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Fetch a single provider by ID (stale or active)."""
    await _ensure_table(db)
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT capability, last_seen_at FROM ucx_providers WHERE provider_id = ?",
        (provider_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"provider {provider_id!r} not found")
    cap = json.loads(row["capability"])
    stale = (time.time() - row["last_seen_at"]) > _STALE_SECS
    return {"provider": cap, "stale": stale}


@router.delete("/providers/{provider_id}")
async def deregister_provider(
    provider_id: str,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Provider Agent voluntarily removes itself from the registry."""
    await _ensure_table(db)
    await db.execute("DELETE FROM ucx_providers WHERE provider_id = ?", (provider_id,))
    await db.commit()
    return {"ok": True, "provider_id": provider_id}


# ── Dopamine Mint Ledger ──────────────────────────────────────────────────────
#
# Mirrors kernel/compute/accounting.rs ComputeScore + DopamineAllocation.
# Rate: 1 Dopamine per 10 GPU-minutes at 100% utilization.
#   micro_dopamine = gpu_seconds * quality_score * (1_000_000 / 600)

_MICRO_PER_GPU_SEC = 1_000_000.0 / 600.0   # from DopamineAllocation::MICRO_DOPAMINE_PER_GPU_SEC
_DOPAMINE_LEDGER_CREATED = False


async def _ensure_dopamine_tables(db: aiosqlite.Connection) -> None:
    global _DOPAMINE_LEDGER_CREATED
    if _DOPAMINE_LEDGER_CREATED:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS dopamine_allocations (
            alloc_id        TEXT PRIMARY KEY,
            work_id         TEXT NOT NULL,
            agent_id        TEXT NOT NULL,
            device_id       TEXT NOT NULL,
            gpu_seconds     REAL NOT NULL,
            utilization     REAL NOT NULL,
            witness_count   INTEGER NOT NULL,
            quality_score   REAL NOT NULL,
            micro_dopamine  INTEGER NOT NULL,
            osovm_epoch     INTEGER,
            zangbeto_id     TEXT,
            workload_type   TEXT,
            osovm_proof     TEXT,
            created_at      REAL NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dopa_agent ON dopamine_allocations (agent_id, created_at DESC)"
    )
    await db.commit()
    _DOPAMINE_LEDGER_CREATED = True


def _compute_score(gpu_seconds: float, utilization: float, witness_count: int) -> float:
    """Mirror ComputeScore::compute() from accounting.rs."""
    witness_factor = {0: 0.0, 1: 0.5, 2: 0.75}.get(witness_count, 1.0)
    util_factor    = max(0.0, min(1.0, utilization))
    raw_score      = min(gpu_seconds / 3600.0, 1.0) * util_factor * witness_factor
    return max(0.0, min(1.0, raw_score))


@router.post(
    "/dopamine/mint",
    summary="Credit Dopamine from a VerifiedGPUWork submission",
    description=(
        "Body: {work_id, agent_id, device_id, gpu_seconds, utilization, witness_count, "
        "workload_type, osovm_proof (optional), osovm_epoch (optional), zangbeto_id (optional)}. "
        "Uses same scoring formula as kernel/compute/accounting.rs. "
        "Returns {alloc_id, micro_dopamine, dopamine_units, quality_score}."
    ),
)
async def mint_dopamine(
    body: dict[str, Any],
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Record a Dopamine allocation from a completed VerifiedGPUWork."""
    await _ensure_dopamine_tables(db)

    work_id       = (body.get("work_id") or "").strip()
    agent_id      = (body.get("agent_id") or "").strip()
    device_id     = (body.get("device_id") or "").strip()
    if not all([work_id, agent_id, device_id]):
        raise HTTPException(400, "work_id, agent_id, device_id required")

    try:
        gpu_seconds   = float(body["gpu_seconds"])
        utilization   = float(body.get("utilization", 1.0))
        witness_count = int(body.get("witness_count", 0))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "gpu_seconds required (float); utilization/witness_count optional")

    if gpu_seconds <= 0:
        raise HTTPException(400, "gpu_seconds must be positive")

    # Require at least one witness (chain integrity gate)
    if witness_count == 0:
        raise HTTPException(422, "at least 1 witness receipt required for Dopamine allocation")

    quality_score  = _compute_score(gpu_seconds, utilization, witness_count)
    micro_dopamine = int(gpu_seconds * quality_score * _MICRO_PER_GPU_SEC)

    alloc_id      = str(uuid.uuid4())
    osovm_epoch   = body.get("osovm_epoch")
    zangbeto_id   = body.get("zangbeto_id")
    workload_type = body.get("workload_type", "custom")
    osovm_proof   = body.get("osovm_proof")

    await db.execute(
        """
        INSERT INTO dopamine_allocations
            (alloc_id, work_id, agent_id, device_id, gpu_seconds, utilization,
             witness_count, quality_score, micro_dopamine, osovm_epoch,
             zangbeto_id, workload_type, osovm_proof, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (alloc_id, work_id, agent_id, device_id, gpu_seconds, utilization,
         witness_count, quality_score, micro_dopamine, osovm_epoch,
         zangbeto_id, workload_type, osovm_proof, time.time()),
    )
    await db.commit()

    logger.info(
        "dopamine mint: agent=%s work=%s gpu_secs=%.1f score=%.4f micro=%d",
        agent_id, work_id, gpu_seconds, quality_score, micro_dopamine,
    )

    return {
        "alloc_id":       alloc_id,
        "work_id":        work_id,
        "agent_id":       agent_id,
        "micro_dopamine": micro_dopamine,
        "dopamine_units": micro_dopamine / 1_000_000.0,
        "quality_score":  quality_score,
        "gpu_seconds":    gpu_seconds,
        "utilization":    utilization,
        "witness_count":  witness_count,
    }


@router.get(
    "/dopamine/balance/{agent_id}",
    summary="Agent Dopamine balance",
)
async def dopamine_balance(
    agent_id: str,
    caller: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _ensure_dopamine_tables(db)
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT SUM(micro_dopamine) AS total, COUNT(*) AS alloc_count FROM dopamine_allocations WHERE agent_id = ?",
        (agent_id,),
    ) as cur:
        row = dict(await cur.fetchone())

    total_micro = row["total"] or 0
    return {
        "agent_id":       agent_id,
        "micro_dopamine": total_micro,
        "dopamine_units": total_micro / 1_000_000.0,
        "alloc_count":    row["alloc_count"],
    }


@router.get(
    "/dopamine/ledger",
    summary="Recent Dopamine allocation history",
)
async def dopamine_ledger(
    limit: int = 50,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _ensure_dopamine_tables(db)
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT * FROM dopamine_allocations ORDER BY created_at DESC LIMIT ?",
        (min(limit, 500),),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return {"allocations": rows, "count": len(rows)}
