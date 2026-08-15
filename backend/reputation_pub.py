"""
Reputation publishing — writes Julia-computed trust scores back to Vantage
for display purposes. The Julia /mesh/score endpoint is the authoritative
source; this module caches the result in mesh_agents.trust_score.
"""

import logging
from typing import Optional

import aiosqlite
import httpx

from . import bondhive_client
from .config import settings
from .db import DB_PATH, get_db

log = logging.getLogger(__name__)

JULIA_MESH_SCORE_PATH = "/mesh/score"


async def resolve_bondhive_prior(block_id: str, neighbor_mesh_agent_id: str) -> float:
    """Decision 3 (owner, 2026-08-15): BlockMesh stays authoritative for
    trust; Bondhive's BondScore becomes an input signal INTO it, not a
    competing authority. The pairwise trust_signal event model (from_agent
    vouching for to_agent) doesn't fit BondScore's actual shape -- it's an
    absolute, agent-owned reputation, not a relational one. This function
    is the real integration point instead: Julia's /mesh/score already
    accepts a `prior` (default neutral 0.5, never previously overridden
    anywhere in the codebase) -- Bondhive-informed agents get their prior
    seeded from BondScore instead of the flat default.

    Never blocks or degrades scoring. Falls back to the neutral 0.5
    default whenever: Bondhive is disabled (BONDHIVE_RPC_URL unset), the
    mesh agent isn't linked to a real Vantage account (mesh agent_id is
    not 1:1 with agents.id -- an OSOVM witness or other external agent can
    join a block with no corresponding Vantage account at all), no
    bondhive_stake_account is set, or the lookup fails.

    NOTE: Bondhive's exact response schema is not yet confirmed on this
    side (wC's pillar owns it) -- this does a best-effort field lookup
    (bond_score/score/reputation) clamped into Julia's expected [0,1]
    prior range. If Bondhive's real scale isn't 0-1, this clamp alone
    won't rescale it correctly. Revisit once wC confirms the real shape;
    until then, anything unrecognized degrades to neutral rather than
    erroring.
    """
    if not bondhive_client.enabled():
        return 0.5

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        mesh_row = await (await db.execute(
            "SELECT vantage_name FROM mesh_agents WHERE agent_id=? AND block_id=?",
            (neighbor_mesh_agent_id, block_id),
        )).fetchone()
        if not mesh_row or not mesh_row["vantage_name"]:
            return 0.5
        agent_row = await (await db.execute(
            "SELECT bondhive_stake_account FROM agents WHERE name=?",
            (mesh_row["vantage_name"],),
        )).fetchone()
        if not agent_row or not agent_row["bondhive_stake_account"]:
            return 0.5

    result = await bondhive_client.get_bond_score(agent_row["bondhive_stake_account"])
    if not result:
        return 0.5

    raw = result.get("bond_score", result.get("score", result.get("reputation")))
    if raw is None:
        return 0.5
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, score))


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
