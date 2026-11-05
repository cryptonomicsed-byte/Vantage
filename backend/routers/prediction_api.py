"""Prediction-market read surface for the Trading → Prediction tab.

Why a dedicated router instead of more endpoints on trading.py: everything on
that tab is read-only telemetry over three tables (`prediction_signals`,
`brier_predictions`, `prediction_resolutions`) plus the calibration gate's own
counts. None of it touches wallets, orders or strategies, and putting it in
trading.py would bury a 900-line file under another 200 lines of SELECTs.

Auth: agent key (same as the rest of /api/trading). Market data is public, but
the decisions and reasoning in `brier_predictions` are this system's, not the
venue's -- and every other read surface in the app is keyed, so an unkeyed one
here would be the odd hole rather than the convenience.

The shapes are deliberately flat dicts: the frontend table renders them
straight through, and a Pydantic model per endpoint would only re-state the
column names.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query

from .. import brier_calibration_gate as gate
from ..db import get_db
from ..deps import get_agent

router = APIRouter(prefix="/api/trading/prediction", tags=["prediction-markets"])


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _dicts(cur, rows) -> list[dict]:
    names = [d[0] for d in (cur.description or [])]
    return [dict(zip(names, r)) for r in rows]


async def _one(db, sql: str, args: tuple = ()):
    cur = await db.execute(sql, args)
    row = await cur.fetchone()
    return row[0] if row else 0


@router.get("/overview")
async def prediction_overview(agent: dict = Depends(get_agent)):
    """Counts behind the tab's header: feed volume, decision mix, gate state."""
    now = int(time.time())
    day_ago = now - 86400

    async with get_db() as db:
        signals_total = await _one(db, "SELECT COUNT(*) FROM prediction_signals")
        signals_24h = await _one(
            db, "SELECT COUNT(*) FROM prediction_signals WHERE ingested_ts >= ?", (day_ago,)
        )
        unprocessed = await _one(
            db, "SELECT COUNT(*) FROM prediction_signals WHERE processed = 0"
        )
        cur = await db.execute(
            "SELECT COALESCE(NULLIF(LOWER(source), ''), 'unknown') AS s, COUNT(*) "
            "FROM prediction_signals GROUP BY s ORDER BY 2 DESC"
        )
        by_source = [{"source": r[0], "signals": r[1]} for r in await cur.fetchall()]

        decisions_total = await _one(db, "SELECT COUNT(*) FROM brier_predictions")
        decisions_24h = await _one(
            db, "SELECT COUNT(*) FROM brier_predictions WHERE predicted_ts >= ?", (day_ago,)
        )
        cur = await db.execute(
            "SELECT verdict, COUNT(*) FROM brier_predictions GROUP BY verdict ORDER BY 2 DESC"
        )
        verdicts = {r[0]: r[1] for r in await cur.fetchall()}
        abstentions = await _one(
            db, "SELECT COUNT(*) FROM brier_predictions WHERE COALESCE(abstained, 0) = 1"
        )
        avg_conf = await _one(
            db, "SELECT AVG(confidence) FROM brier_predictions WHERE predicted_ts >= ?",
            (day_ago,),
        )

        open_now = await _one(
            db,
            "SELECT COUNT(*) FROM prediction_signals "
            "WHERE COALESCE(end_date, '') > ? ",
            (_iso(now),),
        )
        closing_soon = await _one(
            db,
            "SELECT COUNT(*) FROM prediction_signals "
            "WHERE COALESCE(end_date, '') > ? AND COALESCE(end_date, '') <= ?",
            (_iso(now), _iso(now + 86400)),
        )
        cur = await db.execute(
            "SELECT MIN(end_date) FROM prediction_signals WHERE COALESCE(end_date, '') > ?",
            (_iso(now),),
        )
        row = await cur.fetchone()
        next_close = row[0] if row else None

    stats = await gate.brier_stats()

    return {
        "generated_at": now,
        "signals": {
            "total": signals_total,
            "last_24h": signals_24h,
            "unprocessed": unprocessed,
            "by_source": by_source,
        },
        "decisions": {
            "total": decisions_total,
            "last_24h": decisions_24h,
            "verdicts": verdicts,
            "abstentions": abstentions,
            "directional": max(0, decisions_total - abstentions),
            "avg_confidence_24h": round(float(avg_conf or 0.0), 4),
        },
        "calibration": stats,
        "markets": {
            "open_now": open_now,
            "closing_next_24h": closing_soon,
            "next_close": next_close,
        },
    }


@router.get("/markets")
async def prediction_markets(
    limit: int = Query(40, ge=1, le=200),
    source: Optional[str] = Query(None, description="polymarket | kalshi | limitless"),
    open_only: bool = Query(False, description="only markets whose end_date is in the future"),
    decided_only: bool = Query(False, description="only markets this system has decided on"),
    q: Optional[str] = Query(None, description="substring match on the question"),
    agent: dict = Depends(get_agent),
):
    """The feed itself: one row per market, with this system's decision attached.

    Deduped by market_id because the collectors re-ingest the same market every
    cycle -- without the fold, the tab would show the same question four times
    with four different decision timestamps and none of them the newest.
    """
    now_iso = _iso(int(time.time()))
    where = []
    args: list = []
    if source:
        where.append("LOWER(s.source) = ?")
        args.append(source.strip().lower())
    if open_only:
        where.append("COALESCE(s.end_date, '') > ?")
        args.append(now_iso)
    if decided_only:
        where.append("p.signal_id IS NOT NULL")
    if q:
        where.append("LOWER(COALESCE(s.question, '')) LIKE ?")
        args.append(f"%{q.strip().lower()}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    # Over-fetch, then fold duplicates down to `limit` distinct markets.
    fetch = min(200 * 4, max(200, limit * 6))
    # Open markets are ordered by how soon they settle: "what closes next" is
    # the question this tab exists to answer, and ingest order only says which
    # collector ran last. Closed history keeps newest-first.
    order = "s.end_date ASC" if open_only else "s.ingested_ts DESC"
    async with get_db() as db:
        cur = await db.execute(
            f"""
            SELECT s.id, s.source, s.market_id, s.question, s.yes_price, s.no_price,
                   s.volume_24h, s.end_date, s.category, s.ingested_ts, s.processed,
                   p.verdict, p.confidence, p.direction, p.conviction, p.should_trade,
                   p.reasoning, p.outcome, p.scored, p.abstained, p.predicted_ts
              FROM prediction_signals s
              LEFT JOIN brier_predictions p ON p.signal_id = s.id
              {clause}
             ORDER BY {order}
             LIMIT ?
            """,
            (*args, fetch),
        )
        rows = _dicts(cur, await cur.fetchall())

    seen: set[str] = set()
    markets: list[dict] = []
    for r in rows:
        mid = r.get("market_id") or r.get("id")
        if mid in seen:
            continue
        seen.add(mid)

        def _price(v):
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        yv, nv = _price(r.get("yes_price")), _price(r.get("no_price"))
        # 0.0/0.0 is the Kalshi collector's "no price published", not a market
        # trading at zero. Rendering it as 0% would claim certainty the venue
        # never stated.
        if yv == 0.0 and nv == 0.0:
            yv, nv = None, None
        r["implied_yes"] = round(yv, 4) if yv is not None else None
        r["implied_no"] = round(nv, 4) if nv is not None else None
        r["settled"] = r.get("outcome") in ("yes", "no", "void")
        markets.append(r)
        if len(markets) >= limit:
            break

    return {"count": len(markets), "markets": markets, "generated_at": int(time.time())}


@router.get("/decisions")
async def prediction_decisions(
    limit: int = Query(50, ge=1, le=200),
    verdict: Optional[str] = Query(None, description="yes | no | skip"),
    agent: dict = Depends(get_agent),
):
    """Decision feed, abstentions included -- that is the point of recording them."""
    where, args = [], []
    if verdict:
        where.append("p.verdict = ?")
        args.append(verdict.strip().lower())
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    async with get_db() as db:
        cur = await db.execute(
            f"""
            SELECT p.signal_id, p.verdict, p.confidence, p.abstained, p.direction,
                   p.conviction, p.should_trade, p.market_id AS decision_market_id,
                   p.reasoning, p.outcome, p.scored, p.predicted_ts, p.resolved_ts,
                   s.question, s.source, s.market_id, s.end_date, s.yes_price
              FROM brier_predictions p
              LEFT JOIN prediction_signals s ON s.id = p.signal_id
              {clause}
             ORDER BY p.predicted_ts DESC
             LIMIT ?
            """,
            (*args, limit),
        )
        rows = _dicts(cur, await cur.fetchall())
    return {"count": len(rows), "decisions": rows, "generated_at": int(time.time())}


@router.get("/calibration")
async def prediction_calibration(agent: dict = Depends(get_agent)):
    """The gate, in numbers: score, the counts behind it, and recent settlements.

    `vacuous` is the field that matters. A Brier of 0.000 over zero scored
    predictions is not calibration -- the gate never measured anything, and the
    UI must not paint that as a healthy 0.
    """
    stats = await gate.brier_stats()

    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT p.signal_id, p.verdict, p.confidence, p.abstained, p.outcome,
                   p.predicted_ts, p.resolved_ts, s.question, s.source
              FROM brier_predictions p
              LEFT JOIN prediction_signals s ON s.id = p.signal_id
             WHERE p.scored = 1
             ORDER BY (p.resolved_ts IS NULL), p.resolved_ts DESC, p.predicted_ts DESC
             LIMIT 20
            """
        )
        recent = _dicts(cur, await cur.fetchall())

        # Audience for the abstention rule: of the skips that have since
        # settled, how many would have been right? A skip is not scored, but
        # it is not invisible either.
        cur = await db.execute(
            """
            SELECT p.outcome, COUNT(*) FROM brier_predictions p
             WHERE COALESCE(p.abstained, 0) = 1 AND p.outcome IN ('yes', 'no')
             GROUP BY p.outcome
            """
        )
        skip_settled = {r[0]: r[1] for r in await cur.fetchall()}

        cur = await db.execute(
            "SELECT COUNT(*) FROM brier_predictions "
            "WHERE scored = 0 AND outcome IS NULL"
        )
        row = await cur.fetchone()
        pending = row[0] if row else 0

    return {
        "stats": stats,
        "recent_resolutions": recent,
        "abstention_audit": {
            "settled_yes": skip_settled.get("yes", 0),
            "settled_no": skip_settled.get("no", 0),
            "note": "abstentions are excluded from the Brier score; these counts "
                    "are how a skipping strategy is judged",
        },
        "pending_resolution": pending,
        "generated_at": int(time.time()),
    }


@router.get("/context")
async def prediction_context(
    limit: int = Query(40, ge=1, le=200),
    source: Optional[str] = Query(None, description="fred | open-meteo | manifold"),
    signal_type: Optional[str] = Query(None, description="macro | weather | community"),
    agent: dict = Depends(get_agent),
):
    """Non-venue evidence: macro prints, forecasts, community probabilities.

    These never settle a market — the venue does. They are what the decision
    layer reads before it speaks, and they are shown here so a decision's
    evidence is inspectable rather than implied.
    """
    from ..signal_sources import CADENCE, store

    rows = await store.latest(limit=limit, source=source, signal_type=signal_type)
    counts = await store.counts_by_source()
    return {
        "count": len(rows),
        "signals": rows,
        "by_source": counts,
        "cadence_seconds": CADENCE,
        "generated_at": int(time.time()),
    }


@router.get("/sources")
async def prediction_sources(agent: dict = Depends(get_agent)):
    """Per-venue health: ingest rate, decision coverage, resolution coverage.

    `open_markets` is the field that catches a dead feed. A collector can keep
    ingesting for weeks while every row is an archived market -- the totals look
    healthy and the tab is full of 2024 questions. Zero open markets against a
    large signal count is that failure, and it belongs on screen.
    """
    now_iso = _iso(int(time.time()))
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT LOWER(COALESCE(NULLIF(s.source, ''), 'unknown')) AS source,
                   COUNT(*) AS signals,
                   SUM(CASE WHEN s.processed = 0 THEN 1 ELSE 0 END) AS unprocessed,
                   SUM(CASE WHEN COALESCE(s.end_date, '') > ? THEN 1 ELSE 0 END) AS open_markets,
                   MAX(s.ingested_ts) AS last_ingest
              FROM prediction_signals s
             GROUP BY source
             ORDER BY signals DESC
            """,
            (now_iso,),
        )
        base = _dicts(cur, await cur.fetchall())

        cur = await db.execute(
            """
            SELECT LOWER(COALESCE(NULLIF(s.source, ''), 'unknown')) AS source,
                   COUNT(*) AS decisions,
                   SUM(CASE WHEN COALESCE(p.abstained, 0) = 1 THEN 1 ELSE 0 END) AS abstentions,
                   SUM(CASE WHEN p.outcome IN ('yes', 'no') THEN 1 ELSE 0 END) AS resolved,
                   SUM(CASE WHEN p.scored = 0 AND p.outcome IS NULL THEN 1 ELSE 0 END) AS pending
              FROM brier_predictions p
              JOIN prediction_signals s ON s.id = p.signal_id
             GROUP BY source
            """
        )
        decision_map = {r["source"]: r for r in _dicts(cur, await cur.fetchall())}

    for row in base:
        d = decision_map.get(row["source"], {})
        row["decisions"] = d.get("decisions") or 0
        row["abstentions"] = d.get("abstentions") or 0
        row["resolved"] = d.get("resolved") or 0
        row["pending"] = d.get("pending") or 0
        row["resolvable"] = row["source"] in gate.RESOLVABLE_VENUES

    return {"sources": base, "resolvable_venues": list(gate.RESOLVABLE_VENUES),
            "generated_at": int(time.time())}
