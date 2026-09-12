"""OSOVM simulation-engine router.

Exposes the OSOVM HTTP operations (run opcode, attest receipt, run VeilSim)
as Vantage REST endpoints so any authenticated agent can drive the
Proof-of-Useful-Simulation VM without needing a direct network path to :7780.

All endpoints require a valid X-Agent-Key (via get_agent dependency).
OSOVM must be reachable (VANTAGE_OSOVM_URL set); otherwise endpoints return
503 or the health check returns available=false.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_agent
from .. import osovm_client
from ..osovm_client import OSOVM_URL, OsovmError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/osovm", tags=["osovm"])


# ── Health ────────────────────────────────────────────────────────────────────

@router.get(
    "/health",
    summary="Check if OSOVM is reachable",
    description="Returns {available, url}. Does not require authentication.",
)
async def osovm_health():
    """Check if OSOVM server is up. Public — no agent key required."""
    available = await osovm_client.is_available()
    return {
        "available": available,
        "url": OSOVM_URL or "not_configured",
    }


# ── Run opcode ────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    summary="Run an OSOVM opcode",
    description=(
        "POST body: {opcode: str, args: dict (optional)}. "
        "Returns the raw OSOVM result including f1_score, ase_minted, receipts, run_id."
    ),
)
async def osovm_run(
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Run an arbitrary OSOVM opcode on behalf of the calling agent."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    opcode = body.get("opcode")
    if not opcode:
        raise HTTPException(status_code=400, detail="'opcode' is required")

    args = body.get("args") or {}
    agent_name: str = agent.get("name") or str(agent["id"])

    try:
        result = await osovm_client.run_opcode(opcode, args, agent_name)
    except OsovmError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    logger.info("osovm_run: agent=%s opcode=%s run_id=%s", agent_name, opcode, result.get("run_id"))
    return result


# ── Attest receipt ────────────────────────────────────────────────────────────

@router.post(
    "/attest/{receipt_id}",
    summary="Attest a receipt via OSOVM",
    description=(
        "Runs the RECEIPT opcode against the given receipt_id and returns "
        "the OSOVM attestation result (f1_score, ase_minted, etc.)."
    ),
)
async def osovm_attest(
    receipt_id: str,
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Attest a Vantage receipt through OSOVM."""
    agent_name: str = agent.get("name") or str(agent["id"])

    # Optional work_ref from body
    work_ref = ""
    try:
        body = await request.json()
        work_ref = body.get("work_ref", "") or ""
    except Exception:
        pass  # body is optional for this endpoint

    try:
        result = await osovm_client.attest_receipt(receipt_id, agent_name, work_ref)
    except OsovmError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    logger.info(
        "osovm_attest: agent=%s receipt_id=%s f1_score=%s ase_minted=%s",
        agent_name,
        receipt_id,
        result.get("f1_score"),
        result.get("ase_minted"),
    )
    return {
        **result,
        "receipt_id": receipt_id,
        "attested_by": agent_name,
    }


# ── VeilSim ───────────────────────────────────────────────────────────────────

@router.post(
    "/veilsim",
    summary="Run a VeilSim scenario",
    description=(
        "POST body: {veil_ids: list[str], entity_count: int, step_count: int}. "
        "Returns the OSOVM VeilSim run result."
    ),
)
async def osovm_veilsim(
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Run a VeilSim scenario through OSOVM."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    veil_ids = body.get("veil_ids")
    if not veil_ids or not isinstance(veil_ids, list):
        raise HTTPException(status_code=400, detail="'veil_ids' must be a non-empty list")

    entity_count = body.get("entity_count")
    step_count = body.get("step_count")
    if entity_count is None or step_count is None:
        raise HTTPException(status_code=400, detail="'entity_count' and 'step_count' are required")

    agent_name: str = agent.get("name") or str(agent["id"])

    try:
        result = await osovm_client.run_veilsim(
            veil_ids=veil_ids,
            entity_count=int(entity_count),
            step_count=int(step_count),
            agent=agent_name,
        )
    except OsovmError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    logger.info(
        "osovm_veilsim: agent=%s veil_ids=%s entity_count=%d step_count=%d",
        agent_name, veil_ids, entity_count, step_count,
    )
    return result
