"""
Reputation publishing — writes Julia-computed trust scores back to Vantage
for display purposes. The Julia /mesh/score endpoint is the authoritative
source; this module caches the result in mesh_agents.trust_score.
"""

import logging
from typing import Optional

import aiosqlite
import httpx

from .config import settings
from .db import DB_PATH, get_db

log = logging.getLogger(__name__)

JULIA_MESH_SCORE_PATH = "/mesh/score"


async def publish_julia_score(
    block_id: str,
    agent_id: str,
    neighbor_id: str,
    signals: list[dict],
    prior: float = 0.5,
) -> Optional[float]:
    """
    Ask Julia to compute the trust score for (agent_id → neighbor_id) given
    the signals, then cache the result in mesh_agents.trust_score.

    Returns the computed score, or None if Julia is unavailable.
    The Julia base URL is read from settings.STEWARD_URL (VANTAGE_STEWARD_URL env var).
    """
    julia_url = getattr(settings, "STEWARD_URL", "") or ""
    if not julia_url.strip():
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                julia_url.rstrip("/") + JULIA_MESH_SCORE_PATH,
                json={
                    "agent_id": agent_id,
                    "neighbor_id": neighbor_id,
                    "signals": signals,
                    "prior": prior,
                },
            )
        if not resp.is_success:
            log.debug("julia /mesh/score returned %s", resp.status_code)
            return None

        data = resp.json()
        score = float(data.get("trust_score", prior))

        # Cache in mesh_agents for display (not authoritative — Julia owns the score)
        async with get_db() as db:
            await db.execute(
                """UPDATE mesh_agents SET trust_score = ?
                   WHERE agent_id = ? AND block_id = ?""",
                (min(100.0, score * 100.0), neighbor_id, block_id),
            )
            await db.commit()

        return score

    except Exception as e:
        log.debug("reputation_pub: julia unavailable: %s", e)
        return None
