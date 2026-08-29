"""Narrative-detection endpoints -- see backend/narrative_detection.py for
the real keyword-pattern-mining methodology and its documented limits.
"""
import logging
import time

from fastapi import APIRouter, Header, HTTPException

from backend.narrative_detection import compute_narrative_heat, get_hot_narratives, get_combo_flags
from backend.routers.degen import get_agent

router = APIRouter(prefix="/api/intel/narratives", tags=["narratives"])
logger = logging.getLogger(__name__)


@router.post("/scan")
async def scan_narratives(x_agent_key: str = Header(...)):
    """Runs a real detection pass over currently-active pump.fun tokens
    (see narrative_detection.compute_narrative_heat) and persists results.
    Called lazily by /hot and /combo-flags when the caller wants fresh
    data, and can be triggered directly."""
    if not await get_agent(x_agent_key):
        raise HTTPException(401)
    return await compute_narrative_heat()


@router.get("/hot")
async def hot_narratives(refresh: bool = True, x_agent_key: str = Header(...)):
    """Currently-hot themes: >=2 distinct real tokens sharing a keyword/
    theme within the last 6h. Every theme includes sample_tokens (real
    mints, names, market caps) so the heat number is fully auditable --
    never a bare count with nothing behind it."""
    if not await get_agent(x_agent_key):
        raise HTTPException(401)
    if refresh:
        try:
            await compute_narrative_heat()
        except Exception as e:
            logger.warning("hot_narratives: scan failed, serving last-persisted state: %s", e)
    themes = await get_hot_narratives()
    return {"themes": themes, "count": len(themes), "generated_at": int(time.time())}


@router.get("/combo-flags")
async def combo_flags(limit: int = 20, x_agent_key: str = Header(...)):
    """Real tokens whose name/symbol combined 2+ already-hot narrative
    threads -- the actual 'PINKFONE' detection this feature exists for."""
    if not await get_agent(x_agent_key):
        raise HTTPException(401)
    flags = await get_combo_flags(limit=limit)
    return {"flags": flags, "count": len(flags), "generated_at": int(time.time())}
