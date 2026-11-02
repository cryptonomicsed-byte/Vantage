"""Turn hash-chained receipts into memory-graph nodes.

The graph was frozen because nothing in the codebase ever inserted into
`agent_memory_nodes` -- the only reference was the read-side router. Every
action already writes a receipt, so the raw material was there; it just had no
converter.

Shape, chosen to stay bounded and readable rather than one node per receipt
(8.6k receipts would be an unreadable hairball):

    skill node      mem_skill_<action>              one per distinct action
    experience node mem_exp_<action>_<YYYY-MM-DD>   one per action per day
    edges           cluster_skills -contains-> skill
                    skill -produced-> experience

Strength grows with the count, so frequently-used capabilities stand out
instead of drowning in duplicates. Writes are idempotent (INSERT ... ON
CONFLICT), so re-running a backfill is safe.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

SKILL_CLUSTER = "cluster_skills"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", (text or "unknown").lower()).strip("_")[:40] or "unknown"


def _day_of(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _strength(count: int) -> float:
    """0.30 -> 0.95, log-scaled so 1 vs 8000 receipts is visible but not absurd."""
    return round(min(0.95, 0.30 + 0.10 * math.log10(max(1, count) + 1)), 3)


async def resolve_agent_id(db: aiosqlite.Connection, ref) -> Optional[str]:
    """receipts.agent_id holds either a numeric id or a display name."""
    s = str(ref)
    if s.isdigit():
        return s
    cur = await db.execute("SELECT id FROM agents WHERE name=? LIMIT 1", (s,))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def _upsert_node(db, node_id: str, agent_id: str, label: str, node_type: str,
                       category: str, strength: float, metadata: str) -> None:
    await db.execute(
        """INSERT INTO agent_memory_nodes
             (id, agent_id, label, node_type, category, strength, metadata, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
             strength = MAX(agent_memory_nodes.strength, excluded.strength),
             label    = excluded.label,
             updated_at = datetime('now')""",
        (node_id, agent_id, label, node_type, category, strength, metadata),
    )


async def _upsert_edge(db, source_id: str, target_id: str, edge_type: str, strength: float) -> None:
    await db.execute(
        """INSERT OR IGNORE INTO agent_memory_edges
             (source_id, target_id, edge_type, strength, created_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        (source_id, target_id, edge_type, strength),
    )


async def record_action(
    db: aiosqlite.Connection,
    agent_ref,
    action: str,
    when_epoch: float,
) -> bool:
    """Upsert the skill + experience nodes and their edges for one action."""
    agent_id = await resolve_agent_id(db, agent_ref)
    if agent_id is None:
        return False

    slug = _slug(action)
    day = _day_of(when_epoch)
    skill_id = f"mem_skill_{slug}"
    exp_id = f"mem_exp_{slug}_{day}"

    await db.execute(
        """INSERT INTO agent_memory_nodes
             (id, agent_id, label, node_type, category, strength, metadata, created_at, updated_at)
           VALUES (?, ?, ?, 'skill', 'capability', 0.30, ?, datetime('now'), datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
             strength = MIN(0.95, agent_memory_nodes.strength + 0.01),
             updated_at = datetime('now')""",
        (skill_id, agent_id, action, f'{{"source":"receipts","action":"{action}"}}'),
    )
    await db.execute(
        """INSERT INTO agent_memory_nodes
             (id, agent_id, label, node_type, category, strength, metadata, created_at, updated_at)
           VALUES (?, ?, ?, 'experience', 'activity', 0.30, ?, datetime('now'), datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
             strength = MIN(0.95, agent_memory_nodes.strength + 0.01),
             updated_at = datetime('now')""",
        (exp_id, agent_id, f"{action} · {day}",
         f'{{"source":"receipts","action":"{action}","day":"{day}"}}'),
    )

    for src, dst, etype, weight in (
        (SKILL_CLUSTER, skill_id, "contains", 0.85),
        (skill_id, exp_id, "produced", 0.70),
    ):
        await db.execute(
            """INSERT OR IGNORE INTO agent_memory_edges
                 (source_id, target_id, edge_type, strength, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (src, dst, etype, weight),
        )
    return True


async def backfill(db: aiosqlite.Connection, agent_ref, recompute_strength: bool = True) -> dict:
    """Rebuild an agent's receipt-derived nodes from scratch (idempotent)."""
    agent_id = await resolve_agent_id(db, agent_ref)
    if agent_id is None:
        return {"agent_id": None, "reason": "agent not found"}

    cur = await db.execute(
        "SELECT action, timestamp FROM receipts WHERE agent_id IN (?, ?)",
        (agent_id, str(agent_ref)),
    )
    rows = await cur.fetchall()

    pairs: dict[tuple[str, str], int] = {}
    actions: dict[str, int] = {}
    for action, ts in rows:
        a = action or "unknown"
        day = _day_of(ts if ts is not None else 0)
        pairs[(a, day)] = pairs.get((a, day), 0) + 1
        actions[a] = actions.get(a, 0) + 1

    for (action, day), n in pairs.items():
        slug = _slug(action)
        await _upsert_node(db, f"mem_skill_{slug}", agent_id, action, "skill", "capability",
                           _strength(actions[action]),
                           f'{{"source":"receipts","action":"{action}"}}')
        await _upsert_node(db, f"mem_exp_{slug}_{day}", agent_id, f"{action} · {day}",
                           "experience", "activity", _strength(n),
                           f'{{"source":"receipts","action":"{action}","day":"{day}"}}')
        await _upsert_edge(db, SKILL_CLUSTER, f"mem_skill_{slug}", "contains", 0.85)
        await _upsert_edge(db, f"mem_skill_{slug}", f"mem_exp_{slug}_{day}", "produced", 0.70)

    return {
        "agent_id": agent_id,
        "receipts": len(rows),
        "skills": len(actions),
        "experiences": len(pairs),
    }
