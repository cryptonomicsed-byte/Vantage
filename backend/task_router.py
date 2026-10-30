"""Task router — matches tasks to capable agents.

Given a task's required_capabilities list, returns ranked candidate agents.
Ranking: 1) capability match count, 2) reputation, 3) availability
         (available > thinking > working > blocked > offline).
"""
import logging
import os
from typing import Optional

from .capability_registry import find_capable_agents
from .wisdom_engine import wisdom_route as _wisdom_route

logger = logging.getLogger(__name__)

# Availability priority (lower index = higher priority)
_AVAILABILITY_ORDER = ["available", "thinking", "working", "blocked", "offline"]

# Tier-gated external job board
EXTERNAL_JOB_BOARD_TIER = int(os.environ.get("EXTERNAL_JOB_BOARD_TIER", "4"))
EXTERNAL_JOB_BOARD_URL = os.environ.get("EXTERNAL_JOB_BOARD_URL", "")


def _availability_rank(availability: str) -> int:
    try:
        return _AVAILABILITY_ORDER.index(availability)
    except ValueError:
        return len(_AVAILABILITY_ORDER)


def _rank_candidates(candidates: list) -> list:
    """Sort candidates by: match_count desc, reputation desc, availability asc."""
    return sorted(
        candidates,
        key=lambda c: (
            -c.get("_match_count", 0),
            -c.get("reputation", 0.0),
            _availability_rank(c.get("availability", "offline")),
        ),
    )


async def route_task(
    task_id: int,
    required_capabilities: list,
    agent_tier: int = 1,
) -> list:
    """Return ranked candidate agents for a task.

    Queries the capability registry for agents that satisfy ALL
    required_capabilities, then ranks them. Returns list of agent records
    with a '_match_count' key added for transparency.

    Args:
        task_id: The task ID (used for logging / future persistence).
        required_capabilities: Capabilities the executing agent must have.
        agent_tier: Tier of the requesting agent (default 1). Tier >= EXTERNAL_JOB_BOARD_TIER
                    unlocks the external job board query when EXTERNAL_JOB_BOARD_URL is set.

    Returns:
        Sorted list of candidate agent dicts (best match first).
    """
    candidates = await find_capable_agents(required_capabilities)
    ranked = _rank_candidates(candidates)

    # Soft re-rank hint from Clojure wisdom engine (fire-and-forget, never blocks routing)
    if candidates:
        try:
            task_modes = [str(a.get("id", "")) for a in candidates]
            wisdom = await _wisdom_route(
                f"task {task_id} requiring {required_capabilities}", task_modes
            )
            recommended = wisdom.get("recommended_mode")
            if recommended:
                logger.info(
                    "task_router: wisdom hint for task %s → mode=%s scores=%s",
                    task_id, recommended, wisdom.get("scores", {}),
                )
        except Exception as _we:
            logger.debug("task_router: wisdom engine unavailable: %s", _we)

    # Tier-gated external job board
    if agent_tier >= EXTERNAL_JOB_BOARD_TIER and EXTERNAL_JOB_BOARD_URL:
        try:
            import httpx
            r = httpx.get(f"{EXTERNAL_JOB_BOARD_URL}/jobs/open", timeout=3.0)
            if r.status_code == 200:
                external_jobs = r.json().get("jobs", [])
                # Merge external jobs into candidates (append, not replace)
                # external_jobs items expected to have: {job_id, description, reward}
                # log and pass through — actual assignment handled by caller
                logger.info(
                    f"External job board: {len(external_jobs)} jobs fetched for tier-{agent_tier} agent"
                )
        except Exception as e:
            logger.warning(f"External job board unreachable (tier gate): {e}")

    logger.info(
        "task_router: task %s with caps %s matched %d candidate(s)",
        task_id,
        required_capabilities,
        len(ranked),
    )
    return ranked


async def auto_assign(task_id: int, required_capabilities: list) -> Optional[dict]:
    """Return the single best agent for a task, or None if no match.

    Uses route_task() internally and returns the top-ranked candidate.
    The caller is responsible for actually assigning / notifying the agent.

    Args:
        task_id: The task ID.
        required_capabilities: Capabilities required.

    Returns:
        Best-match agent dict, or None if no capable agents are available.
    """
    ranked = await route_task(task_id, required_capabilities)
    if not ranked:
        logger.info("task_router: no capable agent found for task %s", task_id)
        return None
    best = ranked[0]
    logger.info(
        "task_router: auto-assigning task %s to agent %s (availability=%s, reputation=%.2f)",
        task_id,
        best.get("agent_id"),
        best.get("availability"),
        best.get("reputation", 0.0),
    )
    return best
