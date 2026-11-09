"""P2-05 — the shadow rank: conviction x source reliability, never fed back.

The live rank (promotable_signals) orders calls by conviction alone. This
module computes the rank the system *would* produce if it trusted each
source's measured hit rate instead, and only ever logs it to
shadow_rank_log. It is deliberately not wired into promotable_signals /
jev_decide: switching shadow to live is a separate, human decision after a
walk-forward check (handoff rule 6).

    shadow_score = conviction x (hits + 15) / (n + 30)

where hits/n come from the 24h source_hit_rate finding (P2-04). A source with
no measured outcome yet gets the neutral prior 15/30 = 0.5, so its shadow
score is half its conviction rather than an invented edge.

Enabled by SHADOW_RANK_ENABLED (default off). Fail-open: if Mycelium is
unreachable every source gets the prior and the shadow rank still computes
and logs -- a shadow that must not silently stop just because the learning
layer is down.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SHADOW_RANK_ENABLED = os.environ.get("SHADOW_RANK_ENABLED", "0").strip() == "1"
MYCELIUM_URL = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")
WINDOW = "24h"
PRIOR_HITS, PRIOR_N = 15, 30

DDL = """CREATE TABLE IF NOT EXISTS shadow_rank_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    call_key TEXT NOT NULL,
    live_rank INTEGER,
    shadow_rank INTEGER,
    conviction REAL,
    reliability REAL,
    hits INTEGER,
    n INTEGER
)"""


def _get(url: str, timeout: float = 10.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vantage-shadow-rank/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        log.debug("shadow rank: fetch failed for %s: %s", url, e)
        return None


def fetch_source_hit_rates(window: str = WINDOW) -> Dict[str, Dict[str, int]]:
    """source -> {hits, n} from the source_hit_rate findings for one window.

    Empty dict on any failure (fail-open to the prior)."""
    data = _get(f"{MYCELIUM_URL}/api/findings?miner=source_hit_rate&limit=5000")
    out: Dict[str, Dict[str, int]] = {}
    for f in (data or {}).get("findings") or []:
        p = f.get("payload")
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except (TypeError, ValueError):
                continue
        if not isinstance(p, dict) or p.get("window") != window:
            continue
        source = p.get("source")
        n, hits = p.get("n"), p.get("hits")
        if source and isinstance(n, int) and isinstance(hits, int):
            out[source] = {"hits": hits, "n": n}
    return out


def reliability(hits: int, n: int) -> float:
    return (hits + PRIOR_HITS) / (n + PRIOR_N)


def shadow_rank(signals: List[Dict[str, Any]],
                hit_rates: Optional[Dict[str, Dict[str, int]]] = None) -> List[Dict[str, Any]]:
    """Re-rank promotable calls by shadow_score = conviction x reliability.

    `signals` are promotable_signals() calls (each has source, conviction,
    symbol, direction, mint, ts, side). Returns the same list, each entry
    augmented with live_rank (its conviction rank) and shadow_rank (its
    reliability-adjusted rank) plus shadow_score/reliability/hits/n. The input
    order is the live rank (conviction, then ts)."""
    hit_rates = hit_rates if hit_rates is not None else fetch_source_hit_rates()
    scored: List[Dict[str, Any]] = []
    for i, s in enumerate(signals):
        m = hit_rates.get(s.get("source") or "", {"hits": 0, "n": 0})
        hits, n = m["hits"], m["n"]
        rel = reliability(hits, n)
        entry = dict(s)
        entry.update(live_rank=i + 1, reliability=rel, hits=hits, n=n,
                     shadow_score=(s.get("conviction") or 0.0) * rel)
        scored.append(entry)
    order = sorted(range(len(scored)), key=lambda j: (-scored[j]["shadow_score"], -scored[j]["ts"]))
    for shadow_pos, j in enumerate(order, 1):
        scored[j]["shadow_rank"] = shadow_pos
    return scored


def _call_key(s: Dict[str, Any]) -> str:
    return "|".join(str(s.get(k) or "") for k in ("source", "symbol", "direction", "mint"))


async def log_ranking(ranked: List[Dict[str, Any]]) -> int:
    """Write one shadow_rank_log row per call. Returns rows written."""
    from backend.db import get_db
    now = int(time.time())
    rows = [
        (now, _call_key(s), s.get("live_rank"), s.get("shadow_rank"),
         s.get("conviction"), s.get("reliability"), s.get("hits"), s.get("n"))
        for s in ranked
        if s.get("shadow_rank") is not None
    ]
    if not rows:
        return 0
    async with get_db() as db:
        await db.execute(DDL)
        await db.executemany(
            "INSERT INTO shadow_rank_log (ts, call_key, live_rank, shadow_rank, "
            "conviction, reliability, hits, n) VALUES (?,?,?,?,?,?,?,?)", rows)
        await db.commit()
    return len(rows)


async def compute_and_log(limit: int = 25) -> Dict[str, Any]:
    """One shadow cycle: rank the current promotable calls, log, and return
    the ranked list. Behind SHADOW_RANK_ENABLED (caller checks)."""
    from backend.pipeline import promotable_signals
    signals = promotable_signals(limit=limit)
    ranked = shadow_rank(signals)
    logged = await log_ranking(ranked)
    return {"logged": logged, "calls": ranked,
            "shadow_rank_enabled": SHADOW_RANK_ENABLED}
