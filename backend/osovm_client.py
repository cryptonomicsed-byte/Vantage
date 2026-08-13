"""Pluggable hook for OSOVM (Proof-of-Simulation VM) attestation.

Real gap this closes: as of the 2026-08 cross-pillar audit, Vantage had
zero code coupling with OSOVM -- no config, no client, no column to record
a proof against a job_task. This is that pluggable hook, following the
same contract as OMOKODA_URL/omokoda_cognition_proxy.py: empty
settings.OSOVM_URL means every function here is a no-op returning None,
not a fabricated call to an endpoint nobody has confirmed is live yet.

Wiring an actual caller (e.g. job_tasks approval requiring a proof before
payout) is a separate, deliberate decision for whoever owns that flow --
this module only makes the connection possible to build.
"""
import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.OSOVM_URL)


async def get_proof(sim_hash: str) -> Optional[dict]:
    """Fetch a determinism proof for a given sim hash, if OSOVM is configured.

    Returns None (not an error) when OSOVM_URL is unset -- callers should
    treat that as "no attestation available", the same degrade-gracefully
    behavior copilot.py already uses for an agent with no cognition_url.
    """
    if not enabled():
        return None
    headers = {}
    if settings.OSOVM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OSOVM_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{settings.OSOVM_URL.rstrip('/')}/v1/proof/{sim_hash}",
                headers=headers,
            )
            if r.status_code == 200:
                return r.json()
            logger.warning("OSOVM proof lookup failed: %s %s", r.status_code, r.text[:200])
            return None
    except Exception as e:
        logger.warning("OSOVM proof lookup error: %s", e)
        return None
