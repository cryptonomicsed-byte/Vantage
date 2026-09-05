"""Agent capability registry.

Agents register what they can do:
  {
    "agent_id": 5,
    "agent_name": "Hermes-Ares",
    "runtime": "omokoda2",
    "capabilities": ["research", "code.execute", "nostr.publish", "git.commit"],
    "tools": ["web_search", "code_runner", "git"],
    "availability": "available",
    "trust_level": 3,
    "reputation": 0.85,
  }

Stored in SQLite (capability_registry table).
Queried for task routing.
"""
import json
import logging
from typing import Optional

import aiosqlite

from .db import DB_PATH

logger = logging.getLogger(__name__)

VALID_AVAILABILITY = {"available", "working", "thinking", "blocked", "offline"}


async def _ensure_registry_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS capability_registry (
                agent_id INTEGER PRIMARY KEY,
                agent_name TEXT,
                runtime TEXT NOT NULL DEFAULT 'vantage-derived',
                capabilities TEXT NOT NULL DEFAULT '[]',
                tools TEXT NOT NULL DEFAULT '[]',
                availability TEXT NOT NULL DEFAULT 'offline',
                trust_level INTEGER NOT NULL DEFAULT 1,
                reputation REAL NOT NULL DEFAULT 0.5,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_capability_registry_availability "
            "ON capability_registry(availability)"
        )
        await db.commit()


async def register_capabilities(
    agent_id: int,
    capabilities: list,
    tools: list,
    runtime: str = "vantage-derived",
    agent_name: Optional[str] = None,
) -> None:
    """Register or update an agent's capabilities."""
    await _ensure_registry_table()
    # Fetch agent_name from agents table if not provided
    if agent_name is None:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=10000")
                db.row_factory = aiosqlite.Row
                row = await (await db.execute(
                    "SELECT name FROM agents WHERE id = ?", (agent_id,)
                )).fetchone()
                if row:
                    agent_name = row["name"]
        except Exception as exc:
            logger.debug("capability_registry: could not fetch agent name: %s", exc)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        await db.execute(
            """INSERT INTO capability_registry
               (agent_id, agent_name, runtime, capabilities, tools, availability, updated_at)
               VALUES (?, ?, ?, ?, ?, 'available', datetime('now'))
               ON CONFLICT(agent_id) DO UPDATE SET
                 agent_name = excluded.agent_name,
                 runtime = excluded.runtime,
                 capabilities = excluded.capabilities,
                 tools = excluded.tools,
                 availability = CASE
                     WHEN availability = 'offline' THEN 'available'
                     ELSE availability
                 END,
                 updated_at = datetime('now')""",
            (
                agent_id,
                agent_name,
                runtime,
                json.dumps(capabilities),
                json.dumps(tools),
            ),
        )
        await db.commit()
    logger.info("capability_registry: agent %s registered %d capabilities", agent_id, len(capabilities))


async def get_agent_capabilities(agent_id: int) -> dict:
    """Return capability record for an agent, or empty dict if not registered."""
    await _ensure_registry_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM capability_registry WHERE agent_id = ?", (agent_id,)
        )).fetchone()
    if not row:
        return {}
    result = dict(row)
    result["capabilities"] = json.loads(result.get("capabilities") or "[]")
    result["tools"] = json.loads(result.get("tools") or "[]")
    return result


async def find_capable_agents(
    required_capabilities: list,
    availability: Optional[str] = None,
) -> list:
    """Find agents that have ALL required capabilities.

    Returns records sorted by: capability match count desc, reputation desc.
    If availability is specified, filters to that state only.
    If availability is None, excludes 'offline' agents.
    """
    await _ensure_registry_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        db.row_factory = aiosqlite.Row
        if availability:
            rows = await (await db.execute(
                "SELECT * FROM capability_registry WHERE availability = ?", (availability,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM capability_registry WHERE availability != 'offline'"
            )).fetchall()

    results = []
    for row in rows:
        record = dict(row)
        agent_caps = set(json.loads(record.get("capabilities") or "[]"))
        required_set = set(required_capabilities)
        match_count = len(required_set & agent_caps)
        # Must have ALL required capabilities
        if required_set and match_count < len(required_set):
            continue
        record["capabilities"] = list(agent_caps)
        record["tools"] = json.loads(record.get("tools") or "[]")
        record["_match_count"] = match_count
        results.append(record)

    return results


async def update_availability(agent_id: int, availability: str) -> None:
    """Update an agent's availability state.

    availability: available | working | thinking | blocked | offline
    """
    if availability not in VALID_AVAILABILITY:
        raise ValueError(f"Invalid availability '{availability}'. Must be one of: {VALID_AVAILABILITY}")
    await _ensure_registry_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        await db.execute(
            """INSERT INTO capability_registry (agent_id, availability, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(agent_id) DO UPDATE SET
                 availability = excluded.availability,
                 updated_at = datetime('now')""",
            (agent_id, availability),
        )
        await db.commit()


async def get_all_capabilities() -> list:
    """Return the full capability directory — all registered agents."""
    await _ensure_registry_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=10000")
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM capability_registry ORDER BY reputation DESC, agent_name ASC"
        )).fetchall()
    results = []
    for row in rows:
        record = dict(row)
        record["capabilities"] = json.loads(record.get("capabilities") or "[]")
        record["tools"] = json.loads(record.get("tools") or "[]")
        results.append(record)
    return results
