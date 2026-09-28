"""waggle_client.py — Thin async client for the agentic-waggle stigmergic substrate.

Waggle is a zero-dependency pheromone field: agents deposit decaying signals on
resources so the swarm can discover hot tasks without a central scheduler.

Environment:
    WAGGLE_URL  Base URL of the waggle server (default "http://localhost:7778").
                Set in .env.example; leave empty to silently skip waggle calls.

Typical call sites (fire-and-forget):
    asyncio.create_task(emit_signal(task_id, "task"))
    # inside task-create endpoint — does not block the response path.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WAGGLE_URL: str = os.environ.get("WAGGLE_URL", "http://localhost:7778").rstrip("/")


def _enabled() -> bool:
    """Return True if a waggle endpoint has been configured."""
    return bool(WAGGLE_URL)


# ── write side ────────────────────────────────────────────────────────────────

async def emit_signal(
    task_id: str,
    task_type: str,
    intensity: float = 1.0,
    agent_id: str = "vantage",
    note: str = "",
) -> None:
    """Deposit a decaying signal on the task resource.

    Maps Vantage task concepts onto waggle primitives:
        resource  →  "task://<task_id>"
        kind      →  task_type  (e.g. "claimed", "completed", "help", "gold")
        agent     →  agent_id   (Vantage node identity)
        intensity →  caller-supplied weight (0–10, default 1.0)

    The call is intentionally fire-and-forget: if waggle is unreachable the
    task lifecycle continues unaffected.
    """
    if not _enabled():
        return
    resource = f"task://{task_id}"
    body: dict[str, Any] = {
        "agent": agent_id,
        "resource": resource,
        "kind": task_type,
        "intensity": intensity,
    }
    if note:
        body["note"] = note
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                f"{WAGGLE_URL}/v1/signals",
                json=body,
                timeout=2.0,
            )
            if resp.status_code >= 400:
                logger.debug("waggle emit_signal %s → HTTP %s", task_id, resp.status_code)
    except Exception as exc:  # network errors are non-fatal
        logger.debug("waggle emit_signal error (non-fatal): %s", exc)


async def get_task_signals(
    prefix: str = "task://",
    kind: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Sniff the waggle field for active task signals.

    Returns a list of Signal dicts sorted by effective intensity (strongest
    first).  Returns [] on any error so callers can treat waggle as optional.
    """
    if not _enabled():
        return []
    params: dict[str, Any] = {"prefix": prefix, "limit": limit}
    if kind:
        params["kind"] = kind
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{WAGGLE_URL}/v1/sniff", params=params, timeout=3.0)
        if resp.status_code == 200:
            return resp.json().get("signals", [])
        logger.debug("waggle get_task_signals → HTTP %s", resp.status_code)
    except Exception as exc:
        logger.debug("waggle get_task_signals error (non-fatal): %s", exc)
    return []


async def register_watch(
    agent_id: str,
    prefix: str = "task://",
    kind_map: dict[str, str] | None = None,
) -> dict:
    """Register a waggle watch so outcome events are ingested as signals.

    Returns the Watch dict (contains ingest_path) or {} on error.
    """
    if not _enabled():
        return {}
    body: dict[str, Any] = {
        "agent": agent_id,
        "prefix": prefix,
    }
    if kind_map:
        body["kind_map"] = kind_map
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(f"{WAGGLE_URL}/v1/watches", json=body, timeout=3.0)
        if resp.status_code == 200:
            return resp.json()
        logger.debug("waggle register_watch → HTTP %s", resp.status_code)
    except Exception as exc:
        logger.debug("waggle register_watch error (non-fatal): %s", exc)
    return {}


async def ingest_outcome(watch_id: str, task_id: str, success: bool) -> dict:
    """Report task success/failure via the waggle ingest path.

    Maps to POST /v1/ingest/{watch_id} with a WatchEvent body.
    """
    if not _enabled():
        return {}
    body: dict[str, Any] = {
        "resource": f"task://{task_id}",
        "outcome": "success" if success else "failure",
    }
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                f"{WAGGLE_URL}/v1/ingest/{watch_id}",
                json=body,
                timeout=2.0,
            )
        if resp.status_code == 200:
            return resp.json()
        logger.debug("waggle ingest_outcome %s → HTTP %s", task_id, resp.status_code)
    except Exception as exc:
        logger.debug("waggle ingest_outcome error (non-fatal): %s", exc)
    return {}
