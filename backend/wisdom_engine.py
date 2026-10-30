"""Clojure wisdom engine client — policy evaluation, Hermetic scoring, mode routing."""
import os
import httpx

WISDOM_URL = os.environ.get("WISDOM_ENGINE_URL", "http://localhost:7704")
_policy_cache: dict = {}


async def evaluate_policy(action: str, capabilities: list, required: list) -> dict:
    """Ask wisdom engine if agent capabilities satisfy required caps for action."""
    cache_key = f"{action}:{sorted(capabilities)}:{sorted(required)}"
    if cache_key in _policy_cache:
        return _policy_cache[cache_key]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(
                f"{WISDOM_URL}/policy",
                json={"action": action, "capabilities": capabilities, "required_capabilities": required},
            )
            result = r.json()
            _policy_cache[cache_key] = result
            return result
    except Exception as e:
        return {"ok": False, "allowed": True, "error": str(e)}  # fail-open


async def hermetic_score(text: str) -> dict:
    """Score text against the 7 Hermetic principles."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(f"{WISDOM_URL}/hermetic", json={"text": text})
            return r.json()
    except Exception:
        return {"ok": False, "alignment": 0.0}


async def wisdom_route(context: str, available_modes: list = None) -> dict:
    """Given a task context, recommend the best agent mode."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(
                f"{WISDOM_URL}/route",
                json={"context": context, "available_modes": available_modes or ["code", "trade", "video", "mixed"]},
            )
            return r.json()
    except Exception:
        return {"ok": False, "recommended_mode": "mixed"}


async def mode_route(context: str, agent_capabilities: list, available_modes: list = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(f"{WISDOM_URL}/mode-route",
                json={"context": context, "agent_capabilities": agent_capabilities,
                      "available_modes": available_modes or ["code","trade","video","mixed"]})
            return r.json()
    except Exception:
        return {"ok": False, "recommended_mode": "mixed"}

async def filter_capabilities(requirements: list, candidates: list) -> list:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(f"{WISDOM_URL}/capability/filter",
                json={"requirements": requirements, "candidates": candidates})
            return r.json().get("ranked", candidates)
    except Exception:
        return candidates
