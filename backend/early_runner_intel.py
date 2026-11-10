"""Early Runner section: buy classification, per-runner profiles, pipeline overview.

Read-only over tables the P4 chain already writes (runner_tokens,
runner_trace_queue, runner_early_buyers, wallet_labels, runner_watchlist,
confluence_events) plus the curve listener's coverage. Never writes, never
posts to the signal pool.

Why classification exists (measured on the live tables, 2026-11-09): an
oldest-first RPC walk records every buy that touches the mint, on any pool.
142 of 192 RPC-traced rows were dust under 0.01 SOL -- bots trading side pools
at prices no real buyer pays -- and two rows were liquidity going into a pool
(one "buy" of 5,995 SOL receiving 20% of supply). Ranked raw, those crowded the
real early buyers out of the list and into the watchlist's scoring. Rows are
classified, never deleted: the raw rank stays, `real_rank` counts real buys.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

PUMP_SUPPLY = 1_000_000_000          # every pump.fun mint
DUST_SOL = float(os.environ.get("RUNNER_DUST_SOL", "0.01"))
# one tx receiving >= 5% of supply for >= 100 SOL is a pool being seeded or
# migrated, not a trader (the curve itself only sells ~80% across its life)
LIQ_SUPPLY_FRAC = 0.05
LIQ_MIN_SOL = 100.0
BUNDLE_WINDOW_S = 1                  # real buys within 1 s of each other


def classify(row: Dict[str, Any]) -> str:
    """'buy' | 'dust' | 'liquidity' for one runner_early_buyers row."""
    sol = row.get("sol")
    tokens = row.get("tokens") or 0
    if isinstance(sol, (int, float)) and sol >= LIQ_MIN_SOL and tokens >= LIQ_SUPPLY_FRAC * PUMP_SUPPLY:
        return "liquidity"
    if not isinstance(sol, (int, float)) or sol < DUST_SOL:
        return "dust"
    return "buy"


def annotate(rows: List[Dict[str, Any]], origin_time: Optional[int] = None) -> List[Dict[str, Any]]:
    """Adds kind, real_rank (rank among real buys) and delay_s (seconds after
    the create tx, when known). Input order = entry order. Pure."""
    n = 0
    for r in rows:
        r["kind"] = classify(r)
        if r["kind"] == "buy":
            n += 1
            r["real_rank"] = n
        else:
            r["real_rank"] = None
        ts = r.get("entry_ts")
        r["delay_s"] = (ts - origin_time) if (origin_time and isinstance(ts, (int, float))) else None
    return rows


def profile(rows: List[Dict[str, Any]], origin_time: Optional[int] = None, first_n: int = 20) -> Dict[str, Any]:
    """Summary of one runner's entry, from annotated rows. Pure."""
    annotate(rows, origin_time)
    real = [r for r in rows if r["kind"] == "buy"]
    head = real[:first_n]
    kinds = Counter(r["kind"] for r in rows)
    # bundles: groups of real buys landing within BUNDLE_WINDOW_S of each other
    bundles, cur = [], []
    for r in head:
        if cur and r.get("entry_ts") is not None and cur[-1].get("entry_ts") is not None \
                and r["entry_ts"] - cur[-1]["entry_ts"] <= BUNDLE_WINDOW_S:
            cur.append(r)
        else:
            if len(cur) >= 3:
                bundles.append(cur)
            cur = [r]
    if len(cur) >= 3:
        bundles.append(cur)
    labels = Counter(l for r in head for l in (r.get("labels") or []))
    funders = Counter(r["funded_by"] for r in head if r.get("funded_by"))
    shared = {f: c for f, c in funders.items() if c >= 2}
    first = rows[0] if rows else None
    dev_like = first is not None and "dev" in (first.get("labels") or [])
    return {
        "rows": len(rows), "real_buyers": len(real), "dust": kinds.get("dust", 0),
        "liquidity_events": kinds.get("liquidity", 0),
        "dust_share": round(kinds.get("dust", 0) / len(rows), 3) if rows else None,
        "first_real_delay_s": real[0]["delay_s"] if real else None,
        "sol_first_n": round(sum(r.get("sol") or 0 for r in head), 3),
        "median_sol_first_n": _median([r.get("sol") or 0 for r in head]),
        "bundles": [{"size": len(b), "sol": round(sum(x.get("sol") or 0 for x in b), 3),
                     "wallets": [x["wallet"] for x in b]} for b in bundles],
        "bundled_share": round(sum(len(b) for b in bundles) / len(head), 3) if head else None,
        "label_mix": dict(labels.most_common()),
        "shared_funders": shared,
        "first_row": {"wallet": first["wallet"], "sol": first.get("sol"), "kind": first["kind"],
                      "dev": dev_like} if first else None,
        "first_n": first_n,
    }


def _median(xs: Iterable[float]) -> Optional[float]:
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return round(s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2, 4)


async def runner_profile(mint: str) -> Dict[str, Any]:
    from . import runner_trace as rt
    eb = await rt.early_buyers(mint)
    tr = eb.get("trace") or {}
    return {"mint": mint, "trace": tr or None,
            "profile": profile(eb["buyers"], tr.get("origin_time")), "buyers": eb["buyers"]}


async def overview(limit: int = 50, include_unbacked: bool = False) -> Dict[str, Any]:
    """Pipeline health + every recent runner with its entry profile."""
    from .db import get_db
    from . import runner_trace as rt
    now = int(time.time())
    out: Dict[str, Any] = {"generated_at": now}
    from .runner_detect import ensure_table, MIN_LIQ_USD, MIN_LIQ_FRAC
    async with get_db() as db:
        await rt._ensure(db)
        await ensure_table(db)
        out["liquidity_rule"] = {"min_usd": MIN_LIQ_USD, "min_frac_of_cap": MIN_LIQ_FRAC}
        out["runner_validity"] = {("backed" if b == 1 else "unbacked" if b == 0 else "unverified"): n
                                  for b, n in await (await db.execute(
                                      "SELECT backed, COUNT(*) FROM runner_tokens GROUP BY backed")).fetchall()}
        q = await (await db.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(buyers),0) FROM runner_trace_queue GROUP BY status")).fetchall()
        out["trace_queue"] = {s: {"n": n, "buyers": b} for s, n, b in q}
        tiers = await (await db.execute(
            "SELECT tier_usd, COUNT(*) FROM runner_tokens WHERE backed = 1 GROUP BY tier_usd")).fetchall()
        out["runners_by_tier"] = {str(int(t or 0)): n for t, n in tiers}
        out["runners_24h"] = (await (await db.execute(
            "SELECT COUNT(*) FROM runner_tokens WHERE backed = 1 AND detected_at >= ?", (now - 86400,))).fetchone())[0]
        out["labels"] = dict(await (await db.execute(
            "SELECT label, COUNT(DISTINCT wallet) FROM wallet_labels GROUP BY label")).fetchall()) \
            if await _has(db, "wallet_labels") else {}
        out["watchlist_entities"] = (await (await db.execute(
            "SELECT COUNT(*) FROM runner_watchlist")).fetchone())[0] if await _has(db, "runner_watchlist") else 0
        if await _has(db, "confluence_events"):
            out["confluence_24h"] = (await (await db.execute(
                "SELECT COUNT(*) FROM confluence_events WHERE fired_at >= ?", (now - 86400,))).fetchone())[0]
        cols = ["mint", "symbol", "tier_usd", "peak_mcap_usd", "last_mcap_usd", "detected_at", "first_seen",
                "trace_status", "backed", "liq_usd"]
        where = "" if include_unbacked else "WHERE backed = 1 "
        runners = [dict(zip(cols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(cols)} FROM runner_tokens {where}ORDER BY detected_at DESC LIMIT ?",
            (limit,))).fetchall()]
        mints = [r["mint"] for r in runners]
        by_mint: Dict[str, list] = {m: [] for m in mints}
        origin: Dict[str, Optional[int]] = {}
        if mints:
            ph = ",".join("?" * len(mints))
            bcols = ["mint", "wallet", "rank", "entry_ts", "sol", "tokens", "funded_by", "is_major"]
            for r in await (await db.execute(
                    f"SELECT {', '.join(bcols)} FROM runner_early_buyers WHERE mint IN ({ph}) ORDER BY mint, rank",
                    mints)).fetchall():
                by_mint[r[0]].append(dict(zip(bcols, r)))
            for m, ot in await (await db.execute(
                    f"SELECT mint, origin_time FROM runner_trace_queue WHERE mint IN ({ph})", mints)).fetchall():
                origin[m] = ot
            from .wallet_labels import labels_for
            labels = await labels_for(db, list({b["wallet"] for bs in by_mint.values() for b in bs}))
            for bs in by_mint.values():
                for b in bs:
                    b["labels"] = labels.get(b["wallet"], [])
    all_rows = [b for bs in by_mint.values() for b in bs]
    kinds = Counter(classify(b) for b in all_rows)
    out["buyer_rows"] = {"total": len(all_rows), **dict(kinds)}
    for r in runners:
        rows = by_mint.get(r["mint"]) or []
        p = profile(rows, origin.get(r["mint"])) if rows else None
        r["profile"] = p and {k: p[k] for k in ("real_buyers", "dust", "liquidity_events", "dust_share",
                                                 "first_real_delay_s", "sol_first_n", "bundled_share",
                                                 "label_mix", "first_row")}
        r["age_to_tier_s"] = (r["detected_at"] - r["first_seen"]) if r.get("first_seen") and r.get("detected_at") else None
    out["runners"] = runners
    out["curve"] = await _curve_summary()
    return out


# Last good curve reading. coverage() scans a ~1 GB DB; right after a restart
# or under write load it can exceed the wait, and the overview then reported
# the listener "off" while it was live. A slow read returns the previous
# reading with its age; null only if there has never been one.
_CURVE_LAST: Dict[str, Any] = {}


async def _curve_summary() -> Optional[Dict[str, Any]]:
    import asyncio
    try:
        from . import curve_listener as cl
        cov = await asyncio.wait_for(asyncio.to_thread(cl.coverage), 20)
        if cov:
            v = {k: cov.get(k) for k in ("enabled", "last_event_age_s", "trades_1h", "mints_24h",
                                         "created_seen_24h", "db_bytes")}
            v["gaps"] = len((cov.get("state") or {}).get("gaps") or cov.get("gaps") or [])
            _CURVE_LAST.update(at=time.time(), val=v)
            return {**v, "reading_age_s": 0}
    except Exception:  # noqa: BLE001 -- the overview never fails on the listener
        pass
    if _CURVE_LAST:
        age = time.time() - _CURVE_LAST["at"]
        v = dict(_CURVE_LAST["val"])
        if isinstance(v.get("last_event_age_s"), (int, float)):
            v["last_event_age_s"] += age
        return {**v, "reading_age_s": round(age)}
    return None


async def _has(db, table: str) -> bool:
    return bool(await (await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))).fetchone())
