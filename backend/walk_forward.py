"""P4-16 -- walk-forward report: would the shadow rank (and the TradingOS
challenger's vetoes) have done better than live, using only what was known
at the time?

For each UTC day D in the report range:
  train  = resolver marks (Mycelium signal_outcome, one window) whose
           marked_at is BEFORE D -> per-source reliability
           (hits + 15) / (n + 30), the shadow-rank prior (P2-05)
  test   = ledger calls created on D that the resolver has scored
           (long/short only; hold/exit/alert are not scored)
  live   = test calls ordered by conviction (the live rank)
  shadow = ordered by conviction x reliability
  metric = hit rate of the top K calls each way, plus the day's base rate

Nothing from D or later leaks into D's reliabilities. Days with no scored
calls are listed with n=0, not dropped, so a thin report looks thin.

Challenger (P4-15): a challenged call is matched to its ledger record by
source + direction + (mint, else symbol) within 2 h; reported are the hit
rates of calls it would have vetoed vs not. Below MIN_N matched calls the
rates are null.

Read-only: computed on request (cached 10 min); writes nothing.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional
from backend.signal_record import canonical_direction

PRIOR_HITS, PRIOR_N = 15.0, 30.0
MIN_N = 10
MATCH_S = 2 * 3600
_CACHE: Dict[str, Any] = {}
CACHE_S = 600


def _iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def fetch_marks(since_ts: int, window: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """record id -> {hit, scored, marked_at}; None if Mycelium is unreachable."""
    from backend.mycelium_bridge import MYCELIUM_URL
    q = urllib.parse.urlencode({"agent": "vantage_ledger", "action": "signal_outcome",
                                "since": _iso(since_ts), "limit": 20000})
    try:
        with urllib.request.urlopen(f"{MYCELIUM_URL}/api/traces?{q}", timeout=15) as r:
            body = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for t in body.get("traces") or []:
        p = t.get("payload") if isinstance(t, dict) else None
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                continue
        if isinstance(p, dict) and p.get("window") == window and t.get("target"):
            m = p.get("marked_at")
            if isinstance(m, str):
                try:
                    from datetime import datetime
                    m = int(datetime.fromisoformat(m.replace("Z", "+00:00")).timestamp())
                except ValueError:
                    m = None
            out.setdefault(t["target"], {"hit": p.get("hit"), "scored": p.get("scored", True),
                                         "marked_at": m if isinstance(m, (int, float)) else None})
    return out


def _scored(mark: Optional[Dict[str, Any]]) -> bool:
    return bool(mark) and mark.get("scored") is not False and isinstance(mark.get("hit"), bool)


def compute(calls: List[Dict[str, Any]], marks: Dict[str, Dict[str, Any]], start: int, days: int, k: int,
            challenges: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Pure. calls: {id, source, direction, conviction, created_at, mint, symbol}."""
    for c in calls:
        c["direction"] = canonical_direction(c.get("direction"))
    calls = [c for c in calls if c["direction"] in ("long", "short")]
    by_id = {c["id"]: c for c in calls}
    resolved = [(marks[i]["marked_at"], by_id[i]["source"], marks[i]["hit"])
                for i in marks if i in by_id and _scored(marks[i]) and marks[i].get("marked_at")]
    rows, tot = [], {"n": 0, "live_hits": 0, "shadow_hits": 0, "k_n": 0, "base_hits": 0}
    for d in range(days):
        d0 = start + d * 86400
        d1 = d0 + 86400
        hits = defaultdict(float)
        n = defaultdict(int)
        for marked_at, src, hit in resolved:
            if marked_at < d0:          # known before the day starts -- nothing later leaks in
                n[src] += 1
                hits[src] += 1.0 if hit else 0.0
        test = [c for c in calls if d0 <= (c.get("created_at") or 0) < d1 and _scored(marks.get(c["id"]))]

        def rel(src):
            return (hits[src] + PRIOR_HITS) / (n[src] + PRIOR_N)
        live = sorted(test, key=lambda c: -(c.get("conviction") or 0))
        shadow = sorted(test, key=lambda c: -((c.get("conviction") or 0) * rel(c["source"])))
        kk = min(k, len(test))
        lh = sum(1 for c in live[:kk] if marks[c["id"]]["hit"])
        sh = sum(1 for c in shadow[:kk] if marks[c["id"]]["hit"])
        bh = sum(1 for c in test if marks[c["id"]]["hit"])
        rows.append({"day": time.strftime("%Y-%m-%d", time.gmtime(d0)), "n": len(test), "k": kk,
                     "live_top_k_hit_rate": (lh / kk) if kk else None,
                     "shadow_top_k_hit_rate": (sh / kk) if kk else None,
                     "base_hit_rate": (bh / len(test)) if test else None,
                     "sources_with_history": sum(1 for s in n if n[s])})
        tot["n"] += len(test)
        tot["k_n"] += kk
        tot["live_hits"] += lh
        tot["shadow_hits"] += sh
        tot["base_hits"] += bh
    summary = {"calls_scored": tot["n"], "top_k_slots": tot["k_n"],
               "live_top_k_hit_rate": (tot["live_hits"] / tot["k_n"]) if tot["k_n"] else None,
               "shadow_top_k_hit_rate": (tot["shadow_hits"] / tot["k_n"]) if tot["k_n"] else None,
               "base_hit_rate": (tot["base_hits"] / tot["n"]) if tot["n"] else None,
               "enough_data": tot["k_n"] >= MIN_N}
    return {"days": rows, "summary": summary, "challenger": _challenger(calls, marks, challenges or [])}


def _challenger(calls, marks, challenges) -> Dict[str, Any]:
    idx = defaultdict(list)
    for c in calls:
        if _scored(marks.get(c["id"])):
            key = (c.get("source"), (c.get("direction") or "").lower(), c.get("mint") or (c.get("symbol") or "").upper())
            idx[key].append(c)
    veto, keep = [], []
    for ch in challenges:
        if ch.get("would_veto") is None:
            continue
        key = (ch.get("source"), canonical_direction(ch.get("direction")),
               ch.get("mint") or (ch.get("symbol") or "").upper())
        best = None
        for c in idx.get(key, []):
            dt = abs((c.get("created_at") or 0) - (ch.get("call_ts") or 0))
            if dt <= MATCH_S and (best is None or dt < best[0]):
                best = (dt, c)
        if best:
            (veto if ch["would_veto"] else keep).append(bool(marks[best[1]["id"]]["hit"]))

    def rate(xs):
        return (sum(xs) / len(xs)) if len(xs) >= MIN_N else None
    return {"matched": len(veto) + len(keep), "vetoed": len(veto), "kept": len(keep),
            "vetoed_hit_rate": rate(veto), "kept_hit_rate": rate(keep), "min_n": MIN_N}


async def report(days: int = 30, k: int = 5, window: str = "24h", now: Optional[float] = None,
                 fetch: Callable[[int, str], Optional[Dict[str, Dict[str, Any]]]] = fetch_marks) -> Dict[str, Any]:
    days = max(1, min(int(days), 120))
    k = max(1, min(int(k), 50))
    window = window if window in ("1h", "6h", "24h") else "24h"
    ck = f"{days}|{k}|{window}"
    if now is None and ck in _CACHE and time.time() - _CACHE[ck][0] < CACHE_S:
        return _CACHE[ck][1]
    now_i = int(now or time.time())
    today = now_i - now_i % 86400
    start = today - (days - 1) * 86400
    history_from = start - 120 * 86400
    marks = await asyncio.to_thread(fetch, history_from, window)
    from backend.db import get_db
    from backend.signal_ledger import ensure_tables
    async with get_db() as db:
        await ensure_tables(db)
        cols = ["id", "source", "direction", "conviction", "created_at", "address", "symbol"]
        calls = [dict(zip(cols, r)) for r in await (await db.execute(
            "SELECT id, source, direction, conviction, COALESCE(created_at, ingested_at), address, symbol "
            "FROM signal_ledger WHERE COALESCE(created_at, ingested_at) >= ?", (history_from,))).fetchall()]
        for c in calls:
            c["mint"] = c.pop("address") or None
        try:
            ccols = ["source", "direction", "mint", "symbol", "call_ts", "would_veto"]
            challenges = [dict(zip(ccols, r)) for r in await (await db.execute(
                "SELECT source, direction, mint, symbol, call_ts, would_veto FROM tradingos_challenges "
                "WHERE challenged_at >= ?", (history_from,))).fetchall()]
        except Exception:  # noqa: BLE001 -- P4-15 table not there yet
            challenges = []
    if marks is None:
        out = {"status": "mycelium_unreachable", "window": window, "k": k, "days": [], "summary": None,
               "challenger": None, "ledger_calls": len(calls)}
    else:
        out = {"status": "ok", "window": window, "k": k, "ledger_calls": len(calls), "marks": len(marks),
               **compute(calls, marks, start, days, k, challenges)}
    out["generated_at"] = now_i
    if now is None:
        _CACHE[ck] = (time.time(), out)
    return out
