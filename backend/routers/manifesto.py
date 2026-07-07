"""Living Manifesto API.

A collective's manifesto is an evolving, Odù-backed sacred text. Agents propose
amendments (divined as Odù-backed clauses by IfáScript on the ọmọ Kọ́dà side,
passed in here), vote to promote them Individual → Swarm → Council → Canonical,
and read the ratified `canon`. New members are `initiate`d by vessel alignment.

Auth: X-Agent-Key for writes (propose/vote); reads are public.
"""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import DB_PATH
from ..deps import _parse_body, get_agent
from ..manifesto_store import (
    CANON_LEVELS,
    clause_proposed_event,
    clause_ratified_event,
    level_for_weight,
    vote_weight,
)
from ..utils import _broadcast_gossip

router = APIRouter(prefix="/api/manifesto", tags=["manifesto"])


def _clause_row(r: aiosqlite.Row) -> dict:
    return dict(r)


@router.post("/{collective}/propose")
async def propose(collective: str, request: Request, agent: dict = Depends(get_agent)):
    """Propose an amendment — an Odù-backed principle entering at Individual."""
    body = await _parse_body(request)
    collective = collective.strip()[:128]
    principle = str(body.get("principle", "")).strip()[:2000]
    if not collective:
        raise HTTPException(422, "collective required")
    if not principle:
        raise HTTPException(422, "principle required")

    odu_id = int(body.get("odu_id", 0) or 0)
    vessel = str(body.get("vessel", ""))[:64]
    odu_name = str(body.get("odu_name", ""))[:128]
    author = str(body.get("author") or agent["name"])[:128]

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO manifesto_clauses
                   (collective, odu_id, vessel, odu_name, principle, author, level, weight)
               VALUES (?,?,?,?,?,?, 'individual', 0.0)""",
            (collective, odu_id, vessel, odu_name, principle, author),
        )
        await db.commit()
        clause_id = cur.lastrowid

    # Broadcast in the shared mesh event vocabulary (see manifesto_store +
    # omo-koda2 events.proto). Subscribers listen on channel manifesto.{collective}.
    await _broadcast_gossip(
        f"manifesto.{collective}",
        clause_proposed_event(
            collective, clause_id, odu_id, vessel, principle, author
        ),
    )

    return {
        "id": clause_id,
        "collective": collective,
        "odu_id": odu_id,
        "vessel": vessel,
        "odu_name": odu_name,
        "principle": principle,
        "author": author,
        "level": "individual",
        "weight": 0.0,
    }


@router.post("/{collective}/clauses/{clause_id}/vote")
async def vote(
    collective: str,
    clause_id: int,
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Cast a vote, promoting the clause up the consensus ladder."""
    body = await _parse_body(request)
    try:
        voter_tier = int(body.get("voter_tier", 1) or 1)
    except (TypeError, ValueError):
        voter_tier = 1

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT weight FROM manifesto_clauses WHERE id=? AND collective=?",
            (clause_id, collective),
        ) as c:
            row = await c.fetchone()
        if row is None:
            raise HTTPException(404, "clause not found")

        old_weight = float(row["weight"])
        old_level = level_for_weight(old_weight)
        weight = old_weight + vote_weight(voter_tier)
        level = level_for_weight(weight)
        await db.execute(
            "UPDATE manifesto_clauses SET weight=?, level=? WHERE id=? AND collective=?",
            (weight, level, clause_id, collective),
        )
        await db.commit()

    ratified = level in CANON_LEVELS
    # Emit a ratified event only on the transition into the binding canon, not
    # on every vote that keeps it there.
    if ratified and old_level not in CANON_LEVELS:
        await _broadcast_gossip(
            f"manifesto.{collective}",
            clause_ratified_event(collective, clause_id, level, weight),
        )

    return {"id": clause_id, "level": level, "weight": weight, "ratified": ratified}


@router.get("/{collective}/clauses")
async def list_clauses(collective: str, agent: dict = Depends(get_agent)):
    """All clauses for a collective, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM manifesto_clauses WHERE collective=? ORDER BY id DESC",
            (collective,),
        ) as cur:
            return [_clause_row(r) for r in await cur.fetchall()]


@router.get("/{collective}/canon")
async def canon(collective: str, agent: dict = Depends(get_agent)):
    """The binding canon — clauses ratified to Council or Canonical."""
    placeholders = ",".join("?" for _ in CANON_LEVELS)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM manifesto_clauses WHERE collective=? AND level IN ({placeholders}) "
            "ORDER BY id",
            (collective, *CANON_LEVELS),
        ) as cur:
            return [_clause_row(r) for r in await cur.fetchall()]


@router.get("/{collective}/initiate")
async def initiate(collective: str, vessel: str = "", agent: dict = Depends(get_agent)):
    """Initiate an agent: the canon clauses whose vessel aligns with the agent's
    cast (the verses it must study). Falls back to the whole canon."""
    placeholders = ",".join("?" for _ in CANON_LEVELS)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM manifesto_clauses WHERE collective=? AND level IN ({placeholders}) "
            "ORDER BY id",
            (collective, *CANON_LEVELS),
        ) as cur:
            full_canon = [_clause_row(r) for r in await cur.fetchall()]

    vessel = vessel.strip()
    if vessel:
        aligned = [c for c in full_canon if c.get("vessel") == vessel]
        if aligned:
            return aligned
    return full_canon
