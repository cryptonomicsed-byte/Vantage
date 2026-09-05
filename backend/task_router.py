"""Task router — matches tasks to capable agents.

Given a task's required_capabilities list, returns ranked candidate agents.
Ranking: 1) capability match count, 2) reputation, 3) availability
         (available > thinking > working > blocked > offline).
"""
import logging
from typing import Optional

from .capability_registry import find_capable_agents

logger = logging.getLogger(__name__)

# Availability priority (lower index = higher priority)
_AVAILABILITY_ORDER = ["available", "thinking", "working", "blocked", "offline"]


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


async def route_task(task_id: int, required_capabilities: list) -> list:
    """Return ranked candidate agents for a task.

    Queries the capability registry for agents that satisfy ALL
    required_capabilities, then ranks them. Returns list of agent records
    with a '_match_count' key added for transparency.

    Args:
        task_id: The task ID (used for logging / future persistence).
        required_capabilities: Capabilities the executing agent must have.

    Returns:
        Sorted list of candidate agent dicts (best match first).
    """
    candidates = await find_capable_agents(required_capabilities)
    ranked = _rank_candidates(candidates)
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
