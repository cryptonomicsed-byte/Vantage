"""
Àṣẹ (ASE) Emission Clock — Vantage endpoint layer.

Canonical spec: OSOVM_CODEX.md §29
Architecture:   project_emission_governance.md

EMISSION_RATE = 1 Àṣẹ / 60 seconds (global clock)
Each tick (1 minute) mints 1 ASE and distributes it across 8 protocol pools.

Routes:
  POST /api/ase/emission/tick             — process one minute-tick (cron-safe, idempotent)
  GET  /api/ase/emission/stats            — total emitted, pool balances
  GET  /api/ase/emission/history          — recent emission receipts
  GET  /api/ase/emission/pools            — current pool balances

The tick endpoint is idempotent per minute (uses floor(unix/60) as emission_number).
Call from a cron job every 60 seconds.  Extra calls in the same minute are no-ops.

Every emission produces an EmissionReceipt stored in the DB.
Future: wire receipt to Zàngbétò for on-chain anchoring.
"""

import logging
import math
import time
import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ase", tags=["ase-emission"])

# ── Constants (from emission governance spec) ─────────────────────────────────

EMISSION_RATE_PER_TICK = 1          # 1 ASE per 60-second tick
TICK_SECONDS = 60

# 8 distribution pools — percentages must sum to 100.
# 2 pools are TBD; allocated to Reserve until specified.
POOL_WEIGHTS: dict[str, float] = {
    "SimulationPool": 0.30,  # 30% → Proof-of-Simulation contributors
    "ResearchPool":   0.15,  # 15% → research + open-source
    "GovernancePool": 0.15,  # 15% → Council + governance operations
    "ReservePool":    0.20,  # 20% → strategic reserve (includes 2 TBD pools)
    "GrantPool":      0.10,  # 10% → community grants
    "UBIPool":        0.10,  # 10% → sovereign wallet UBI distribution
}

assert abs(sum(POOL_WEIGHTS.values()) - 1.0) < 1e-9, "Pool weights must sum to 1.0"

# ── DB ────────────────────────────────────────────────────────────────────────

_TABLES_CREATED = False


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ase_emission_receipts (
            emission_id         TEXT PRIMARY KEY,
            emission_number     INTEGER NOT NULL UNIQUE,   -- floor(unix / TICK_SECONDS)
            timestamp           REAL NOT NULL,
            total_minted        INTEGER NOT NULL,          -- micro_ase (1 ASE = 1_000_000 micro)
            candidate_set_hash  TEXT NOT NULL,
            previous_hash       TEXT,
            pool_distribution   TEXT NOT NULL,             -- JSON
            zangbeto_anchor     TEXT,
            created_at          REAL NOT NULL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS ase_pool_balances (
            pool_name   TEXT PRIMARY KEY,
            micro_ase   INTEGER NOT NULL DEFAULT 0,
            last_update REAL NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ase_emission_num ON ase_emission_receipts (emission_number DESC)"
    )
    # Seed pool rows so balance queries always return something
    for pool in POOL_WEIGHTS:
        await db.execute(
            "INSERT OR IGNORE INTO ase_pool_balances (pool_name, micro_ase, last_update) VALUES (?, 0, ?)",
            (pool, time.time()),
        )
    await db.commit()
    _TABLES_CREATED = True


# ── Helpers ───────────────────────────────────────────────────────────────────

MICRO_ASE_PER_TICK = EMISSION_RATE_PER_TICK * 1_000_000  # 1 ASE = 1_000_000 micro_ase


def _pool_distribution(emission_number: int) -> dict[str, int]:
    """Deterministic micro_ase split across pools for this emission tick."""
    total = MICRO_ASE_PER_TICK
    dist: dict[str, int] = {}
    allocated = 0
    pools = list(POOL_WEIGHTS.items())
    for i, (pool, weight) in enumerate(pools):
        if i == len(pools) - 1:
            # Give remainder to last pool to avoid rounding drift
            dist[pool] = total - allocated
        else:
            amount = int(total * weight)
            dist[pool] = amount
            allocated += amount
    return dist


def _candidate_set_hash(emission_number: int) -> str:
    """Deterministic hash of the candidate set for this emission (placeholder — real
    implementation draws from SimulationPool leaderboard)."""
    import hashlib
    return hashlib.sha256(f"emission:{emission_number}".encode()).hexdigest()


async def _get_previous_hash(db: aiosqlite.Connection) -> str | None:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT emission_id FROM ase_emission_receipts ORDER BY emission_number DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return row["emission_id"] if row else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/emission/tick",
    summary="Process one ASE emission tick (60 seconds = 1 ASE minted)",
)
async def emission_tick(
    caller: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Idempotent per-minute tick.  Computes the emission_number as floor(now / 60)
    and skips if that number already exists.

    Production: call every 60 seconds from a systemd timer or cron job.
    """
    await _ensure_tables(db)

    now = time.time()
    emission_number = int(math.floor(now / TICK_SECONDS))

    # Idempotency: skip if this tick was already processed
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT emission_id FROM ase_emission_receipts WHERE emission_number = ?",
        (emission_number,),
    ) as cur:
        if await cur.fetchone():
            return {
                "skipped": True,
                "emission_number": emission_number,
                "reason": "tick already processed",
            }

    distribution = _pool_distribution(emission_number)
    candidate_hash = _candidate_set_hash(emission_number)
    previous_hash = await _get_previous_hash(db)
    emission_id = str(uuid.uuid4())

    # Insert receipt
    await db.execute(
        """INSERT INTO ase_emission_receipts
           (emission_id, emission_number, timestamp, total_minted, candidate_set_hash,
            previous_hash, pool_distribution, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            emission_id, emission_number, now, MICRO_ASE_PER_TICK,
            candidate_hash, previous_hash,
            str(distribution), now,
        ),
    )

    # Update pool balances
    for pool, amount in distribution.items():
        await db.execute(
            "UPDATE ase_pool_balances SET micro_ase = micro_ase + ?, last_update = ? WHERE pool_name = ?",
            (amount, now, pool),
        )

    await db.commit()

    logger.info(
        "ASE emission #%d: %d micro_ase minted, distribution=%s",
        emission_number, MICRO_ASE_PER_TICK, distribution,
    )

    return {
        "emission_id":      emission_id,
        "emission_number":  emission_number,
        "total_minted":     MICRO_ASE_PER_TICK,
        "ase_units":        EMISSION_RATE_PER_TICK,
        "distribution":     distribution,
        "previous_hash":    previous_hash,
        "timestamp":        now,
    }


@router.get(
    "/emission/stats",
    summary="Total ASE emitted and pool summaries",
)
async def emission_stats(
    caller: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row

    async with db.execute(
        "SELECT COUNT(*) AS ticks, SUM(total_minted) AS total FROM ase_emission_receipts"
    ) as cur:
        totals = dict(await cur.fetchone())

    async with db.execute(
        "SELECT pool_name, micro_ase FROM ase_pool_balances ORDER BY micro_ase DESC"
    ) as cur:
        pools = [dict(r) for r in await cur.fetchall()]

    return {
        "total_ticks":     totals["ticks"] or 0,
        "total_micro_ase": totals["total"] or 0,
        "total_ase":       (totals["total"] or 0) / 1_000_000,
        "pool_balances":   pools,
        "emission_rate":   f"{EMISSION_RATE_PER_TICK} ASE / {TICK_SECONDS}s",
    }


@router.get(
    "/emission/history",
    summary="Recent emission receipts",
)
async def emission_history(
    limit: int = 60,
    caller: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT * FROM ase_emission_receipts ORDER BY emission_number DESC LIMIT ?",
        (min(limit, 1440),),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return {"receipts": rows, "count": len(rows)}


@router.get(
    "/emission/pools",
    summary="Current pool balances",
)
async def pool_balances(
    caller: dict = Depends(get_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT pool_name, micro_ase, last_update FROM ase_pool_balances ORDER BY pool_name"
    ) as cur:
        pools = [dict(r) for r in await cur.fetchall()]
    return {
        "pools": [
            {
                **p,
                "ase_units": p["micro_ase"] / 1_000_000,
                "pool_weight": POOL_WEIGHTS.get(p["pool_name"], 0.0),
            }
            for p in pools
        ]
    }
