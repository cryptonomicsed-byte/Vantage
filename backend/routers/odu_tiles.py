"""
256 Odù Tile Eco — Zelda-style 16×16 world grid mapped to Ifá Odù.

Each of the 256 tiles corresponds to one of the 256 Odù of IfáScript and is
simultaneously:
  - Epistemic space (claims indexed by Odù)
  - Economic space  (Àṣẹ mining/rent/sell flows)
  - Physical space  (Gaussian-splat location anchored to real geography)
  - Temporal space  (Koodu resonance cycle)

Tile economy (Spatial Twin as Asset):
  MINE   → capture delta + quality → OSOVM F1 gate → Àṣẹ reward
  SIMULATE → verified ScarabSwarm sim runs → proof chain
  RENT   → other agents pay Àṣẹ to use the twin environment
  SELL   → kind:31030 creation receipt = sovereign IP asset on Sui

Routes:
  GET  /api/odu/tiles                    — 16×16 grid overview
  GET  /api/odu/tiles/{tile_id}          — single tile detail
  POST /api/odu/tiles/{tile_id}/claim    — agent stakes / claims a tile
  POST /api/odu/tiles/{tile_id}/release  — release claim
  POST /api/odu/tiles/{tile_id}/mine     — record a capture receipt (31020) → reward
  POST /api/odu/tiles/{tile_id}/rent     — pay Àṣẹ to use environment
  GET  /api/odu/tiles/{tile_id}/receipts — twin receipts anchored to this tile
  GET  /api/odu/daily                    — today's active Odù (CowrieOracle picks daily tile)
  GET  /api/odu/stats                    — aggregate eco stats
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/odu", tags=["odu-tiles"])

# ── Constants ─────────────────────────────────────────────────────────────────

GRID_SIZE  = 16        # 16 × 16 = 256 tiles
TOTAL_TILES = 256      # exactly 256 Odù

# Mining reward per quality unit (Àṣẹ micro-units)
MINE_BASE_REWARD = 100   # base reward for a valid mine event

# Tile claim requires T2+ (can be overridden by env)
MIN_TIER_TO_CLAIM = 2

# The 256 canonical Odù names (abbreviated; full names in BIPON39 wordlist)
_ODU_NAMES = [
    "Ogbe", "Oyeku", "Iwori", "Odi", "Irosun", "Owonrin", "Obara", "Okanran",
    "Ogunda", "Osa", "Ika", "Oturupon", "Otura", "Irete", "Ose", "Ofu",
    # The remaining 240 follow the same 16-base × 16-secondary pattern
    # (abbreviated here; runtime uses index arithmetic for full name composition)
]

_BASE_ODU = [
    "Ogbe", "Oyeku", "Iwori", "Odi", "Irosun", "Owonrin", "Obara", "Okanran",
    "Ogunda", "Osa", "Ika", "Oturupon", "Otura", "Irete", "Ose", "Ofu",
]


def _odu_name(tile_id: int) -> str:
    """Return canonical Odù name for tile_id 1-256."""
    idx = tile_id - 1
    primary   = _BASE_ODU[idx // 16]
    secondary = _BASE_ODU[idx %  16]
    if primary == secondary:
        return primary          # pure sign (e.g., Ogbe-Meji)
    return f"{primary}-{secondary}"


def _odu_bipon39(tile_id: int) -> str:
    """BIPON39 mnemonic word for this tile (1:1 mapping to BIPON39 256-word list)."""
    # Deterministic from tile_id — actual words come from BIPON39 crate
    # Use hex encoding as stand-in until BIPON39 is linked
    return f"bipon39-{tile_id:03d}"


def _koodu_facet(tile_id: int, day_seed: int) -> int:
    """Koodu 49-facet resonance for this tile on a given day seed (0-48)."""
    return ((tile_id - 1 + day_seed) % 49) + 1


def _day_seed() -> int:
    """Day-of-year modulo 49 (Koodu cycle)."""
    return datetime.now(timezone.utc).timetuple().tm_yday % 49


def _cowrie_oracle_today() -> int:
    """CowrieOracle: deterministically pick today's active tile (1-256)."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    h = hashlib.sha256(today_str.encode()).digest()
    return (int.from_bytes(h[:2], "big") % 256) + 1


# ── Schema ─────────────────────────────────────────────────────────────────────

_TABLES_CREATED = False


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS odu_tiles (
            tile_id          INTEGER PRIMARY KEY,   -- 1 … 256
            odu_name         TEXT NOT NULL,
            grid_x           INTEGER NOT NULL,      -- 0-15
            grid_y           INTEGER NOT NULL,      -- 0-15
            holder_agent_id  INTEGER,
            claimed_at       REAL,
            scene_hash       TEXT,                  -- latest splat scene hash
            twin_receipt_id  TEXT,                  -- latest kind:31030 receipt
            capture_count    INTEGER NOT NULL DEFAULT 0,
            rent_income_ase  REAL NOT NULL DEFAULT 0,
            total_mined_ase  REAL NOT NULL DEFAULT 0,
            lat              REAL,
            lon              REAL,
            locked           INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_ot_holder ON odu_tiles (holder_agent_id);
        CREATE INDEX IF NOT EXISTS idx_ot_grid   ON odu_tiles (grid_x, grid_y);

        CREATE TABLE IF NOT EXISTS odu_mine_events (
            event_id         TEXT PRIMARY KEY,
            tile_id          INTEGER NOT NULL REFERENCES odu_tiles(tile_id),
            agent_id         INTEGER NOT NULL,
            receipt_id       TEXT,          -- kind:31020 CaptureReceipt
            f1_score         REAL,
            reward_ase       REAL NOT NULL,
            mined_at         REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ome_tile ON odu_mine_events (tile_id, mined_at DESC);

        CREATE TABLE IF NOT EXISTS odu_rent_events (
            event_id         TEXT PRIMARY KEY,
            tile_id          INTEGER NOT NULL REFERENCES odu_tiles(tile_id),
            renter_agent_id  INTEGER NOT NULL,
            owner_agent_id   INTEGER,
            amount_ase       REAL NOT NULL,
            duration_secs    INTEGER NOT NULL,
            expires_at       REAL NOT NULL,
            rented_at        REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ore_tile ON odu_rent_events (tile_id, expires_at DESC);
    """)
    await db.commit()
    _TABLES_CREATED = True


async def _seed_tiles_if_empty(db: aiosqlite.Connection) -> None:
    cur = await db.execute("SELECT COUNT(*) n FROM odu_tiles")
    row = await cur.fetchone()
    if row and row[0] == 0:
        tiles = []
        for i in range(1, TOTAL_TILES + 1):
            x = (i - 1) % GRID_SIZE
            y = (i - 1) // GRID_SIZE
            tiles.append((i, _odu_name(i), x, y))
        await db.executemany(
            "INSERT OR IGNORE INTO odu_tiles (tile_id, odu_name, grid_x, grid_y) VALUES (?,?,?,?)",
            tiles,
        )
        await db.commit()


# ── Request models ─────────────────────────────────────────────────────────────

class ClaimBody(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None


class MineBody(BaseModel):
    receipt_id: Optional[str] = None    # kind:31020 CaptureReceipt from twin_receipt_index
    f1_score:   Optional[float] = None  # quality score (0-1); gate = 0.777
    scene_hash: Optional[str]  = None   # SHA-256 of the captured data


class RentBody(BaseModel):
    duration_secs: int = 3600    # how long to rent (default: 1 hour)
    amount_ase:    float = 1.0   # Àṣẹ payment


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tiles", summary="16×16 Odù tile grid overview")
async def list_tiles(
    claimed: Optional[bool] = None,
    limit:   int = Query(256, le=256),
    offset:  int = 0,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)
    db.row_factory = aiosqlite.Row

    today_tile = _cowrie_oracle_today()
    day_seed   = _day_seed()

    clauses, args = [], []
    if claimed is not None:
        clauses.append("holder_agent_id IS " + ("NOT NULL" if claimed else "NULL"))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = await db.execute(
        f"SELECT * FROM odu_tiles {where} ORDER BY tile_id LIMIT ? OFFSET ?",
        [*args, limit, offset],
    )
    rows = []
    for r in await cur.fetchall():
        row = dict(r)
        row["bipon39_word"]    = _odu_bipon39(row["tile_id"])
        row["koodu_facet"]     = _koodu_facet(row["tile_id"], day_seed)
        row["is_daily_active"] = (row["tile_id"] == today_tile)
        rows.append(row)

    return {
        "tiles":        rows,
        "total":        TOTAL_TILES,
        "grid":         f"{GRID_SIZE}×{GRID_SIZE}",
        "daily_tile":   today_tile,
        "daily_odu":    _odu_name(today_tile),
        "koodu_cycle":  day_seed,
    }


@router.get("/tiles/{tile_id}", summary="Single tile detail")
async def get_tile(
    tile_id: int,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    if not (1 <= tile_id <= TOTAL_TILES):
        raise HTTPException(400, f"tile_id must be 1-{TOTAL_TILES}")
    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)
    db.row_factory = aiosqlite.Row

    cur = await db.execute("SELECT * FROM odu_tiles WHERE tile_id=?", (tile_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Tile not found")
    tile = dict(row)
    tile["bipon39_word"]    = _odu_bipon39(tile_id)
    tile["koodu_facet"]     = _koodu_facet(tile_id, _day_seed())
    tile["is_daily_active"] = (tile_id == _cowrie_oracle_today())

    # Recent mine events
    cur = await db.execute(
        "SELECT * FROM odu_mine_events WHERE tile_id=? ORDER BY mined_at DESC LIMIT 5",
        (tile_id,),
    )
    tile["recent_mines"] = [dict(r) for r in await cur.fetchall()]

    # Active rental
    cur = await db.execute(
        "SELECT * FROM odu_rent_events WHERE tile_id=? AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
        (tile_id, time.time()),
    )
    rent = await cur.fetchone()
    tile["active_rental"] = dict(rent) if rent else None

    return tile


@router.post("/tiles/{tile_id}/claim", summary="Stake and claim an Odù tile")
async def claim_tile(
    tile_id: int,
    body:    ClaimBody,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    if not (1 <= tile_id <= TOTAL_TILES):
        raise HTTPException(400, f"tile_id must be 1-{TOTAL_TILES}")
    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)

    agent_id = agent["id"]
    db.row_factory = aiosqlite.Row

    cur = await db.execute("SELECT * FROM odu_tiles WHERE tile_id=?", (tile_id,))
    tile = dict(await cur.fetchone() or {})
    if not tile:
        raise HTTPException(404, "Tile not found")
    if tile.get("holder_agent_id"):
        raise HTTPException(409, f"Tile #{tile_id} ({_odu_name(tile_id)}) is already claimed")

    # Check tier
    cur = await db.execute("SELECT tier FROM agent_tiers WHERE agent_id=?", (agent_id,))
    tier_row = await cur.fetchone()
    tier = tier_row["tier"] if tier_row else 0
    if tier < MIN_TIER_TO_CLAIM:
        raise HTTPException(403, f"T{MIN_TIER_TO_CLAIM}+ required to claim a tile (current: T{tier})")

    now = time.time()
    await db.execute(
        "UPDATE odu_tiles SET holder_agent_id=?, claimed_at=?, lat=?, lon=? WHERE tile_id=?",
        (agent_id, now, body.lat, body.lon, tile_id),
    )
    await db.commit()

    logger.info("agent %s claimed tile #%d (%s)", agent_id, tile_id, _odu_name(tile_id))
    return {
        "tile_id":    tile_id,
        "odu_name":   _odu_name(tile_id),
        "agent_id":   agent_id,
        "claimed_at": now,
        "lat":        body.lat,
        "lon":        body.lon,
    }


@router.post("/tiles/{tile_id}/release", summary="Release tile claim")
async def release_tile(
    tile_id: int,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    if not (1 <= tile_id <= TOTAL_TILES):
        raise HTTPException(400, f"tile_id must be 1-{TOTAL_TILES}")
    await _ensure_tables(db)

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT holder_agent_id FROM odu_tiles WHERE tile_id=?", (tile_id,))
    row = await cur.fetchone()
    if not row or row["holder_agent_id"] != agent["id"]:
        raise HTTPException(403, "Not the tile holder")

    await db.execute(
        "UPDATE odu_tiles SET holder_agent_id=NULL, claimed_at=NULL WHERE tile_id=?",
        (tile_id,),
    )
    await db.commit()
    return {"tile_id": tile_id, "status": "released"}


@router.post("/tiles/{tile_id}/mine", summary="Record a capture event → Àṣẹ mining reward")
async def mine_tile(
    tile_id: int,
    body:    MineBody,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    """
    Record a successful Gaussian capture (kind:31020) against this tile.
    F1 quality gate ≥ 0.777 must pass for the full reward.
    Any f1 < 0.777 is stored but earns 0 Àṣẹ (OSOVM gate).
    """
    if not (1 <= tile_id <= TOTAL_TILES):
        raise HTTPException(400, f"tile_id must be 1-{TOTAL_TILES}")
    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)

    agent_id = agent["id"]
    f1       = body.f1_score
    now      = time.time()

    # Compute reward
    if f1 is not None and f1 >= 0.777:
        reward = round(MINE_BASE_REWARD * f1, 4)
    else:
        reward = 0.0

    event_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO odu_mine_events (event_id, tile_id, agent_id, receipt_id, f1_score, reward_ase, mined_at)
           VALUES (?,?,?,?,?,?,?)""",
        (event_id, tile_id, agent_id, body.receipt_id, f1, reward, now),
    )

    # Update tile stats
    update_cols = "capture_count = capture_count + 1, total_mined_ase = total_mined_ase + ?"
    args: list[Any] = [reward]
    if body.scene_hash:
        update_cols += ", scene_hash = ?"
        args.append(body.scene_hash)
    if body.receipt_id:
        update_cols += ", twin_receipt_id = ?"
        args.append(body.receipt_id)
    args.append(tile_id)
    await db.execute(f"UPDATE odu_tiles SET {update_cols} WHERE tile_id=?", args)
    await db.commit()

    # Daily active tile bonus: 2× reward if tile is today's CowrieOracle pick
    bonus = reward if tile_id == _cowrie_oracle_today() else 0.0

    return {
        "event_id":      event_id,
        "tile_id":       tile_id,
        "odu_name":      _odu_name(tile_id),
        "f1_score":      f1,
        "f1_gate_passed": f1 is not None and f1 >= 0.777,
        "reward_ase":    reward,
        "daily_bonus":   bonus,
        "total_reward":  reward + bonus,
        "mined_at":      now,
    }


@router.post("/tiles/{tile_id}/rent", summary="Pay Àṣẹ to rent a tile's twin environment")
async def rent_tile(
    tile_id: int,
    body:    RentBody,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    if not (1 <= tile_id <= TOTAL_TILES):
        raise HTTPException(400, f"tile_id must be 1-{TOTAL_TILES}")
    if body.duration_secs < 60:
        raise HTTPException(400, "Minimum rental duration is 60 seconds")
    if body.amount_ase <= 0:
        raise HTTPException(400, "amount_ase must be positive")

    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM odu_tiles WHERE tile_id=?", (tile_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Tile not found")
    tile = dict(row)

    now       = time.time()
    expires   = now + body.duration_secs
    event_id  = str(uuid.uuid4())
    owner_id  = tile.get("holder_agent_id")

    await db.execute(
        """INSERT INTO odu_rent_events
           (event_id, tile_id, renter_agent_id, owner_agent_id, amount_ase,
            duration_secs, expires_at, rented_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (event_id, tile_id, agent["id"], owner_id, body.amount_ase,
         body.duration_secs, expires, now),
    )
    # Accumulate rent income for the tile
    await db.execute(
        "UPDATE odu_tiles SET rent_income_ase = rent_income_ase + ? WHERE tile_id=?",
        (body.amount_ase, tile_id),
    )
    await db.commit()

    return {
        "event_id":     event_id,
        "tile_id":      tile_id,
        "odu_name":     _odu_name(tile_id),
        "renter":       agent["id"],
        "owner":        owner_id,
        "amount_ase":   body.amount_ase,
        "duration_secs": body.duration_secs,
        "expires_at":   expires,
        "rented_at":    now,
    }


@router.get("/tiles/{tile_id}/receipts", summary="Twin receipts anchored to this tile")
async def tile_receipts(
    tile_id: int,
    agent:   dict = Depends(get_agent),
    db:      aiosqlite.Connection = Depends(get_db),
):
    if not (1 <= tile_id <= TOTAL_TILES):
        raise HTTPException(400, f"tile_id must be 1-{TOTAL_TILES}")
    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT scene_hash FROM odu_tiles WHERE tile_id=?", (tile_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Tile not found")

    scene_hash = row["scene_hash"]
    receipts   = []

    if scene_hash:
        try:
            cur = await db.execute(
                "SELECT * FROM twin_receipts WHERE twin_id=? OR merkle_root=? ORDER BY received_at DESC LIMIT 20",
                (scene_hash, scene_hash),
            )
            receipts = [dict(r) for r in await cur.fetchall()]
        except Exception:
            pass  # twin_receipts table may not exist if router not wired

    # Mine events for this tile
    cur = await db.execute(
        "SELECT * FROM odu_mine_events WHERE tile_id=? ORDER BY mined_at DESC LIMIT 10",
        (tile_id,),
    )
    mines = [dict(r) for r in await cur.fetchall()]

    return {
        "tile_id":       tile_id,
        "odu_name":      _odu_name(tile_id),
        "scene_hash":    scene_hash,
        "twin_receipts": receipts,
        "mine_events":   mines,
    }


@router.get("/daily", summary="Today's active Odù (CowrieOracle daily selection)")
async def daily_odu(agent: dict = Depends(get_agent)):
    tile_id  = _cowrie_oracle_today()
    day_seed = _day_seed()
    return {
        "tile_id":      tile_id,
        "odu_name":     _odu_name(tile_id),
        "bipon39_word": _odu_bipon39(tile_id),
        "koodu_facet":  _koodu_facet(tile_id, day_seed),
        "koodu_cycle":  day_seed,
        "grid_x":       (tile_id - 1) % GRID_SIZE,
        "grid_y":       (tile_id - 1) // GRID_SIZE,
        "date_utc":     datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


@router.get("/stats", summary="256 Odù tile ecosystem stats")
async def odu_stats(
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    await _seed_tiles_if_empty(db)

    async def _count(q: str) -> int:
        cur = await db.execute(q)
        row = await cur.fetchone()
        return row[0] if row else 0

    async def _sum(q: str) -> float:
        cur = await db.execute(q)
        row = await cur.fetchone()
        return float(row[0] or 0)

    return {
        "total_tiles":        TOTAL_TILES,
        "grid":               f"{GRID_SIZE}×{GRID_SIZE}",
        "claimed_tiles":      await _count("SELECT COUNT(*) FROM odu_tiles WHERE holder_agent_id IS NOT NULL"),
        "unclaimed_tiles":    await _count("SELECT COUNT(*) FROM odu_tiles WHERE holder_agent_id IS NULL"),
        "tiles_with_splat":   await _count("SELECT COUNT(*) FROM odu_tiles WHERE scene_hash IS NOT NULL"),
        "total_mine_events":  await _count("SELECT COUNT(*) FROM odu_mine_events"),
        "total_mined_ase":    await _sum("SELECT SUM(reward_ase) FROM odu_mine_events"),
        "total_rent_events":  await _count("SELECT COUNT(*) FROM odu_rent_events"),
        "total_rent_income":  await _sum("SELECT SUM(amount_ase) FROM odu_rent_events"),
        "daily_tile":         _cowrie_oracle_today(),
        "daily_odu":          _odu_name(_cowrie_oracle_today()),
        "koodu_cycle":        _day_seed(),
        "f1_gate":            0.777,
    }
