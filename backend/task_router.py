"""Task router — matches tasks to capable agents.

Given a task's required_capabilities list, returns ranked candidate agents.
Ranking: 1) capability match count, 2) reputation, 3) availability
         (available > thinking > working > blocked > offline).
"""
import logging
import os
from typing import Optional

from .capability_registry import find_capable_agents
from .wisdom_engine import wisdom_route as _wisdom_route, mode_route as _mode_route

logger = logging.getLogger(__name__)

# Minimum Clojure wisdom confidence to use as primary routing decision (else fall through)
_WISDOM_CONFIDENCE_THRESHOLD = float(
    os.environ.get("WISDOM_CONFIDENCE_THRESHOLD", "0.5")
)

# Observe hermetic scores in the routing log; reorder only when explicitly primed.
HERMETIC_OBSERVE = os.environ.get("HERMETIC_OBSERVE", "1") == "1"
WISDOM_PRIMARY = os.environ.get("WISDOM_PRIMARY", "0") == "1"

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

    # PRIMARY: Clojure wisdom engine as routing decision-maker
    # mode_route returns {recommended_mode, confidence, reasoning, ok}
    # If confidence > threshold, use wisdom result directly as the ranked list.
    # Fall through to local Python capability scoring when wisdom is unavailable.
    if candidates:
        try:
            agent_ids = [str(a.get("agent_id") or a.get("id", "")) for a in candidates]
            # Context must carry task MEANING, not just an id. Built from the
            # id alone the engine scored ~0.45 on every call -- permanently
            # below the 0.5 threshold, so its vote could never win.
            _tt, _td = "", ""
            try:
                import sqlite3 as _sq
                _dbp = os.environ.get("VANTAGE_DATA_DIR", "/opt/ares/Vantage/data") + "/vantage.db"
                _c = _sq.connect(f"file:{_dbp}?mode=ro", uri=True)
                _row = _c.execute("SELECT title, description FROM jobs WHERE id=?", (task_id,)).fetchone()
                _c.close()
                if _row:
                    _tt, _td = (_row[0] or ""), (_row[1] or "")
            except Exception:
                pass
            # Task TEXT only. Concatenating the capability list here let the
            # engine keyword-match on the caps themselves (a "Regression job"
            # routed as mode=trade because "trade" appeared in the cap list).
            # Caps travel in the agent_capabilities parameter, where they belong.
            context = f"{_tt} {_td}".strip() or f"task {task_id}"
            # CONTRACT REPAIR: capabilities and modes are different axes. The old
            # call passed agent ids as BOTH, so the engine keyword-matched
            # identifiers. Send what each parameter actually means.
            caps = sorted({str(c) for c in (required_capabilities or [])})
            modes = caps or ["code", "trade", "video", "mixed"]
            wisdom = await _mode_route(context, caps, modes)
            confidence = float(wisdom.get("confidence", 0.0))
            recommended = wisdom.get("recommended_mode") or wisdom.get("mode")
            logger.info(
                "task_router: wisdom decision for task %s -> mode=%s confidence=%.2f reasoning=%s",
                task_id, recommended, confidence, wisdom.get("reasoning", ""),
            )

            # OBSERVE-ONLY hermetic scoring. Computes and logs; reorders nothing
            # unless WISDOM_PRIMARY=1. Failure here must never break routing.
            if HERMETIC_OBSERVE:
                try:
                    from .mood.hermetic_scorer import score_agent
                    _dbp = os.environ.get("VANTAGE_DATA_DIR", "/opt/ares/Vantage/data") + "/vantage.db"
                    scores = []
                    for a in candidates[:8]:
                        _aid = a.get("agent_id") or a.get("id")
                        try:
                            r = score_agent(
                                _dbp, _aid,
                                intent=required_capabilities,
                                capability=(a.get("capabilities") or caps),
                                action_type=(recommended or None),
                                mood=None,
                                koodu_day=None,
                            )
                            scores.append((str(_aid), r["alignment"], r["coverage"]))
                        except Exception:
                            continue
                    logger.info("task_router[hermetic-observe] task=%s %s",
                                task_id, scores)
                    if WISDOM_PRIMARY and recommended:
                        # mode selects the eligible set; hermetic alignment orders it.
                        sel = [s for s in scores if s[1] is not None]
                        if sel:
                            best = max(sel, key=lambda s: (s[1] * s[2],))
                            ranked.sort(key=lambda a: (str(a.get("agent_id") or a.get("id")) != best[0],
                                                       -next((s[1] for s in sel if s[0] == str(a.get("agent_id") or a.get("id"))), 0.0)))
                            logger.info("task_router[hermetic-primary] task=%s chose=%s score=%.3f cov=%.2f",
                                        task_id, best[0], best[1], best[2])
                except Exception as _he:
                    logger.debug("task_router: hermetic observe failed: %s", _he)

            if recommended and confidence >= _WISDOM_CONFIDENCE_THRESHOLD:
                # `recommended` is a MODE, not an agent id. Compare it against
                # candidate capabilities instead of identity (the old comparison
                # could never match and `ranked` came back unchanged).
                def _has_mode(a):
                    caps_a = {str(c).lower() for c in (a.get("capabilities") or [])}
                    return recommended.lower() in caps_a
                primary = [a for a in ranked if _has_mode(a)]
                rest = [a for a in ranked if not _has_mode(a)]
                if primary:
                    ranked = primary + rest
                    logger.info(
                        "task_router: wisdom primary routing accepted (confidence=%.2f, mode=%s, matched=%d)",
                        confidence, recommended, len(primary)
                    )
        except Exception as _we:
            logger.debug("task_router: wisdom engine unavailable, using local ranking: %s", _we)

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
