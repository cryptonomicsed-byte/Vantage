"""Persistence and matching for context signals.

One row per (source, market_id): these are "what is true now", not a time
series. The table stays small on purpose -- a decision only ever needs the
current read, and keeping history here would grow without a reader.

Matching is lexical and deliberately dumb: shared significant tokens between a
market question and a context item. It will miss paraphrase and it will
occasionally over-match; both are visible in the stored match_score, which is
better than an opaque embedding score nobody can argue with at 3am.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

try:
    from ..db import get_db
except ImportError:  # backend/ on sys.path
    from db import get_db

_STOP = {
    "will", "would", "could", "should", "the", "and", "for", "with", "that",
    "this", "from", "into", "than", "then", "there", "their", "have", "has",
    "been", "before", "after", "above", "below", "over", "under", "about",
    "market", "price", "does", "what", "when", "which", "who", "how", "many",
    "much", "more", "most", "any", "all", "not", "yes", "no", "win", "wins",
    "2025", "2026", "2027",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    # 3-char floor, not 4: "cpi", "wti", "gdp" are the subjects of the very
    # questions this matching exists for, and dropping them made "Will CPI
    # inflation..." match only on the generic half of the phrase.
    return {w for w in words if len(w) > 2 and w not in _STOP}


async def upsert(items: list[dict[str, Any]]) -> int:
    """Write normalized signals, replacing the previous read for each key."""
    if not items:
        return 0
    written = 0
    async with get_db() as db:
        for it in items:
            await db.execute(
                """
                INSERT INTO context_signals
                    (source, market_id, signal_type, observed_ts, confidence, direction, payload_json)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(source, market_id) DO UPDATE SET
                    signal_type = excluded.signal_type,
                    observed_ts = excluded.observed_ts,
                    confidence  = excluded.confidence,
                    direction   = excluded.direction,
                    payload_json= excluded.payload_json
                """,
                (
                    it["source"],
                    it["market_id"],
                    it["signal_type"],
                    int(time.time()),
                    float(it["confidence"]),
                    it["direction"],
                    json.dumps(it["payload"], default=str),
                ),
            )
            written += 1
        await db.commit()
    return written


def _row(cur_row, names) -> dict[str, Any]:
    d = dict(zip(names, cur_row))
    try:
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
    except (ValueError, TypeError):
        d["payload"] = {}
    return d


async def latest(
    limit: int = 50,
    source: Optional[str] = None,
    signal_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    where, args = [], []
    if source:
        where.append("source = ?")
        args.append(source)
    if signal_type:
        where.append("signal_type = ?")
        args.append(signal_type)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    async with get_db() as db:
        cur = await db.execute(
            f"""SELECT source, market_id, signal_type, observed_ts, confidence,
                       direction, payload_json
                  FROM context_signals {clause}
                 ORDER BY observed_ts DESC, confidence DESC
                 LIMIT ?""",
            (*args, limit),
        )
        names = [d[0] for d in (cur.description or [])]
        return [_row(r, names) for r in await cur.fetchall()]


async def counts_by_source() -> list[dict[str, Any]]:
    async with get_db() as db:
        cur = await db.execute(
            """SELECT source, signal_type, COUNT(*) AS n, MAX(observed_ts) AS last_seen
                 FROM context_signals GROUP BY source, signal_type ORDER BY n DESC"""
        )
        names = [d[0] for d in (cur.description or [])]
        return [dict(zip(names, r)) for r in await cur.fetchall()]


async def matching(question: str, limit: int = 3, scan: int = 400) -> list[dict[str, Any]]:
    """Context items that share significant tokens with a market question."""
    if not question:
        return []
    want = _tokens(question)
    if not want:
        return []

    rows = await latest(limit=scan)
    scored = []
    for row in rows:
        payload = row.get("payload") or {}
        haystack = " ".join(str(v) for v in (
            row.get("market_id"),
            payload.get("label"),
            payload.get("question"),
            payload.get("city"),
            payload.get("title"),
        ) if v)
        have = _tokens(haystack)
        shared = want & have
        if not shared:
            continue
        # Two shared tokens, or one that is at least 6 chars (a domain word like
        # "bitcoin" carries more than "rate" does).
        if len(shared) < 2 and not any(len(t) >= 6 for t in shared):
            continue
        row = dict(row)
        row["match_score"] = len(shared)
        row["matched_on"] = sorted(shared)[:6]
        scored.append(row)

    scored.sort(key=lambda r: (r["match_score"], r["confidence"]), reverse=True)
    return scored[:limit]
