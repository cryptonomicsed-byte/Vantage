"""Prediction-market scoring -- log-return "Prediction Value" method ported
from HKUDS/FutureShow. Lets any agent record a binary-market call (e.g. a
Polymarket YES/NO position) and, once the market resolves, score it against
market consensus rather than just win/loss."""
import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent, _parse_body
from ..prediction_value import compute_prediction_value, market_prob_for_call

router = APIRouter(prefix="/api/prediction-scores", tags=["trading"])


@router.post("")
async def record_call(request: Request, agent: dict = Depends(get_agent)):
    """Record a call on a binary market. Body: {market_slug, call ('YES'|'NO'),
    yes_prob?, no_prob?, order_id?, is_correct?}. If is_correct is supplied
    (e.g. importing an already-resolved market), the row is scored
    immediately; otherwise it's stored pending resolution."""
    body = await _parse_body(request)
    call = str(body.get("call", "")).strip().upper()
    if call not in ("YES", "NO"):
        raise HTTPException(422, "call must be 'YES' or 'NO'")

    market_slug = str(body.get("market_slug", ""))[:200]
    order_id = body.get("order_id")
    yes_prob = body.get("yes_prob")
    no_prob = body.get("no_prob")
    market_prob = market_prob_for_call(call, yes_prob, no_prob)
    if market_prob is None:
        raise HTTPException(422, "yes_prob or no_prob is required to resolve a market probability for this call")

    is_correct = body.get("is_correct")
    value = None
    resolved_at_sql = "NULL"
    if is_correct is not None:
        value = compute_prediction_value(call, market_prob, bool(is_correct))
        resolved_at_sql = "datetime('now')"

    async with get_db() as db:
        cur = await db.execute(
            f"""INSERT INTO prediction_scores
                (agent_id, order_id, market_slug, call, market_prob, is_correct, value, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, {resolved_at_sql})""",
            (agent["id"], order_id, market_slug, call, market_prob,
             int(bool(is_correct)) if is_correct is not None else None, value),
        )
        score_id = cur.lastrowid
        await db.commit()

    return {"id": score_id, "call": call, "market_prob": market_prob, "value": value,
            "status": "resolved" if is_correct is not None else "pending"}


@router.post("/{score_id}/resolve")
async def resolve_call(score_id: int, request: Request, agent: dict = Depends(get_agent)):
    """Mark a pending call as resolved once the market settles. Body: {is_correct: bool}."""
    body = await _parse_body(request)
    if "is_correct" not in body:
        raise HTTPException(422, "is_correct is required")
    is_correct = bool(body["is_correct"])

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM prediction_scores WHERE id=? AND agent_id=?", (score_id, agent["id"])
        )).fetchone()
        if not row:
            raise HTTPException(404, "Score not found")
        value = compute_prediction_value(row["call"], row["market_prob"], is_correct)
        await db.execute(
            "UPDATE prediction_scores SET is_correct=?, value=?, resolved_at=datetime('now') WHERE id=?",
            (int(is_correct), value, score_id),
        )
        await db.commit()

    return {"id": score_id, "is_correct": is_correct, "value": value}


@router.get("/me")
async def my_scores(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM prediction_scores WHERE agent_id=? ORDER BY created_at DESC LIMIT 200",
            (agent["id"],),
        )).fetchall()
    return [dict(r) for r in rows]


@router.get("/leaderboard")
async def leaderboard():
    """Public, like Vantage's other leaderboards. Ranked by average
    Prediction Value among resolved calls -- correctly calling a longshot
    ranks above correctly calling the favorite, unlike a plain win-rate."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT a.id AS agent_id, a.name,
                   COUNT(*) AS total,
                   SUM(ps.is_correct) AS correct,
                   AVG(ps.value) AS avg_value
            FROM prediction_scores ps
            JOIN agents a ON a.id = ps.agent_id
            WHERE ps.is_correct IS NOT NULL
            GROUP BY a.id
            HAVING total >= 1
            ORDER BY avg_value DESC
            LIMIT 100
        """)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["accuracy"] = (d["correct"] or 0) / d["total"] if d["total"] else 0.0
        out.append(d)
    return out
