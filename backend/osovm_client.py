"""HTTP client for the OSOVM simulation engine at :7780.

Real gap this closes: as of the 2026-08 cross-pillar audit, Vantage had
zero code coupling with OSOVM -- no config, no client, no column to record
a proof against a job_task. This module is that pluggable hook, following
the same contract as OMOKODA_URL/omokoda_cognition_proxy.py: empty
VANTAGE_OSOVM_URL means every function here is a no-op returning a clear
error, not a fabricated call to an endpoint nobody has confirmed is live yet.

Wiring an actual caller (e.g. job_tasks approval requiring a proof before
payout) is a separate, deliberate decision for whoever owns that flow --
this module only makes the connection possible to build.
"""
import logging
import os
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# Read directly from env as well as settings (settings uses VANTAGE_ prefix)
OSOVM_URL: str = settings.OSOVM_URL or os.getenv("VANTAGE_OSOVM_URL", "")
OSOVM_API_KEY: str = settings.OSOVM_API_KEY or os.getenv("VANTAGE_OSOVM_API_KEY", "")

_TIMEOUT = 30.0


class OsovmError(Exception):
    """Raised when OSOVM is not configured or returns a non-ok response."""


def _configured() -> bool:
    return bool(OSOVM_URL)


def _headers() -> dict:
    h: dict = {"Content-Type": "application/json"}
    if OSOVM_API_KEY:
        h["Authorization"] = f"Bearer {OSOVM_API_KEY}"
    return h


async def is_available() -> bool:
    """Check if OSOVM server is reachable via GET /health."""
    if not _configured():
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{OSOVM_URL.rstrip('/')}/health",
                headers=_headers(),
            )
            return r.status_code == 200
    except Exception as exc:
        logger.debug("OSOVM health check failed: %s", exc)
        return False


async def run_opcode(opcode: str, args: dict, agent: str) -> dict:
    """POST /run to OSOVM.

    Returns {status, f1_score, ase_minted, receipts, run_id, ...}.
    Raises OsovmError if OSOVM is not configured, if the HTTP call fails,
    or if the response status field is not "ok".
    """
    if not _configured():
        raise OsovmError("OSOVM not configured")
    payload = {"opcode": opcode, "args": args or {}, "agent": agent}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{OSOVM_URL.rstrip('/')}/run",
                json=payload,
                headers=_headers(),
            )
            r.raise_for_status()
            result = r.json()
    except httpx.HTTPStatusError as exc:
        raise OsovmError(f"OSOVM HTTP error {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except Exception as exc:
        raise OsovmError(f"OSOVM request failed: {exc}") from exc

    if result.get("status") != "ok":
        raise OsovmError(f"OSOVM returned non-ok status: {result.get('status')} — {result}")
    return result


async def run_veilsim(
    veil_ids: list,
    entity_count: int,
    step_count: int,
    agent: str,
) -> dict:
    """POST /veilsim/run to OSOVM. Returns sim result."""
    if not _configured():
        raise OsovmError("OSOVM not configured")
    payload = {
        "veil_ids": veil_ids,
        "entity_count": entity_count,
        "step_count": step_count,
        "agent": agent,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{OSOVM_URL.rstrip('/')}/veilsim/run",
                json=payload,
                headers=_headers(),
            )
            r.raise_for_status()
            result = r.json()
    except httpx.HTTPStatusError as exc:
        raise OsovmError(f"OSOVM veilsim HTTP error {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except Exception as exc:
        raise OsovmError(f"OSOVM veilsim request failed: {exc}") from exc
    return result


async def attest_receipt(receipt_id: str, agent_ref: str, work_ref: str = "") -> dict:
    """Full attestation flow.

    1. Calls run_opcode("RECEIPT", {"receipt_id": receipt_id, "agent": agent_ref,
       "work_ref": work_ref}, agent_ref)
    2. Returns the OSOVM result with f1_score and ase_minted.

    Raises OsovmError if OSOVM is not configured or the attestation fails.
    """
    args: dict = {"receipt_id": receipt_id, "agent": agent_ref}
    if work_ref:
        args["work_ref"] = work_ref
    return await run_opcode("RECEIPT", args, agent_ref)


# ── Legacy compatibility shim ─────────────────────────────────────────────────
# The original osovm_client.py (pre-2026-09 stub) exported `enabled()` and
# `get_proof()`. Keep them so any existing callers don't break while the new
# full surface is adopted.

def enabled() -> bool:
    return _configured()


async def get_proof(sim_hash: str) -> Optional[dict]:
    """Fetch a determinism proof for a given sim hash, if OSOVM is configured.

    Returns None (not an error) when OSOVM_URL is unset -- callers should
    treat that as "no attestation available".
    """
    if not _configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{OSOVM_URL.rstrip('/')}/v1/proof/{sim_hash}",
                headers=_headers(),
            )
            if r.status_code == 200:
                return r.json()
            logger.warning("OSOVM proof lookup failed: %s %s", r.status_code, r.text[:200])
            return None
    except Exception as exc:
        logger.warning("OSOVM proof lookup error: %s", exc)
        return None
