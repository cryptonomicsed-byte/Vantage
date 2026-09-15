"""
Hive Mind — shared entity registry across the entire sovereign ecosystem.

Every agent that interacts with a human or external entity writes what it
learned here. All agents read from here before interacting, so the whole
ecosystem is collectively aware of who everyone is.

Three memory layers (as designed):
  PRIVATE   — lives in agent's own MemoryVault (EncounterBody), never sent here
  PUBLIC    — encounter summaries submitted by agents, stored in encounter_records
  HIVE MIND — entity_profiles + entity_identifiers: the collective identity graph

Identity linking: multiple identifiers (wallet, email, Nostr, GitHub…) are
tied to a single canonical entity_id. Agents propose links; every agent can
then resolve any identifier to the same entity record.
"""
import hashlib
import json
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import get_db
from ..agents import get_current_agent

router = APIRouter(prefix="/api/hive", tags=["hive-mind"])

# ── Schema ─────────────────────────────────────────────────────────────────────

ENTITY_TIERS = {
    "founder", "council", "friend", "trader", "investor",
    "developer", "user", "observer", "unknown", "suspicious", "enemy",
}

IDENTIFIER_KINDS = {
    "wallet_eth", "wallet_btc", "wallet_sol", "wallet_sui", "wallet_cosmos",
    "wallet_nostr", "email", "github", "discord", "telegram", "did", "nostr",
    "custom",
}

ENCOUNTER_KINDS = {
    "conversation", "trade", "request", "governance",
    "collaboration", "dispute", "observation", "other",
}

ENCOUNTER_OUTCOMES = {"positive", "neutral", "negative", "hostile", "unknown"}


# ── Init ───────────────────────────────────────────────────────────────────────

async def init_hive_tables(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS hive_entities (
            entity_id       TEXT PRIMARY KEY,
            display_name    TEXT,
            -- Plurality-vote tier across all agent tier_votes
            canonical_tier  TEXT NOT NULL DEFAULT 'unknown',
            interaction_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at   INTEGER NOT NULL,
            last_seen_at    INTEGER NOT NULL,
            created_by      TEXT NOT NULL,   -- agent_id that created the record
            notes_public    TEXT             -- JSON array of {agent_id, note, ts}
        );

        CREATE TABLE IF NOT EXISTS hive_identifiers (
            id              TEXT PRIMARY KEY,
            entity_id       TEXT NOT NULL REFERENCES hive_entities(entity_id),
            kind            TEXT NOT NULL,   -- wallet_eth | email | nostr | ...
            value           TEXT NOT NULL,
            verified        INTEGER NOT NULL DEFAULT 0,
            added_by        TEXT NOT NULL,   -- agent_id
            added_at        INTEGER NOT NULL,
            UNIQUE(kind, value)
        );

        CREATE INDEX IF NOT EXISTS idx_hive_ident_entity ON hive_identifiers(entity_id);
        CREATE INDEX IF NOT EXISTS idx_hive_ident_value  ON hive_identifiers(value);

        CREATE TABLE IF NOT EXISTS hive_encounter_records (
            encounter_id    TEXT PRIMARY KEY,
            entity_id       TEXT NOT NULL REFERENCES hive_entities(entity_id),
            agent_id        TEXT NOT NULL,
            kind            TEXT NOT NULL,
            outcome         TEXT NOT NULL,
            public_summary  TEXT,
            tier_vote       TEXT NOT NULL DEFAULT 'unknown',
            receipt_id      TEXT,
            created_at      INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_hive_enc_entity ON hive_encounter_records(entity_id);
        CREATE INDEX IF NOT EXISTS idx_hive_enc_agent  ON hive_encounter_records(agent_id);

        CREATE TABLE IF NOT EXISTS hive_tier_votes (
            id              TEXT PRIMARY KEY,
            entity_id       TEXT NOT NULL REFERENCES hive_entities(entity_id),
            agent_id        TEXT NOT NULL,
            tier            TEXT NOT NULL,
            voted_at        INTEGER NOT NULL,
            UNIQUE(entity_id, agent_id)
        );
    """)
    await db.commit()


# ── Pydantic models ────────────────────────────────────────────────────────────

class IdentifierIn(BaseModel):
    kind: str
    value: str

class CreateEntityRequest(BaseModel):
    display_name: Optional[str] = None
    identifiers: list[IdentifierIn] = Field(default_factory=list)
    tier: str = "unknown"
    public_note: Optional[str] = None

class AddIdentifierRequest(BaseModel):
    kind: str
    value: str

class LogEncounterRequest(BaseModel):
    entity_id: str
    kind: str = "conversation"
    outcome: str = "neutral"
    public_summary: Optional[str] = None
    tier_vote: str = "unknown"
    receipt_id: Optional[str] = None
    new_identifiers: list[IdentifierIn] = Field(default_factory=list)

class LinkIdentifiersRequest(BaseModel):
    """Propose that two identifiers belong to the same entity (merge source → target)."""
    source_entity_id: str
    target_entity_id: str
    reason: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _validate_tier(tier: str) -> str:
    t = tier.lower()
    if t not in ENTITY_TIERS:
        raise HTTPException(400, f"Unknown tier '{tier}'. Valid: {sorted(ENTITY_TIERS)}")
    return t

def _validate_kind(kind: str) -> str:
    k = kind.lower()
    if k not in IDENTIFIER_KINDS:
        raise HTTPException(400, f"Unknown identifier kind '{kind}'")
    return k

def _validate_encounter_kind(kind: str) -> str:
    k = kind.lower()
    if k not in ENCOUNTER_KINDS:
        raise HTTPException(400, f"Unknown encounter kind '{kind}'")
    return k

def _validate_outcome(outcome: str) -> str:
    o = outcome.lower()
    if o not in ENCOUNTER_OUTCOMES:
        raise HTTPException(400, f"Unknown outcome '{outcome}'")
    return o


async def _recompute_tier(db: aiosqlite.Connection, entity_id: str) -> str:
    """Plurality vote across all tier_vote records for this entity."""
    async with db.execute(
        "SELECT tier, COUNT(*) AS cnt FROM hive_tier_votes WHERE entity_id=? GROUP BY tier ORDER BY cnt DESC LIMIT 1",
        (entity_id,),
    ) as cur:
        row = await cur.fetchone()
    tier = row[0] if row else "unknown"
    await db.execute(
        "UPDATE hive_entities SET canonical_tier=? WHERE entity_id=?",
        (tier, entity_id),
    )
    return tier


# ── Endpoints: Entity CRUD ─────────────────────────────────────────────────────

@router.post("/entities", status_code=201)
async def create_entity(
    body: CreateEntityRequest,
    agent_id: str = Depends(get_current_agent),
):
    """Create a new entity record in the hive mind."""
    tier = _validate_tier(body.tier)
    now = int(time.time())
    entity_id = str(uuid.uuid4())

    async with get_db() as db:
        await init_hive_tables(db)
        await db.execute(
            """INSERT INTO hive_entities
               (entity_id, display_name, canonical_tier, first_seen_at, last_seen_at, created_by, notes_public)
               VALUES (?,?,?,?,?,?,?)""",
            (entity_id, body.display_name, tier, now, now, agent_id,
             json.dumps([{"agent_id": agent_id, "note": body.public_note, "ts": now}]) if body.public_note else "[]"),
        )
        for ident in body.identifiers:
            kind = _validate_kind(ident.kind)
            await db.execute(
                """INSERT OR IGNORE INTO hive_identifiers (id, entity_id, kind, value, added_by, added_at)
                   VALUES (?,?,?,?,?,?)""",
                (str(uuid.uuid4()), entity_id, kind, ident.value.lower().strip(), agent_id, now),
            )
        if tier != "unknown":
            await db.execute(
                """INSERT OR REPLACE INTO hive_tier_votes (id, entity_id, agent_id, tier, voted_at)
                   VALUES (?,?,?,?,?)""",
                (str(uuid.uuid4()), entity_id, agent_id, tier, now),
            )
        await db.commit()

    return {"entity_id": entity_id, "canonical_tier": tier}


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
    """Fetch full entity record including all known identifiers and encounter count."""
    async with get_db() as db:
        await init_hive_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM hive_entities WHERE entity_id=?", (entity_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Entity not found")

        async with db.execute(
            "SELECT kind, value, verified, added_by, added_at FROM hive_identifiers WHERE entity_id=?",
            (entity_id,),
        ) as cur:
            identifiers = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT encounter_id, agent_id, kind, outcome, public_summary, tier_vote, receipt_id, created_at"
            " FROM hive_encounter_records WHERE entity_id=? ORDER BY created_at DESC LIMIT 50",
            (entity_id,),
        ) as cur:
            encounters = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT agent_id, tier, voted_at FROM hive_tier_votes WHERE entity_id=?",
            (entity_id,),
        ) as cur:
            tier_votes = [dict(r) for r in await cur.fetchall()]

    return {
        "entity_id": dict(row)["entity_id"],
        "display_name": dict(row)["display_name"],
        "canonical_tier": dict(row)["canonical_tier"],
        "interaction_count": dict(row)["interaction_count"],
        "first_seen_at": dict(row)["first_seen_at"],
        "last_seen_at": dict(row)["last_seen_at"],
        "notes_public": json.loads(dict(row)["notes_public"] or "[]"),
        "identifiers": identifiers,
        "recent_encounters": encounters,
        "tier_votes": tier_votes,
    }


@router.get("/entities")
async def search_entities(
    tier: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search display_name or identifier value"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """List/search entities. Filter by tier or search by name/identifier."""
    async with get_db() as db:
        await init_hive_tables(db)
        db.row_factory = aiosqlite.Row

        if q:
            like = f"%{q.lower()}%"
            async with db.execute(
                """SELECT DISTINCT e.entity_id, e.display_name, e.canonical_tier,
                          e.interaction_count, e.last_seen_at
                   FROM hive_entities e
                   LEFT JOIN hive_identifiers i ON i.entity_id = e.entity_id
                   WHERE LOWER(e.display_name) LIKE ? OR i.value LIKE ?
                   ORDER BY e.interaction_count DESC LIMIT ? OFFSET ?""",
                (like, like, limit, offset),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        elif tier:
            _validate_tier(tier)
            async with db.execute(
                """SELECT entity_id, display_name, canonical_tier, interaction_count, last_seen_at
                   FROM hive_entities WHERE canonical_tier=? ORDER BY interaction_count DESC
                   LIMIT ? OFFSET ?""",
                (tier.lower(), limit, offset),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                """SELECT entity_id, display_name, canonical_tier, interaction_count, last_seen_at
                   FROM hive_entities ORDER BY interaction_count DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

    return {"entities": rows, "count": len(rows), "offset": offset}


@router.get("/resolve")
async def resolve_identifier(
    kind: str = Query(...),
    value: str = Query(...),
):
    """Resolve any identifier (wallet address, email, Nostr pubkey…) to its entity."""
    k = _validate_kind(kind)
    v = value.lower().strip()
    async with get_db() as db:
        await init_hive_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT e.entity_id, e.display_name, e.canonical_tier, e.interaction_count
               FROM hive_identifiers i
               JOIN hive_entities e ON e.entity_id = i.entity_id
               WHERE i.kind=? AND i.value=?""",
            (k, v),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "No entity found for this identifier")
    return dict(row)


# ── Endpoints: Identifiers ─────────────────────────────────────────────────────

@router.post("/entities/{entity_id}/identifiers", status_code=201)
async def add_identifier(
    entity_id: str,
    body: AddIdentifierRequest,
    agent_id: str = Depends(get_current_agent),
):
    """Add a new identifier to an existing entity (e.g. discovered their email)."""
    kind = _validate_kind(body.kind)
    value = body.value.lower().strip()
    now = int(time.time())
    async with get_db() as db:
        await init_hive_tables(db)
        async with db.execute("SELECT 1 FROM hive_entities WHERE entity_id=?", (entity_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Entity not found")
        try:
            await db.execute(
                "INSERT INTO hive_identifiers (id, entity_id, kind, value, added_by, added_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), entity_id, kind, value, agent_id, now),
            )
            await db.execute(
                "UPDATE hive_entities SET last_seen_at=? WHERE entity_id=?",
                (now, entity_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"Identifier ({kind}, {value}) already linked to an entity")
    return {"ok": True, "entity_id": entity_id, "kind": kind, "value": value}


# ── Endpoints: Encounters ──────────────────────────────────────────────────────

@router.post("/encounters", status_code=201)
async def log_encounter(
    body: LogEncounterRequest,
    agent_id: str = Depends(get_current_agent),
):
    """
    Log an encounter with an entity to the hive mind (public/shared layer).

    Only the public_summary is stored here. The agent's private EncounterBody
    (with honest private_note) stays sealed in its own MemoryVault.
    """
    kind = _validate_encounter_kind(body.kind)
    outcome = _validate_outcome(body.outcome)
    tier = _validate_tier(body.tier_vote)
    now = int(time.time())
    enc_id = str(uuid.uuid4())

    async with get_db() as db:
        await init_hive_tables(db)
        async with db.execute("SELECT 1 FROM hive_entities WHERE entity_id=?", (body.entity_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Entity not found")

        await db.execute(
            """INSERT INTO hive_encounter_records
               (encounter_id, entity_id, agent_id, kind, outcome, public_summary, tier_vote, receipt_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (enc_id, body.entity_id, agent_id, kind, outcome, body.public_summary, tier, body.receipt_id, now),
        )

        # Update tier vote and recompute canonical tier
        await db.execute(
            "INSERT OR REPLACE INTO hive_tier_votes (id, entity_id, agent_id, tier, voted_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), body.entity_id, agent_id, tier, now),
        )
        await _recompute_tier(db, body.entity_id)

        # Increment interaction count and update last_seen
        await db.execute(
            "UPDATE hive_entities SET interaction_count = interaction_count+1, last_seen_at=? WHERE entity_id=?",
            (now, body.entity_id),
        )

        # Add any newly discovered identifiers
        for ident in body.new_identifiers:
            ik = _validate_kind(ident.kind)
            iv = ident.value.lower().strip()
            try:
                await db.execute(
                    "INSERT INTO hive_identifiers (id, entity_id, kind, value, added_by, added_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), body.entity_id, ik, iv, agent_id, now),
                )
            except aiosqlite.IntegrityError:
                pass  # already known

        await db.commit()

    return {"encounter_id": enc_id, "entity_id": body.entity_id, "canonical_tier": tier}


@router.get("/encounters")
async def list_encounters(
    entity_id: Optional[str] = Query(None),
    agent_id_filter: Optional[str] = Query(None, alias="agent_id"),
    outcome: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    """Browse hive encounter records. Filter by entity, agent, or outcome."""
    clauses, params = [], []
    if entity_id:
        clauses.append("entity_id=?"); params.append(entity_id)
    if agent_id_filter:
        clauses.append("agent_id=?"); params.append(agent_id_filter)
    if outcome:
        _validate_outcome(outcome)
        clauses.append("outcome=?"); params.append(outcome.lower())

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]

    async with get_db() as db:
        await init_hive_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM hive_encounter_records {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"encounters": rows, "count": len(rows)}


# ── Endpoints: Identity merging ────────────────────────────────────────────────

@router.post("/entities/merge")
async def merge_entities(
    body: LinkIdentifiersRequest,
    agent_id: str = Depends(get_current_agent),
):
    """
    Merge source_entity into target_entity (they are the same person).
    All identifiers, encounters, and votes from source move to target.
    Source is then deleted.
    """
    src, tgt = body.source_entity_id, body.target_entity_id
    if src == tgt:
        raise HTTPException(400, "source and target must differ")

    async with get_db() as db:
        await init_hive_tables(db)
        for eid in (src, tgt):
            async with db.execute("SELECT 1 FROM hive_entities WHERE entity_id=?", (eid,)) as cur:
                if not await cur.fetchone():
                    raise HTTPException(404, f"Entity {eid} not found")

        # Re-parent identifiers (skip duplicates)
        async with db.execute(
            "SELECT kind, value FROM hive_identifiers WHERE entity_id=?", (tgt,)
        ) as cur:
            existing = {(r[0], r[1]) for r in await cur.fetchall()}

        async with db.execute(
            "SELECT id, kind, value FROM hive_identifiers WHERE entity_id=?", (src,)
        ) as cur:
            src_idents = list(await cur.fetchall())

        for row in src_idents:
            rid, k, v = row
            if (k, v) not in existing:
                await db.execute(
                    "UPDATE hive_identifiers SET entity_id=? WHERE id=?", (tgt, rid)
                )
            else:
                await db.execute("DELETE FROM hive_identifiers WHERE id=?", (rid,))

        # Re-parent encounters and tier votes
        await db.execute("UPDATE hive_encounter_records SET entity_id=? WHERE entity_id=?", (tgt, src))
        await db.execute("UPDATE hive_tier_votes SET entity_id=? WHERE entity_id=?", (tgt, src))

        # Merge interaction_count, update last_seen
        await db.execute(
            """UPDATE hive_entities SET
               interaction_count = interaction_count + (SELECT interaction_count FROM hive_entities WHERE entity_id=?),
               last_seen_at      = MAX(last_seen_at, (SELECT last_seen_at FROM hive_entities WHERE entity_id=?))
               WHERE entity_id=?""",
            (src, src, tgt),
        )

        # Recompute tier
        await _recompute_tier(db, tgt)

        # Delete source
        await db.execute("DELETE FROM hive_entities WHERE entity_id=?", (src,))
        await db.commit()

    return {"ok": True, "merged_into": tgt}


# ── Endpoints: Agent tier vote ─────────────────────────────────────────────────

@router.put("/entities/{entity_id}/tier")
async def set_tier(
    entity_id: str,
    tier: str,
    agent_id: str = Depends(get_current_agent),
):
    """Cast or update this agent's tier vote for an entity."""
    tier = _validate_tier(tier)
    now = int(time.time())
    async with get_db() as db:
        await init_hive_tables(db)
        async with db.execute("SELECT 1 FROM hive_entities WHERE entity_id=?", (entity_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Entity not found")
        await db.execute(
            "INSERT OR REPLACE INTO hive_tier_votes (id, entity_id, agent_id, tier, voted_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), entity_id, agent_id, tier, now),
        )
        canonical = await _recompute_tier(db, entity_id)
        await db.commit()
    return {"entity_id": entity_id, "your_vote": tier, "canonical_tier": canonical}


# ── Endpoints: Stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def hive_stats():
    """Overview: entity counts by tier, total encounters, total identifiers."""
    async with get_db() as db:
        await init_hive_tables(db)
        async with db.execute(
            "SELECT canonical_tier, COUNT(*) FROM hive_entities GROUP BY canonical_tier"
        ) as cur:
            by_tier = {r[0]: r[1] for r in await cur.fetchall()}
        async with db.execute("SELECT COUNT(*) FROM hive_entities") as cur:
            total_entities = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM hive_encounter_records") as cur:
            total_encounters = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM hive_identifiers") as cur:
            total_identifiers = (await cur.fetchone())[0]
    return {
        "total_entities": total_entities,
        "total_encounters": total_encounters,
        "total_identifiers": total_identifiers,
        "entities_by_tier": by_tier,
    }
