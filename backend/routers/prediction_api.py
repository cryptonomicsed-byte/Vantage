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

import json
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
    source: Optional[str] = Query(None, description="kalshi | polymarket"),
    abstained: Optional[bool] = Query(None, description="true = skips only"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0),
    from_ts: Optional[int] = Query(None, description="epoch seconds, inclusive"),
    to_ts: Optional[int] = Query(None, description="epoch seconds, inclusive"),
    agent: dict = Depends(get_agent),
):
    """Decision feed, abstentions included -- that is the point of recording them.

    A skip is a decision, and 99.9% of this system's decisions are skips, so a
    feed that cannot filter down to them is a feed that cannot answer the only
    question worth asking. `abstained=true` is the audit view; `=false` is the
    (currently near-empty) view of calls actually made.
    """
    where, args = [], []
    if verdict:
        where.append("p.verdict = ?")
        args.append(verdict.strip().lower())
    if source:
        where.append("LOWER(s.source) = ?")
        args.append(source.strip().lower())
    if abstained is not None:
        where.append("COALESCE(p.abstained, 0) = ?")
        args.append(1 if abstained else 0)
    if min_confidence is not None:
        where.append("p.confidence >= ?")
        args.append(float(min_confidence))
    if from_ts is not None:
        where.append("p.predicted_ts >= ?")
        args.append(int(from_ts))
    if to_ts is not None:
        where.append("p.predicted_ts <= ?")
        args.append(int(to_ts))
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


# ── history ────────────────────────────────────────────────────────────────

_WINDOWS = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/calibration/history")
async def prediction_calibration_history(
    window: str = Query("30d", description="7d | 30d | 90d"),
    agent: dict = Depends(get_agent),
):
    """Daily series behind the gate, abstention-first.

    Abstentions dominate this system's behaviour (2,037 of 2,040 decisions), so
    the honest history is recorded/abstained/scored per day plus a Brier that is
    `null` on any day with no scored directional call. Returning 0.0 there would
    draw a perfect line through days that measured nothing -- the exact bug the
    gate itself was fixed for.

    `vacuous` is true when the whole window has zero scored calls, so the UI can
    say "no scored calls yet" instead of rendering a flat, healthy-looking zero.
    """
    from fastapi import HTTPException

    if window not in _WINDOWS:
        raise HTTPException(422, f"window must be one of {sorted(_WINDOWS)}")

    days = _WINDOWS[window]
    now = int(time.time())
    # Bucket on UTC day boundaries ending with today. A rolling `now - days*86400`
    # window starts mid-day, so the last bucket ended before today existed and a
    # decision made minutes ago fell off the end of the chart.
    today = (now // 86400) * 86400
    since = today - (days - 1) * 86400

    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT p.verdict, p.confidence, p.outcome,
                   COALESCE(p.abstained, 0) AS abstained, p.predicted_ts
              FROM brier_predictions p
             WHERE p.predicted_ts >= ?
             ORDER BY p.predicted_ts ASC
            """,
            (since,),
        )
        rows = _dicts(cur, await cur.fetchall())

    buckets: dict[str, dict] = {}
    for offset in range(days):
        day = time.strftime("%Y-%m-%d", time.gmtime(since + offset * 86400))
        buckets[day] = {"date": day, "recorded": 0, "abstained": 0,
                        "scored": 0, "brier": None, "_sq": 0.0}

    for row in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(row["predicted_ts"]))
        b = buckets.get(day)
        if b is None:
            continue
        b["recorded"] += 1
        if row["abstained"]:
            b["abstained"] += 1
            continue
        if row["outcome"] in ("yes", "no") and row["verdict"] in ("yes", "no"):
            b["scored"] += 1
            # Same convention as compute_rolling_brier: a "no" verdict predicts
            # (1 - confidence) for the YES outcome.
            pred = row["confidence"] if row["verdict"] == "yes" else 1.0 - row["confidence"]
            actual = 1.0 if row["outcome"] == "yes" else 0.0
            b["_sq"] += (pred - actual) ** 2

    series = []
    total_scored = total_recorded = total_abstained = 0
    sq_all = 0.0
    for day in sorted(buckets):
        b = buckets[day]
        scored = b.pop("scored")
        sq = b.pop("_sq", 0.0)
        b["scored"] = scored
        if scored:
            b["brier"] = round(sq / scored, 6)
            sq_all += sq
        total_scored += scored
        total_recorded += b["recorded"]
        total_abstained += b["abstained"]
        series.append(b)

    return {
        "window": window,
        "days": days,
        "series": series,
        "totals": {
            "recorded": total_recorded,
            "abstained": total_abstained,
            "scored": total_scored,
            "abstention_rate": round(total_abstained / total_recorded, 4) if total_recorded else None,
            "brier": round(sq_all / total_scored, 6) if total_scored else None,
        },
        "vacuous": total_scored == 0,
        "generated_at": now,
    }


# ── one market ─────────────────────────────────────────────────────────────

@router.get("/markets/{source}/{market_id}")
async def prediction_market_detail(
    source: str,
    market_id: str,
    agent: dict = Depends(get_agent),
):
    """One market: the latest signal, the decision this system made, and the
    outside evidence that matched it.

    Composite key on purpose. `market_id` alone is not unique across venues --
    947 Polymarket condition ids and 6,200 Kalshi tickers share one column -- so
    a bare `/markets/{id}` would resolve the wrong market the first time two
    venues ever disagreed about a string.
    """
    from fastapi import HTTPException

    src = source.strip().lower()
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT id, source, market_id, question, yes_price, no_price,
                   volume_24h, end_date, category, ingested_ts, processed, raw_json
              FROM prediction_signals
             WHERE LOWER(source) = ? AND market_id = ?
             ORDER BY ingested_ts DESC
             LIMIT 1
            """,
            (src, market_id),
        )
        rows = _dicts(cur, await cur.fetchall())
        if not rows:
            raise HTTPException(404, f"no {src} market {market_id!r}")
        market = rows[0]

        cur = await db.execute(
            """
            SELECT p.verdict, p.confidence, p.abstained, p.direction, p.conviction,
                   p.should_trade, p.reasoning, p.outcome, p.scored,
                   p.predicted_ts, p.resolved_ts
              FROM brier_predictions p
             WHERE p.signal_id = ?
             ORDER BY p.predicted_ts DESC
             LIMIT 1
            """,
            (market["id"],),
        )
        decision_rows = _dicts(cur, await cur.fetchall())

    context: list[dict] = []
    try:
        from ..signal_sources import store as context_store
        context = await context_store.matching(market["question"] or "", limit=3)
    except Exception:
        context = []

    venue_url = None
    if src == "kalshi":
        venue_url = f"https://kalshi.com/markets/{market_id.lower()}"
    elif src == "polymarket":
        venue_url = f"https://polymarket.com/market/{market_id}"

    # The venue's own payload, for the drawer. Parsed here rather than shipped as
    # a JSON string so the client can render it as a tree; the raw text length is
    # reported too, because a 40KB blob is worth warning about before expanding.
    raw_text = market.pop("raw_json", None) or "{}"
    try:
        raw = json.loads(raw_text)
    except (TypeError, ValueError):
        raw = {"unparsed": raw_text[:2000]}

    return {
        "market": market,
        "decision": decision_rows[0] if decision_rows else None,
        "context": context,
        "venue_url": venue_url,
        "raw": raw,
        "raw_bytes": len(raw_text),
        "generated_at": int(time.time()),
    }


# ── health ─────────────────────────────────────────────────────────────────

@router.get("/health")
async def prediction_health(agent: dict = Depends(get_agent)):
    """Collector liveness, per source, with the failure named rather than implied.

    `status` is derived, not decorative:
      ok        — ingested within 2x its poll interval and has open markets
      stale     — ingesting, but nothing open (the archived-feed failure)
      silent    — no ingest within 2x its poll interval
    """
    now = int(time.time())
    now_iso = _iso(now)
    # Matches each collector's POLL_INTERVAL; 2x is the tolerance before a feed
    # is called late (one missed poll is normal, two is a signal).
    expected = {"kalshi": 900, "polymarket": 900}

    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT LOWER(COALESCE(NULLIF(s.source, ''), 'unknown')) AS source,
                   COUNT(*) AS signals,
                   SUM(CASE WHEN s.processed = 0 THEN 1 ELSE 0 END) AS unprocessed,
                   SUM(CASE WHEN COALESCE(s.end_date, '') > ? THEN 1 ELSE 0 END) AS open_markets,
                   SUM(CASE WHEN COALESCE(s.end_date, '') <= ? OR s.end_date IS NULL
                            THEN 1 ELSE 0 END) AS archived_markets,
                   MAX(s.ingested_ts) AS last_ingest
              FROM prediction_signals s
             GROUP BY source
             ORDER BY signals DESC
            """,
            (now_iso, now_iso),
        )
        base = _dicts(cur, await cur.fetchall())

    sources = []
    for row in base:
        last = row.get("last_ingest") or 0
        age = (now - last) if last else None
        interval = expected.get(row["source"])
        if interval is None:
            status = "untracked"
        elif age is None or age > interval * 2:
            status = "silent"
        elif (row.get("open_markets") or 0) == 0:
            status = "stale"
        else:
            status = "ok"
        row["age_seconds"] = age
        row["expected_interval_seconds"] = interval
        row["open_ratio"] = (
            round((row.get("open_markets") or 0) / row["signals"], 4) if row["signals"] else None
        )
        row["status"] = status
        sources.append(row)

    return {"sources": sources, "generated_at": now}


# ── forecast (Mycelium) ────────────────────────────────────────────────────

@router.get("/forecast")
async def prediction_forecast(
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    """Forecasts recorded in the Mycelium substrate, not invented here.

    Mycelium's `predictions` table is the real store: question, probability,
    resolution_date, resolution_criterion, status, outcome, plus its own
    grading loop. TimesFM is not wired and is not the same thing -- it forecasts
    trace buckets, not markets -- and `vantage_predictor` is a trading engine, so
    neither belongs in a read-only forecast tab.

    Fail-soft: an unreachable gateway returns available=false with a reason; it
    never blocks the tab, and it never fabricates a forecast.
    """
    import os

    import httpx

    base = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"{base}/api/predictions", params={"limit": limit})
        if r.status_code != 200:
            return {"available": False, "predictions": [], "count": 0,
                    "reason": f"gateway returned HTTP {r.status_code}", "generated_at": int(time.time())}
        payload = r.json()
    except Exception as exc:
        return {"available": False, "predictions": [], "count": 0,
                "reason": f"gateway unreachable: {type(exc).__name__}", "generated_at": int(time.time())}

    rows = payload if isinstance(payload, list) else (payload.get("predictions") or [])
    return {
        "available": True,
        "count": len(rows),
        "predictions": rows,
        "gateway": base,
        "note": "Mycelium records these; Vantage does not generate them and does not settle them.",
        "generated_at": int(time.time()),
    }
