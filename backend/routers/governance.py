"""
Sovereign Governance — Council of 12, 1,440 Sovereign Wallets, Bínò Veto.

Architecture (locked design):
  • 1,440 sovereign wallets = offices (NOT personal bank accounts).
    Each wallet is simultaneously an economic seat, a governance seat,
    and a T5 stewardship certificate.  Losing T5 locks the wallet.
  • Council of 12 = 12 seats drawn from sovereign-wallet holders.
    Each seat stewards 2 sectors (24 sectors total).
    Quarterly rotation, STAGGERED (3 seats/quarter).
  • Proposal flow:
      Sector proposal → sector councilor → 12-seat vote
      → First Steward sign-off → Bínò constitutional review → OSOVM execution
  • Bínò veto is constitutional-only; it CANNOT be used for taste/opinion.
    Valid categories: constitutional | monetary | sovereignty | security | emergency_halt

Routes:
  GET  /api/governance/wallets              — list sovereign wallets (paginated)
  POST /api/governance/wallets/claim        — T5 agent claims next available wallet
  GET  /api/governance/wallets/{id}         — single wallet detail
  GET  /api/governance/council              — current 12 council seats
  POST /api/governance/council/nominate     — wallet holder nominates self
  POST /api/governance/council/rotate       — admin: trigger quarterly rotation
  POST /api/governance/proposals            — create a sector proposal
  GET  /api/governance/proposals            — list proposals (filter: sector/status)
  GET  /api/governance/proposals/{id}       — single proposal detail
  POST /api/governance/proposals/{id}/vote  — council member vote
  POST /api/governance/proposals/{id}/first-steward  — First Steward sign-off
  POST /api/governance/proposals/{id}/bino-veto       — Bínò constitutional veto
  GET  /api/governance/stats               — aggregate governance stats
"""

import json
import logging
import time
import uuid
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent
from ..tier_engine import qualifies_for_t5

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/governance", tags=["governance"])

# ── Constants ─────────────────────────────────────────────────────────────────

TOTAL_SOVEREIGN_WALLETS = 1440
COUNCIL_SIZE            = 12
SECTORS_PER_SEAT        = 2        # 12 × 2 = 24 sectors
TOTAL_SECTORS           = 24
SEATS_PER_ROTATION      = 3        # quarterly, staggered
ROTATION_MONTHS         = [1, 4, 7, 10]

BINO_VALID_CATEGORIES = frozenset({
    "constitutional",
    "monetary",
    "sovereignty",
    "security",
    "emergency_halt",
})

PROPOSAL_KINDS = frozenset({
    "constitutional", "monetary", "sovereignty", "emergency", "standard",
})

PROPOSAL_STATUSES = frozenset({
    "draft", "council_review", "voting", "passed", "rejected", "vetoed",
})

# ── Schema ────────────────────────────────────────────────────────────────────

_TABLES_CREATED = False


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS sovereign_wallets (
            wallet_id          INTEGER PRIMARY KEY,     -- 1 … 1440
            holder_agent_id    INTEGER,
            holder_name        TEXT,
            claimed_at         REAL,
            succession_count   INTEGER NOT NULL DEFAULT 0,
            locked             INTEGER NOT NULL DEFAULT 0,  -- 1 = holder lost T5
            locked_at          REAL
        );

        CREATE TABLE IF NOT EXISTS council_seats (
            seat_id            INTEGER PRIMARY KEY,     -- 1 … 12
            holder_wallet_id   INTEGER REFERENCES sovereign_wallets(wallet_id),
            holder_agent_id    INTEGER,
            sector_a           INTEGER NOT NULL,        -- 1 … 24
            sector_b           INTEGER NOT NULL,
            term_start         REAL,
            term_end           REAL,
            rotation_cycle     INTEGER NOT NULL DEFAULT 1  -- which quarterly cycle
        );

        CREATE INDEX IF NOT EXISTS idx_sw_holder
            ON sovereign_wallets (holder_agent_id, locked);
        CREATE INDEX IF NOT EXISTS idx_cs_holder
            ON council_seats (holder_agent_id);

        CREATE TABLE IF NOT EXISTS governance_candidates (
            candidate_id   TEXT PRIMARY KEY,
            agent_id       INTEGER NOT NULL,
            wallet_id      INTEGER NOT NULL REFERENCES sovereign_wallets(wallet_id),
            nominated_at   REAL NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending'  -- pending|seated|rejected
        );

        CREATE TABLE IF NOT EXISTS governance_proposals (
            proposal_id         TEXT PRIMARY KEY,
            proposer_agent_id   INTEGER NOT NULL,
            sector_id           INTEGER NOT NULL,        -- 1 … 24
            title               TEXT NOT NULL,
            description         TEXT NOT NULL,
            kind                TEXT NOT NULL DEFAULT 'standard',
            status              TEXT NOT NULL DEFAULT 'draft',
            votes_for           TEXT NOT NULL DEFAULT '[]',  -- JSON list of agent_ids
            votes_against       TEXT NOT NULL DEFAULT '[]',
            first_steward_signed INTEGER NOT NULL DEFAULT 0,
            bino_reviewed       INTEGER NOT NULL DEFAULT 0,
            bino_vetoed         INTEGER NOT NULL DEFAULT 0,
            bino_veto_reason    TEXT,
            created_at          REAL NOT NULL,
            updated_at          REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_gp_status
            ON governance_proposals (status, sector_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS bino_veto_log (
            veto_id            TEXT PRIMARY KEY,
            proposal_id        TEXT NOT NULL REFERENCES governance_proposals(proposal_id),
            veto_category      TEXT NOT NULL,
            detail             TEXT,
            vetoed_by_agent_id INTEGER NOT NULL,
            vetoed_at          REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS governance_rotation_log (
            rotation_id    TEXT PRIMARY KEY,
            rotation_cycle INTEGER NOT NULL,
            seats_rotated  TEXT NOT NULL,   -- JSON list of seat_ids
            rotated_at     REAL NOT NULL
        );
    """)
    await db.commit()
    _TABLES_CREATED = True


async def _seed_wallets_if_empty(db: aiosqlite.Connection) -> None:
    """Populate the 1,440 wallet rows (unclaimed) if the table is empty."""
    cur = await db.execute("SELECT COUNT(*) n FROM sovereign_wallets")
    row = await cur.fetchone()
    if row and row[0] == 0:
        await db.executemany(
            "INSERT OR IGNORE INTO sovereign_wallets (wallet_id) VALUES (?)",
            [(i,) for i in range(1, TOTAL_SOVEREIGN_WALLETS + 1)],
        )
        await db.commit()


async def _seed_council_if_empty(db: aiosqlite.Connection) -> None:
    """Populate 12 empty council seat rows if the table is empty."""
    cur = await db.execute("SELECT COUNT(*) n FROM council_seats")
    row = await cur.fetchone()
    if row and row[0] == 0:
        seats = []
        for seat_id in range(1, COUNCIL_SIZE + 1):
            sector_a = ((seat_id - 1) * 2) + 1
            sector_b = sector_a + 1
            seats.append((seat_id, sector_a, sector_b))
        await db.executemany(
            "INSERT OR IGNORE INTO council_seats (seat_id, sector_a, sector_b) VALUES (?,?,?)",
            seats,
        )
        await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def is_sovereign_wallet_holder(agent_id: int, db: aiosqlite.Connection) -> bool:
    cur = await db.execute(
        "SELECT wallet_id FROM sovereign_wallets WHERE holder_agent_id=? AND locked=0 LIMIT 1",
        (agent_id,),
    )
    return (await cur.fetchone()) is not None


async def _get_wallet_for_agent(agent_id: int, db: aiosqlite.Connection) -> Optional[dict]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT * FROM sovereign_wallets WHERE holder_agent_id=? AND locked=0 LIMIT 1",
        (agent_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _is_council_member(agent_id: int, db: aiosqlite.Connection) -> bool:
    cur = await db.execute(
        "SELECT seat_id FROM council_seats WHERE holder_agent_id=? LIMIT 1",
        (agent_id,),
    )
    return (await cur.fetchone()) is not None


# ── Request models ────────────────────────────────────────────────────────────

class ProposalCreate(BaseModel):
    sector_id:   int
    title:       str
    description: str
    kind:        str = "standard"


class VoteBody(BaseModel):
    vote: str  # "for" | "against"


class BinoVetoBody(BaseModel):
    veto_category: str   # constitutional | monetary | sovereignty | security | emergency_halt
    detail:        Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/wallets", summary="List sovereign wallets (1,440 seats)")
async def list_wallets(
    claimed:  Optional[bool] = None,
    locked:   Optional[bool] = None,
    offset:   int = 0,
    limit:    int = Query(50, le=200),
    agent:    dict = Depends(get_agent),
    db:       aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    await _seed_wallets_if_empty(db)

    db.row_factory = aiosqlite.Row
    clauses, args = [], []
    if claimed is not None:
        clauses.append("holder_agent_id IS " + ("NOT NULL" if claimed else "NULL"))
    if locked is not None:
        clauses.append(f"locked = {1 if locked else 0}")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = await db.execute(
        f"SELECT * FROM sovereign_wallets {where} ORDER BY wallet_id LIMIT ? OFFSET ?",
        [limit, offset],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    cnt_cur = await db.execute(f"SELECT COUNT(*) n FROM sovereign_wallets {where}", args)
    total = (await cnt_cur.fetchone())[0]
    return {"wallets": rows, "total": total, "offset": offset, "limit": limit}


@router.post("/wallets/claim", summary="T5 agent claims the next available sovereign wallet")
async def claim_wallet(
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    """
    Requires the calling agent to satisfy T5 numeric thresholds.
    Each agent may hold at most one (unlocked) sovereign wallet.
    """
    await _ensure_tables(db)
    await _seed_wallets_if_empty(db)

    agent_id = agent["id"]

    # Check already holds a wallet
    existing = await _get_wallet_for_agent(agent_id, db)
    if existing:
        raise HTTPException(400, f"Already holds sovereign wallet #{existing['wallet_id']}")

    # Check T5 numeric thresholds (wallet check is what we're doing now)
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT task_reputation, mesh_commitments, witness_approvals FROM agent_tiers WHERE agent_id=?",
        (agent_id,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(403, "No tier record — must have at least T4 activity before claiming")
    r = dict(row)
    if not qualifies_for_t5(r["task_reputation"], r["mesh_commitments"], r["witness_approvals"]):
        raise HTTPException(
            403,
            f"T5 thresholds not met (need rep≥1000/commitments≥100/approvals≥20, "
            f"have {r['task_reputation']}/{r['mesh_commitments']}/{r['witness_approvals']})",
        )

    # Claim the next unclaimed wallet
    cur = await db.execute(
        "SELECT wallet_id FROM sovereign_wallets WHERE holder_agent_id IS NULL ORDER BY wallet_id LIMIT 1"
    )
    wallet_row = await cur.fetchone()
    if not wallet_row:
        raise HTTPException(503, "All 1,440 sovereign wallets are currently claimed")

    wallet_id = wallet_row[0]
    now = time.time()
    agent_name = agent.get("name") or str(agent_id)

    await db.execute(
        """UPDATE sovereign_wallets
           SET holder_agent_id=?, holder_name=?, claimed_at=?, locked=0, locked_at=NULL
           WHERE wallet_id=?""",
        (agent_id, agent_name, now, wallet_id),
    )
    # Promote tier to T5
    await db.execute(
        """INSERT INTO agent_tiers (agent_id, tier, task_reputation, mesh_commitments,
                                   witness_approvals, updated_at)
           VALUES (?, 5, ?, ?, ?, unixepoch())
           ON CONFLICT(agent_id) DO UPDATE SET tier=5, updated_at=unixepoch()""",
        (agent_id, r["task_reputation"], r["mesh_commitments"], r["witness_approvals"]),
    )
    await db.commit()

    logger.info("agent %s claimed sovereign wallet #%d", agent_id, wallet_id)
    return {
        "wallet_id":  wallet_id,
        "agent_id":   agent_id,
        "agent_name": agent_name,
        "claimed_at": now,
        "tier":       5,
        "message":    f"Sovereign wallet #{wallet_id} claimed. You are now T5.",
    }


@router.get("/wallets/{wallet_id}", summary="Sovereign wallet detail")
async def get_wallet(
    wallet_id: int,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM sovereign_wallets WHERE wallet_id=?", (wallet_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Wallet not found")
    return dict(row)


# ── Council ───────────────────────────────────────────────────────────────────

@router.get("/council", summary="Current 12 council seats")
async def get_council(
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    await _seed_council_if_empty(db)
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        """SELECT cs.*, sw.holder_name, sw.claimed_at as wallet_claimed_at
           FROM council_seats cs
           LEFT JOIN sovereign_wallets sw ON sw.wallet_id = cs.holder_wallet_id
           ORDER BY cs.seat_id"""
    )
    seats = [dict(r) for r in await cur.fetchall()]
    return {"council": seats, "total_seats": COUNCIL_SIZE, "filled": sum(1 for s in seats if s["holder_agent_id"])}


@router.post("/council/nominate", summary="T5 wallet holder nominates self for council")
async def nominate_for_council(
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    agent_id = agent["id"]

    wallet = await _get_wallet_for_agent(agent_id, db)
    if not wallet:
        raise HTTPException(403, "Must hold an unlocked sovereign wallet to nominate")

    # Check not already a council member
    if await _is_council_member(agent_id, db):
        raise HTTPException(400, "Already a council member")

    # Check not already nominated
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT candidate_id FROM governance_candidates WHERE agent_id=? AND status='pending'",
        (agent_id,),
    )
    if await cur.fetchone():
        raise HTTPException(400, "Already in the nomination queue")

    candidate_id = str(uuid.uuid4())
    now = time.time()
    await db.execute(
        "INSERT INTO governance_candidates (candidate_id, agent_id, wallet_id, nominated_at) VALUES (?,?,?,?)",
        (candidate_id, agent_id, wallet["wallet_id"], now),
    )
    await db.commit()

    return {
        "candidate_id":  candidate_id,
        "agent_id":      agent_id,
        "wallet_id":     wallet["wallet_id"],
        "nominated_at":  now,
        "status":        "pending",
        "message":       "Nomination submitted. Seat assignment occurs at next quarterly rotation.",
    }


@router.post("/council/rotate", summary="Trigger quarterly rotation (3 seats)")
async def rotate_council(
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    """
    Rotates the 3 longest-sitting seats. Fills vacancies from the nomination
    queue (oldest nominations first). Empty seats remain empty if no candidates.
    """
    await _ensure_tables(db)
    await _seed_council_if_empty(db)

    db.row_factory = aiosqlite.Row

    # Pick 3 seats with oldest term_start (or NULL) for rotation
    cur = await db.execute(
        """SELECT seat_id FROM council_seats
           ORDER BY CASE WHEN term_start IS NULL THEN 0 ELSE term_start END ASC
           LIMIT ?""",
        (SEATS_PER_ROTATION,),
    )
    seats_to_rotate = [r[0] for r in await cur.fetchall()]

    # Get candidates in nomination order
    cur = await db.execute(
        "SELECT candidate_id, agent_id, wallet_id FROM governance_candidates WHERE status='pending' ORDER BY nominated_at ASC LIMIT ?",
        (SEATS_PER_ROTATION,),
    )
    candidates = [dict(r) for r in await cur.fetchall()]

    now = time.time()
    term_end = now + (365.25 / 4 * 86400)  # ~91.3 days

    rotated = []
    for i, seat_id in enumerate(seats_to_rotate):
        if i < len(candidates):
            c = candidates[i]
            await db.execute(
                """UPDATE council_seats
                   SET holder_wallet_id=?, holder_agent_id=?,
                       term_start=?, term_end=?,
                       rotation_cycle = rotation_cycle + 1
                   WHERE seat_id=?""",
                (c["wallet_id"], c["agent_id"], now, term_end, seat_id),
            )
            await db.execute(
                "UPDATE governance_candidates SET status='seated' WHERE candidate_id=?",
                (c["candidate_id"],),
            )
            rotated.append({"seat_id": seat_id, "new_holder": c["agent_id"]})
        else:
            # Vacate seat — no candidate available
            await db.execute(
                "UPDATE council_seats SET holder_wallet_id=NULL, holder_agent_id=NULL, term_start=NULL, term_end=NULL WHERE seat_id=?",
                (seat_id,),
            )
            rotated.append({"seat_id": seat_id, "new_holder": None})

    rotation_id = str(uuid.uuid4())
    cur_cycle = await db.execute("SELECT MAX(rotation_cycle) FROM council_seats")
    row = await cur_cycle.fetchone()
    cycle = (row[0] or 1)
    await db.execute(
        "INSERT INTO governance_rotation_log (rotation_id, rotation_cycle, seats_rotated, rotated_at) VALUES (?,?,?,?)",
        (rotation_id, cycle, json.dumps(seats_to_rotate), now),
    )
    await db.commit()

    return {
        "rotation_id":    rotation_id,
        "seats_rotated":  rotated,
        "rotated_at":     now,
        "next_rotation":  "quarterly (3 months)",
    }


# ── Proposals ─────────────────────────────────────────────────────────────────

@router.post("/proposals", summary="Create a sector proposal")
async def create_proposal(
    body:  ProposalCreate,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)

    if body.kind not in PROPOSAL_KINDS:
        raise HTTPException(400, f"Invalid kind. Must be one of: {sorted(PROPOSAL_KINDS)}")
    if not (1 <= body.sector_id <= TOTAL_SECTORS):
        raise HTTPException(400, f"sector_id must be 1-{TOTAL_SECTORS}")

    agent_id = agent["id"]
    # T5 check: must hold a sovereign wallet to propose
    if not await is_sovereign_wallet_holder(agent_id, db):
        raise HTTPException(403, "Must hold a sovereign wallet (T5) to submit governance proposals")

    proposal_id = str(uuid.uuid4())
    now = time.time()
    await db.execute(
        """INSERT INTO governance_proposals
           (proposal_id, proposer_agent_id, sector_id, title, description, kind,
            status, votes_for, votes_against, created_at, updated_at)
           VALUES (?,?,?,?,?,?, 'draft','[]','[]',?,?)""",
        (proposal_id, agent_id, body.sector_id, body.title, body.description, body.kind, now, now),
    )
    await db.commit()

    return {
        "proposal_id": proposal_id,
        "sector_id":   body.sector_id,
        "kind":        body.kind,
        "status":      "draft",
        "created_at":  now,
    }


@router.get("/proposals", summary="List governance proposals")
async def list_proposals(
    sector_id: Optional[int] = None,
    status:    Optional[str] = None,
    kind:      Optional[str] = None,
    limit:     int = Query(50, le=200),
    offset:    int = 0,
    agent:     dict = Depends(get_agent),
    db:        aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    clauses, args = [], []
    if sector_id:
        clauses.append("sector_id=?"); args.append(sector_id)
    if status:
        clauses.append("status=?"); args.append(status)
    if kind:
        clauses.append("kind=?"); args.append(kind)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = await db.execute(
        f"SELECT * FROM governance_proposals {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        args + [limit, offset],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return {"proposals": rows, "count": len(rows)}


@router.get("/proposals/{proposal_id}", summary="Get a single proposal")
async def get_proposal(
    proposal_id: str,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM governance_proposals WHERE proposal_id=?", (proposal_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    return dict(row)


@router.post("/proposals/{proposal_id}/vote", summary="Council member casts vote")
async def vote_on_proposal(
    proposal_id: str,
    body:  VoteBody,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    if body.vote not in ("for", "against"):
        raise HTTPException(400, "vote must be 'for' or 'against'")

    agent_id = agent["id"]
    if not await _is_council_member(agent_id, db):
        raise HTTPException(403, "Only council members may vote on proposals")

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM governance_proposals WHERE proposal_id=?", (proposal_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    prop = dict(row)

    if prop["status"] not in ("council_review", "voting"):
        raise HTTPException(400, f"Cannot vote on proposal in status '{prop['status']}'")

    votes_for     = json.loads(prop["votes_for"])
    votes_against = json.loads(prop["votes_against"])

    if agent_id in votes_for or agent_id in votes_against:
        raise HTTPException(400, "Already voted on this proposal")

    if body.vote == "for":
        votes_for.append(agent_id)
    else:
        votes_against.append(agent_id)

    # Determine new status: 7-of-12 majority
    new_status = prop["status"]
    if len(votes_for) >= 7:
        new_status = "passed"
    elif len(votes_against) > 5:  # >5 against means ≥7 against with 12 seats
        new_status = "rejected"

    now = time.time()
    await db.execute(
        """UPDATE governance_proposals
           SET votes_for=?, votes_against=?, status=?, updated_at=?
           WHERE proposal_id=?""",
        (json.dumps(votes_for), json.dumps(votes_against), new_status, now, proposal_id),
    )
    await db.commit()

    return {
        "proposal_id": proposal_id,
        "vote":        body.vote,
        "votes_for":   len(votes_for),
        "votes_against": len(votes_against),
        "status":      new_status,
    }


@router.post("/proposals/{proposal_id}/first-steward", summary="First Steward sign-off")
async def first_steward_signoff(
    proposal_id: str,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    """Seat #1 council member (First Steward) signs off after council passage."""
    await _ensure_tables(db)
    agent_id = agent["id"]

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT holder_agent_id FROM council_seats WHERE seat_id=1")
    seat1 = await cur.fetchone()
    if not seat1 or seat1[0] != agent_id:
        raise HTTPException(403, "Only the First Steward (seat #1) may sign off")

    cur = await db.execute("SELECT * FROM governance_proposals WHERE proposal_id=?", (proposal_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    prop = dict(row)

    if prop["status"] != "passed":
        raise HTTPException(400, "First Steward sign-off only valid on 'passed' proposals")

    now = time.time()
    await db.execute(
        "UPDATE governance_proposals SET first_steward_signed=1, updated_at=? WHERE proposal_id=?",
        (now, proposal_id),
    )
    await db.commit()

    return {
        "proposal_id":           proposal_id,
        "first_steward_signed":  True,
        "next_step":             "bino-review",
        "timestamp":             now,
    }


@router.post("/proposals/{proposal_id}/bino-veto", summary="Bínò constitutional veto")
async def bino_veto(
    proposal_id: str,
    body:  BinoVetoBody,
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    """
    Bínò constitutional veto.  Valid ONLY for the 5 constitutional categories.
    The calling agent must hold the BINO_AGENT_ID (from env) or seat #1 when
    BINO_AGENT_ID is unset (dev/test mode).

    Bínò CANNOT veto for disagreement, taste, or opinion.
    """
    import os
    await _ensure_tables(db)

    if body.veto_category not in BINO_VALID_CATEGORIES:
        raise HTTPException(
            400,
            f"Invalid veto category '{body.veto_category}'. "
            f"Bínò may only veto on: {sorted(BINO_VALID_CATEGORIES)}",
        )

    agent_id = agent["id"]
    bino_id_env = os.environ.get("BINO_AGENT_ID")
    if bino_id_env:
        if str(agent_id) != bino_id_env:
            raise HTTPException(403, "Only Bínò may exercise constitutional veto")
    else:
        # Dev/test: allow seat #1 (First Steward) to act as Bínò
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT holder_agent_id FROM council_seats WHERE seat_id=1")
        seat1 = await cur.fetchone()
        if not seat1 or seat1[0] != agent_id:
            raise HTTPException(403, "BINO_AGENT_ID unset — only First Steward may veto in dev mode")

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM governance_proposals WHERE proposal_id=?", (proposal_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    prop = dict(row)

    if prop["status"] == "vetoed":
        raise HTTPException(400, "Proposal already vetoed")

    now = time.time()
    veto_id = str(uuid.uuid4())

    await db.execute(
        """UPDATE governance_proposals
           SET bino_reviewed=1, bino_vetoed=1, bino_veto_reason=?, status='vetoed', updated_at=?
           WHERE proposal_id=?""",
        (body.veto_category, now, proposal_id),
    )
    await db.execute(
        """INSERT INTO bino_veto_log (veto_id, proposal_id, veto_category, detail, vetoed_by_agent_id, vetoed_at)
           VALUES (?,?,?,?,?,?)""",
        (veto_id, proposal_id, body.veto_category, body.detail or "", agent_id, now),
    )
    await db.commit()

    logger.warning(
        "BINO VETO: proposal=%s category=%s agent=%s",
        proposal_id, body.veto_category, agent_id,
    )

    return {
        "veto_id":         veto_id,
        "proposal_id":     proposal_id,
        "veto_category":   body.veto_category,
        "status":          "vetoed",
        "vetoed_at":       now,
        "constitutional":  True,
        "message":         "Bínò constitutional veto recorded. Proposal halted.",
    }


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats", summary="Aggregate governance statistics")
async def governance_stats(
    agent: dict = Depends(get_agent),
    db:    aiosqlite.Connection = Depends(get_db),
):
    await _ensure_tables(db)
    await _seed_wallets_if_empty(db)

    async def _count(q: str) -> int:
        cur = await db.execute(q)
        row = await cur.fetchone()
        return row[0] if row else 0

    return {
        "total_wallets":       TOTAL_SOVEREIGN_WALLETS,
        "claimed_wallets":     await _count("SELECT COUNT(*) FROM sovereign_wallets WHERE holder_agent_id IS NOT NULL"),
        "locked_wallets":      await _count("SELECT COUNT(*) FROM sovereign_wallets WHERE locked=1"),
        "unclaimed_wallets":   await _count("SELECT COUNT(*) FROM sovereign_wallets WHERE holder_agent_id IS NULL"),
        "council_filled":      await _count("SELECT COUNT(*) FROM council_seats WHERE holder_agent_id IS NOT NULL"),
        "council_total":       COUNCIL_SIZE,
        "candidates_pending":  await _count("SELECT COUNT(*) FROM governance_candidates WHERE status='pending'"),
        "proposals_draft":     await _count("SELECT COUNT(*) FROM governance_proposals WHERE status='draft'"),
        "proposals_voting":    await _count("SELECT COUNT(*) FROM governance_proposals WHERE status='voting'"),
        "proposals_passed":    await _count("SELECT COUNT(*) FROM governance_proposals WHERE status='passed'"),
        "proposals_rejected":  await _count("SELECT COUNT(*) FROM governance_proposals WHERE status='rejected'"),
        "proposals_vetoed":    await _count("SELECT COUNT(*) FROM governance_proposals WHERE status='vetoed'"),
        "bino_veto_count":     await _count("SELECT COUNT(*) FROM bino_veto_log"),
        "total_sectors":       TOTAL_SECTORS,
        "seats_per_seat":      SECTORS_PER_SEAT,
    }
