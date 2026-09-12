"""
Vantage / UCX provider registry.

Responsibility: Vantage acts as the bootstrap rendezvous for UCX providers.

  Provider Agent → POST /api/ucx/providers/register (capability advertisement)
  UCX Broker     → GET  /api/ucx/providers           (all active providers)
  UCX Broker     → GET  /api/ucx/providers/{id}      (single provider)

Vantage does NOT make compute decisions.  It is a registry, not a broker.
Route these handlers from Vantage's FastAPI router.

Dependency direction: Vantage speaks UCX's JSON schema but does not import
any Rust crates.  The schema is documented in ucx-protocol/src/capability.rs.
"""
from __future__ import annotations

import time
from typing import Any

import aiosqlite

# ---- DB helpers (reuse Vantage's existing get_db context manager) -----------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ucx_providers (
    provider_id   TEXT PRIMARY KEY,
    capability    TEXT NOT NULL,   -- JSON blob matching ProviderCapability
    registered_at REAL NOT NULL,
    last_seen_at  REAL NOT NULL
);
"""

STALE_AFTER_SECS = 120  # provider considered offline after 2 min without heartbeat


async def ensure_table(db: aiosqlite.Connection) -> None:
    await db.execute(CREATE_TABLE)
    await db.commit()


# ---- Registry operations -----------------------------------------------------

async def register_provider(db: aiosqlite.Connection, capability: dict[str, Any]) -> dict:
    provider_id = capability.get("provider_id")
    if not provider_id:
        return {"ok": False, "error": "capability.provider_id required"}

    now = time.time()
    import json
    cap_json = json.dumps(capability)

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
    return {"ok": True, "provider_id": provider_id}


async def list_providers(db: aiosqlite.Connection) -> list[dict]:
    """Return all non-stale providers."""
    import json
    cutoff = time.time() - STALE_AFTER_SECS
    async with db.execute(
        "SELECT capability FROM ucx_providers WHERE last_seen_at > ?",
        (cutoff,)
    ) as cur:
        rows = await cur.fetchall()
    return [json.loads(r[0]) for r in rows]


async def get_provider(db: aiosqlite.Connection, provider_id: str) -> dict | None:
    import json
    async with db.execute(
        "SELECT capability FROM ucx_providers WHERE provider_id = ?",
        (provider_id,)
    ) as cur:
        row = await cur.fetchone()
    return json.loads(row[0]) if row else None


async def provider_heartbeat(db: aiosqlite.Connection, provider_id: str) -> dict:
    await db.execute(
        "UPDATE ucx_providers SET last_seen_at = ? WHERE provider_id = ?",
        (time.time(), provider_id),
    )
    await db.commit()
    return {"ok": True}
