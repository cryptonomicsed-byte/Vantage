"""
UCX Bootstrap Rendezvous — Vantage as provider registry.

Vantage is the discovery layer, NOT the broker.
It stores capability advertisements and serves them to the UCX broker on demand.

Routes:
  POST /api/ucx/providers/register     — Provider Agent announces itself
  POST /api/ucx/providers/{id}/heartbeat — Provider keeps its registration alive
  GET  /api/ucx/providers              — UCX Broker lists active providers
  GET  /api/ucx/providers/{id}         — UCX Broker fetches a single provider

Auth: same X-Agent-Key as the rest of Vantage.
"""

import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from ..db import DB_PATH, get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ucx", tags=["ucx"])

# ---- DB bootstrap -----------------------------------------------------------

_TABLE_CREATED = False

async def _ensure_table(db: aiosqlite.Connection) -> None:
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    await db.execute("""
        CREATE TABLE IF NOT EXISTS ucx_providers (
            provider_id   TEXT PRIMARY KEY,
            capability    TEXT NOT NULL,
            registered_at REAL NOT NULL,
            last_seen_at  REAL NOT NULL
        )
    """)
    await db.commit()
    _TABLE_CREATED = True


# ---- Helpers ----------------------------------------------------------------

import json, time

_STALE_SECS = 120   # provider offline after 2 min without heartbeat


# ---- Routes -----------------------------------------------------------------

@router.post("/providers/register")
async def register_provider(
    body: dict[str, Any],
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Provider Agent announces its ProviderCapability."""
    await _ensure_table(db)
    provider_id = body.get("provider_id")
    if not provider_id:
        raise HTTPException(400, "capability.provider_id required")

    now = time.time()
    cap_json = json.dumps(body)
    await db.execute(
        """
        INSERT INTO ucx_providers (provider_id, capability, registered_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(provider_id) DO UPDATE SET
            capability   = excluded.capability,
            last_seen_at = excluded.last_seen_at
        """,
        (provider_id, cap_json, now, now),
    )
    await db.commit()
    logger.info("ucx: provider registered provider_id=%s agent=%s", provider_id, agent.get("name"))
    return {"ok": True, "provider_id": provider_id}


@router.post("/providers/{provider_id}/heartbeat")
async def provider_heartbeat(
    provider_id: str,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Keep a provider registration alive.  Must be called every <2 min."""
    await _ensure_table(db)
    await db.execute(
        "UPDATE ucx_providers SET last_seen_at = ? WHERE provider_id = ?",
        (time.time(), provider_id),
    )
    await db.commit()
    return {"ok": True, "provider_id": provider_id}


@router.get("/providers")
async def list_providers(
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return all non-stale ProviderCapability objects."""
    await _ensure_table(db)
    cutoff = time.time() - _STALE_SECS
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT capability FROM ucx_providers WHERE last_seen_at > ? ORDER BY last_seen_at DESC",
        (cutoff,)
    ) as cur:
        rows = await cur.fetchall()
    return {"providers": [json.loads(r["capability"]) for r in rows]}


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: str,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Fetch a single provider by ID (stale or active)."""
    await _ensure_table(db)
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT capability, last_seen_at FROM ucx_providers WHERE provider_id = ?",
        (provider_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"provider {provider_id!r} not found")
    cap = json.loads(row["capability"])
    stale = (time.time() - row["last_seen_at"]) > _STALE_SECS
    return {"provider": cap, "stale": stale}


@router.delete("/providers/{provider_id}")
async def deregister_provider(
    provider_id: str,
    agent: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Provider Agent voluntarily removes itself from the registry."""
    await _ensure_table(db)
    await db.execute("DELETE FROM ucx_providers WHERE provider_id = ?", (provider_id,))
    await db.commit()
    return {"ok": True, "provider_id": provider_id}
