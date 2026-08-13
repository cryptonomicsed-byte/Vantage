"""Pluggable hook for Bondhive (Solana/Anchor stake+slash reputation).

Real gap this closes: as of the 2026-08 cross-pillar audit, Vantage had
zero code coupling with Bondhive -- no config, no client, no column
linking a Vantage agent to a Bondhive stake account. This is that
pluggable hook, following the same empty-URL-means-disabled contract as
OMOKODA_URL: settings.BONDHIVE_RPC_URL unset means every function here is
a no-op returning None.

Explicitly NOT resolved by this module: Vantage's own BlockMesh trust
system (routers/mesh.py, /api/mesh/trust/*) is a separate, independent
reputation signal from Bondhive's BondScore. Wiring this client does not
decide which one is authoritative for gating a given action -- that's a
real open decision for whoever owns that reconciliation, flagged in the
round-3 cross-pillar connection survey.
"""
import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.BONDHIVE_RPC_URL)


async def get_bond_score(stake_account: str) -> Optional[dict]:
    """Fetch an agent's BondScore/stake state from Bondhive, if configured.

    Returns None (not an error) when BONDHIVE_RPC_URL is unset -- callers
    should treat that as "no Bondhive reputation available" and fall back
    to Vantage's own BlockMesh trust score, not treat it as a failure.
    """
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{settings.BONDHIVE_RPC_URL.rstrip('/')}/v1/stake/{stake_account}",
                params={"program_id": settings.BONDHIVE_PROGRAM_ID} if settings.BONDHIVE_PROGRAM_ID else {},
            )
            if r.status_code == 200:
                return r.json()
            logger.warning("Bondhive stake lookup failed: %s %s", r.status_code, r.text[:200])
            return None
    except Exception as e:
        logger.warning("Bondhive stake lookup error: %s", e)
        return None
