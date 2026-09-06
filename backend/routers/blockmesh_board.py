"""BlockMesh unified job board — aggregates guild_tasks, task_listings, and jobs/job_tasks.

GET /api/blockmesh/board returns a merged, paginated feed of all available
work items across the three task surfaces that actually exist in the DB:

  - guild_tasks       (tasks_db.py — guild-scoped, proposed/claimed/etc.)
  - task_listings     (db.py init_agents_db — the flat Task Market / Gigs page)
  - job_tasks         (db.py — sub-tasks of multi-agent Jobs, status=open)

Each item is normalised to a common BoardItem shape with an id in the format
"source_type:source_id" so callers can route back to the source API.
"""
import json
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Query

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/blockmesh/board", tags=["mesh"])


# ── Schema migration (idempotent ALTER TABLE) ───────────────────────────────

async def init_blockmesh_board_db() -> None:
    """Add BlockMesh-board columns to the three task tables that power this feed.

    SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so we
    attempt each statement and swallow the OperationalError that fires when
    the column already exists.  This function is safe to call on every boot.
    """
    async with get_db() as db:
        for stmt in [
            # guild_tasks
            "ALTER TABLE guild_tasks ADD COLUMN required_tier INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE guild_tasks ADD COLUMN reward_usdc REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE guild_tasks ADD COLUMN skill_tags TEXT NOT NULL DEFAULT '[]'",
            # task_listings
            "ALTER TABLE task_listings ADD COLUMN required_tier INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE task_listings ADD COLUMN skill_tags TEXT NOT NULL DEFAULT '[]'",
            # job_tasks
            "ALTER TABLE job_tasks ADD COLUMN required_tier INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE job_tasks ADD COLUMN reward_usdc REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE job_tasks ADD COLUMN skill_tags TEXT NOT NULL DEFAULT '[]'",
        ]:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists — harmless
        await db.commit()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_skill_tags(raw: object) -> list[str]:
    """Parse skill_tags from whatever shape it arrived in."""
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t) for t in parsed]
        except Exception:
            pass
    return []


def _ts(value: object) -> Optional[float]:
    """Convert a SQLite datetime string to a unix timestamp (float) or None."""
    if not value:
        return None
    import datetime as _dt
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return _dt.datetime.strptime(str(value), fmt).replace(
                tzinfo=_dt.timezone.utc
            ).timestamp()
        except ValueError:
            continue
    return None


# ── Normalizers ─────────────────────────────────────────────────────────────

def _normalize_guild_task(row: dict) -> dict:
    return {
        "id": f"task:{row['id']}",
        "type": "guild_task",
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "required_tier": int(row.get("required_tier") or 0),
        "reward_usdc": float(row.get("reward_usdc") or 0.0),
        "skill_tags": _parse_skill_tags(row.get("skill_tags", "[]")),
        "status": row.get("status", ""),
        "created_at": _ts(row.get("created_at")),
        "guild_id": row.get("guild_id"),
        "guild_slug": row.get("guild_slug", ""),
        "posted_by_agent_id": row.get("created_by_id"),
    }


def _normalize_task_listing(row: dict) -> dict:
    return {
        "id": f"listing:{row['id']}",
        "type": "task_listing",
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "required_tier": int(row.get("required_tier") or 0),
        "reward_usdc": float(row.get("reward_usdc") or 0.0),
        "skill_tags": _parse_skill_tags(row.get("skill_tags", "[]")),
        "status": row.get("status", ""),
        "created_at": _ts(row.get("created_at")),
        "guild_id": None,
        "guild_slug": None,
        "posted_by_agent_id": row.get("poster_id"),
    }


def _normalize_job_task(row: dict) -> dict:
    return {
        "id": f"job_task:{row['id']}",
        "type": "job",
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "required_tier": int(row.get("required_tier") or 0),
        "reward_usdc": float(row.get("reward_usdc") or 0.0),
        "skill_tags": _parse_skill_tags(row.get("skill_tags", "[]")),
        "status": row.get("status", ""),
        "created_at": _ts(row.get("created_at")),
        "guild_id": None,
        "guild_slug": row.get("guild_slug"),
        "posted_by_agent_id": row.get("poster_id"),
    }


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="Unified BlockMesh job board",
    description=(
        "Aggregate feed of all open work across guild_tasks, task_listings, and job_tasks. "
        "Filter by tier_min (0–4) or type ('guild_task' | 'task_listing' | 'job'). "
        "Returns items sorted by created_at descending."
    ),
)
async def list_board(
    tier_min: int = Query(0, ge=0, le=4, description="Minimum required_tier (0=any)"),
    type: Optional[str] = Query(None, description="Filter: guild_task | task_listing | job"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent: dict = Depends(get_agent),
):
    items: list[dict] = []

    want_guild_task = type is None or type == "guild_task"
    want_task_listing = type is None or type == "task_listing"
    want_job = type is None or type == "job"

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        if want_guild_task:
            async with db.execute(
                """SELECT gt.*, g.slug AS guild_slug_join
                   FROM guild_tasks gt
                   LEFT JOIN guilds g ON g.id = gt.guild_id
                   WHERE gt.status IN ('proposed', 'open')
                     AND gt.required_tier >= ?
                   ORDER BY gt.created_at DESC""",
                (tier_min,),
            ) as cur:
                rows = await cur.fetchall()
            for r in rows:
                d = dict(r)
                # guild_slug is stored directly on guild_tasks; fall back to join
                if not d.get("guild_slug"):
                    d["guild_slug"] = d.get("guild_slug_join", "")
                items.append(_normalize_guild_task(d))

        if want_task_listing:
            async with db.execute(
                """SELECT * FROM task_listings
                   WHERE status = 'open'
                     AND required_tier >= ?
                   ORDER BY created_at DESC""",
                (tier_min,),
            ) as cur:
                rows = await cur.fetchall()
            for r in rows:
                items.append(_normalize_task_listing(dict(r)))

        if want_job:
            # Pull open job_tasks joined with their parent job for guild_slug / poster_id
            async with db.execute(
                """SELECT jt.*, j.guild_slug, j.poster_id
                   FROM job_tasks jt
                   JOIN jobs j ON j.id = jt.job_id
                   WHERE jt.status = 'open'
                     AND jt.required_tier >= ?
                   ORDER BY jt.created_at DESC""",
                (tier_min,),
            ) as cur:
                rows = await cur.fetchall()
            for r in rows:
                items.append(_normalize_job_task(dict(r)))

    # Sort unified feed by created_at descending (None sorts last)
    items.sort(key=lambda x: x["created_at"] or 0.0, reverse=True)

    total = len(items)
    page = items[offset: offset + limit]

    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
    }
