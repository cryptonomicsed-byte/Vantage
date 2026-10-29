"""Task router — matches tasks to capable agents.

Given a task's required_capabilities list, returns ranked candidate agents.
Ranking: 1) capability match count, 2) mode affinity (P4: a candidate whose
declared capabilities carry the task's mode prefix is preferred), 3)
open-task load (P4: fewer open guild tasks wins), 4) reputation,
5) availability (available > thinking > working > blocked > offline).

The router decides WHO; the caller persists the delegation row and notifies
(P4 — auto_assign itself still does not write side effects).
"""
import json
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


def _mode_match(candidate: dict, mode: Optional[str]) -> int:
    """1 if the candidate declares the mode (or mode:*) capability, else 0."""
    if not mode:
        return 0
    caps = candidate.get("capabilities")
    if isinstance(caps, str):
        try:
            caps = json.loads(caps or "[]")
        except ValueError:
            caps = []
    if not isinstance(caps, list):
        return 0
    for cap in caps:
        cap = str(cap)
        if cap in ("all", "*", mode, f"{mode}:*") or cap.startswith(f"{mode}:"):
            return 1
    return 0


async def _open_task_load(agent_id) -> int:
    """Count of open guild tasks currently assigned to this agent. A lower
    load ranks higher. Registry lookups must never crash routing."""
    try:
        from .db import get_db
        async with get_db() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM guild_tasks WHERE claimed_by_id=? "
                "AND status NOT IN ('completed','cancelled','delivered')",
                (agent_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _rank_candidates(candidates: list, mode: Optional[str] = None, loads: Optional[dict] = None) -> list:
    """Sort by: match_count desc, mode match desc, load asc, reputation desc,
    availability asc."""
    loads = loads or {}
    return sorted(
        candidates,
        key=lambda c: (
            -c.get("_match_count", 0),
            -_mode_match(c, mode),
            loads.get(c.get("agent_id"), 0),
            -c.get("reputation", 0.0),
            _availability_rank(c.get("availability", "offline")),
        ),
    )


async def route_task(task_id: int, required_capabilities: list, mode: Optional[str] = None) -> list:
    """Return ranked candidate agents for a task.

    Queries the capability registry for agents that satisfy ALL
    required_capabilities, then ranks them. Returns list of agent records
    with a '_match_count' key added for transparency.

    Args:
        task_id: The task ID (used for logging / future persistence).
        required_capabilities: Capabilities the executing agent must have.
        mode: Optional mode tag (video/code/trade) for affinity scoring.

    Returns:
        Sorted list of candidate agent dicts (best match first).
    """
    candidates = await find_capable_agents(required_capabilities)
    loads = {}
    for c in candidates:
        loads[c.get("agent_id")] = await _open_task_load(c.get("agent_id"))
    ranked = _rank_candidates(candidates, mode=mode, loads=loads)
    logger.info(
        "task_router: task %s with caps %s mode %s matched %d candidate(s)",
        task_id,
        required_capabilities,
        mode,
        len(ranked),
    )
    return ranked


async def auto_assign(task_id: int, required_capabilities: list, mode: Optional[str] = None) -> Optional[dict]:
    """Return the single best agent for a task, or None if no match.

    Uses route_task() internally and returns the top-ranked candidate.
    The caller is responsible for actually assigning / notifying the agent.

    Args:
        task_id: The task ID.
        required_capabilities: Capabilities required.
        mode: Optional mode tag for affinity ranking (P4).

    Returns:
        Best-match agent dict, or None if no capable agents are available.
    """
    ranked = await route_task(task_id, required_capabilities, mode=mode)
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
