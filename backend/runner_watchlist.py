"""P4-06 -- runner watchlist: wallets that keep getting into runners early.

    score(entity) = sum over runners R the entity bought early:
                      tier_weight(R) x earliness x size x decay
      earliness = 1 / rank      (rank among R's first 100 buyers)
      size      = min(1, sol / 5)
      decay     = 0.5 ** (age_days / 90)    (90-day half-life on the entry)

Rules:
  - >= MIN_RUNNERS (2) distinct runners, or the entity is not listed;
  - wallets labelled dev / sniper / bundler / insider / bot / funding_hub
    (P4-05) contribute nothing;
  - clusters once: a wallet and the wallets funded by the same funder are
    one entity (never through a funding hub -- an exchange funding thousands
    of strangers is not a cluster). On each runner an entity counts as its
    best member, not the sum, so splitting one buyer across five wallets
    does not buy five times the score.

No seed list: what goes on the watchlist is computed, never hand-picked
(open decision 5).

P4-07: strategy-lab's radar (radar/watchlist.json: first-mover wallets
with its own 0.50-0.95 conviction, a different method) is imported as a
separate, labelled source -- never blended into this score. Radar wallets
at or above RADAR_MIN_CONVICTION join runner_watchlist_wallets (entity
'radar:<address>' unless already in a computed entity) so confluence counts
them; computed entities that include a radar wallet show its conviction.
The file's own generated_at is reported so a stale radar looks stale. runner_watchlist is rebuilt each cycle in one
transaction; runner_watchlist_wallets maps wallet -> entity for confluence.

Flag RUNNER_WATCHLIST_ENABLED (default off), read each cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

log = logging.getLogger("vantage.watchlist")

MIN_RUNNERS = 2
HALF_LIFE_DAYS = 90.0
SIZE_FULL_SOL = 5.0
TOP_N = 500
RADAR_PATH = os.environ.get("RADAR_WATCHLIST", "/opt/ares/strategy-lab/radar/watchlist.json")
RADAR_MIN_CONVICTION = 0.50

DDL = [
    """CREATE TABLE IF NOT EXISTS runner_watchlist (
        entity TEXT PRIMARY KEY, wallets TEXT NOT NULL, score REAL NOT NULL, runners INTEGER NOT NULL,
        runner_mints TEXT NOT NULL, contributions TEXT NOT NULL, labels TEXT NOT NULL DEFAULT '[]',
        updated_at INTEGER NOT NULL, radar_conviction REAL)""",
    """CREATE TABLE IF NOT EXISTS runner_watchlist_wallets (
        wallet TEXT PRIMARY KEY, entity TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runner_watchlist_candidates (
        entity TEXT PRIMARY KEY, wallets TEXT NOT NULL, score REAL NOT NULL, runners INTEGER NOT NULL,
        runner_mints TEXT NOT NULL, contributions TEXT NOT NULL, labels TEXT NOT NULL DEFAULT '[]',
        updated_at INTEGER NOT NULL, symbol TEXT)""",
    """CREATE TABLE IF NOT EXISTS radar_wallets (
        wallet TEXT PRIMARY KEY, conviction REAL NOT NULL, tier TEXT, evidence TEXT,
        generated_at TEXT, loaded_at INTEGER NOT NULL)""",
]


def read_radar(path: Optional[str] = None) -> Dict[str, Any]:
    """strategy-lab radar file -> {generated_at, wallets: [{address, conviction, tier, evidence}]}.
    Accepts the current dict shape and a bare list."""
    path = path or RADAR_PATH
    if not os.path.exists(path):
        return {"present": False, "path": path, "generated_at": None, "wallets": []}
    with open(path) as f:
        data = json.load(f)
    items = data.get("watchlist", []) if isinstance(data, dict) else data
    out = []
    for e in items or []:
        if isinstance(e, dict) and e.get("address") and isinstance(e.get("conviction"), (int, float)):
            out.append({"address": e["address"], "conviction": float(e["conviction"]),
                        "tier": e.get("tier"), "evidence": e.get("evidence")})
    return {"present": True, "path": path,
            "generated_at": data.get("generated_at") if isinstance(data, dict) else None, "wallets": out}


def enabled() -> bool:
    return os.environ.get("RUNNER_WATCHLIST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def contribution(tier_weight: float, rank: int, sol: Optional[float], age_days: float) -> float:
    if not rank or rank < 1 or rank > 100:
        return 0.0
    size = min(1.0, max(0.0, (sol or 0.0)) / SIZE_FULL_SOL)
    return tier_weight * (1.0 / rank) * size * (0.5 ** (max(0.0, age_days) / HALF_LIFE_DAYS))


def _rank_entities(buys: List[Dict[str, Any]], labels: Dict[str, List[str]], now: float,
                   excluded: frozenset):
    """Pure. (per_entity contributions by mint, entity -> member wallets).

    buys: {mint, wallet, rank, sol, funded_by, entry_ts, tier_weight, symbol}
    """
    hubs = {w for w, ls in labels.items() if "funding_hub" in ls}

    def entity(b) -> str:
        f = b.get("funded_by")
        return f"funder:{f}" if f and f not in hubs else f"wallet:{b['wallet']}"

    # per (entity, mint): best member's contribution
    best: Dict[tuple, Dict[str, Any]] = {}
    members: Dict[str, set] = defaultdict(set)
    for b in buys:
        if excluded & set(labels.get(b["wallet"], [])):
            continue
        age = (now - (b.get("entry_ts") or now)) / 86400.0
        c = contribution(b.get("tier_weight") or 0.0, b.get("rank") or 0, b.get("sol"), age)
        if c <= 0:
            continue
        e = entity(b)
        members[e].add(b["wallet"])
        k = (e, b["mint"])
        if k not in best or c > best[k]["c"]:
            best[k] = {"c": c, "wallet": b["wallet"], "rank": b["rank"], "sol": b.get("sol"),
                       "symbol": b.get("symbol"), "mint": b["mint"]}
    per_entity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for (e, _m), v in best.items():
        per_entity[e].append(v)
    return per_entity, members


def _entry(e: str, contribs: List[Dict[str, Any]], members: Dict[str, set],
           labels: Dict[str, List[str]]) -> Dict[str, Any]:
    contribs.sort(key=lambda v: -v["c"])
    ws = sorted(members[e])
    return {"entity": e, "wallets": ws, "score": round(sum(v["c"] for v in contribs), 6),
            "runners": len(contribs), "runner_mints": [v["mint"] for v in contribs],
            "contributions": [{**v, "c": round(v["c"], 6)} for v in contribs[:10]],
            "labels": sorted({l for w in ws for l in labels.get(w, [])})}


def compute(buys: List[Dict[str, Any]], labels: Dict[str, List[str]], now: float,
            excluded: frozenset) -> List[Dict[str, Any]]:
    """Confirmed clusters: the same entity buying MIN_RUNNERS or more runners."""
    per_entity, members = _rank_entities(buys, labels, now, excluded)
    out = [_entry(e, c, members, labels) for e, c in per_entity.items() if len(c) >= MIN_RUNNERS]
    out.sort(key=lambda r: -r["score"])
    return out[:TOP_N]


def compute_singles(buys: List[Dict[str, Any]], labels: Dict[str, List[str]], now: float,
                    excluded: frozenset, top_n: int = TOP_N) -> List[Dict[str, Any]]:
    """Unconfirmed candidates: strongest entities that bought exactly ONE runner.

    Live the corpus is small (7 liquidity-backed runners, no wallet inside two
    of them), so the MIN_RUNNERS=2 rule alone yields nothing. These are stored
    separately and labelled unconfirmed -- they never enter `runner_watchlist`.
    """
    per_entity, members = _rank_entities(buys, labels, now, excluded)
    out = [_entry(e, c, members, labels) for e, c in per_entity.items() if len(c) == 1]
    out.sort(key=lambda r: -r["score"])
    return out[:top_n]


async def rebuild(db, now: Optional[float] = None) -> Dict[str, Any]:
    from . import wallet_labels
    now = now or time.time()
    await wallet_labels._ensure(db)  # creates runner/trace/label tables too
    from .runner_detect import ensure_table as _ensure_runners
    await _ensure_runners(db)
    for d in DDL:
        await db.execute(d)
    cols = ["mint", "wallet", "rank", "sol", "funded_by", "entry_ts", "tier_weight", "symbol"]
    rows = await (await db.execute(
        "SELECT b.mint, b.wallet, b.rank, b.sol, b.funded_by, COALESCE(b.entry_ts, r.detected_at), "
        "r.tier_weight, r.symbol FROM runner_early_buyers b JOIN runner_tokens r ON r.mint=b.mint "
        "WHERE b.rank <= 100 AND r.backed = 1")).fetchall()  # liquidity-backed runners only
    raw = [dict(zip(cols, r)) for r in rows]
    # dust (side-pool bot trades) and liquidity rows are not entries: drop
    # them and rank the remaining real buys per mint
    from .early_runner_intel import classify
    sol_tok = {(m, w): (s, t) for m, w, s, t in await (await db.execute(
        "SELECT mint, wallet, sol, tokens FROM runner_early_buyers WHERE rank <= 100")).fetchall()}
    buys, seen = [], {}
    for b in sorted(raw, key=lambda x: (x["mint"], x["rank"])):
        s, t = sol_tok.get((b["mint"], b["wallet"]), (b.get("sol"), 0))
        if classify({"sol": s, "tokens": t}) != "buy":
            continue
        seen[b["mint"]] = seen.get(b["mint"], 0) + 1
        buys.append({**b, "rank": seen[b["mint"]]})
    wallets = list({b["wallet"] for b in buys} | {b["funded_by"] for b in buys if b["funded_by"]})
    labels = await wallet_labels.labels_for(db, wallets)
    wl = compute(buys, labels, now, wallet_labels.EXCLUDED_FROM_WATCHLIST)

    # P4-07: radar wallets, separate source
    try:
        radar = await asyncio.to_thread(read_radar)
    except Exception as e:  # noqa: BLE001
        radar = {"present": False, "error": str(e), "wallets": [], "generated_at": None}
    radar_ok = [r for r in radar["wallets"] if r["conviction"] >= RADAR_MIN_CONVICTION]
    rlabels = await wallet_labels.labels_for(db, [r["address"] for r in radar_ok])
    radar_ok = [r for r in radar_ok
                if not (wallet_labels.EXCLUDED_FROM_WATCHLIST & set(rlabels.get(r["address"], [])))]
    radar_by = {r["address"]: r for r in radar_ok}
    for ent in wl:
        hits = [radar_by[w]["conviction"] for w in ent["wallets"] if w in radar_by]
        ent["radar_conviction"] = max(hits) if hits else None
    await db.execute("DELETE FROM radar_wallets")
    await db.executemany(
        "INSERT INTO radar_wallets (wallet, conviction, tier, evidence, generated_at, loaded_at) VALUES (?,?,?,?,?,?)",
        [(r["address"], r["conviction"], r["tier"], json.dumps(r["evidence"]) if r["evidence"] is not None else None,
          radar.get("generated_at"), int(now)) for r in radar_ok])

    await db.execute("DELETE FROM runner_watchlist")
    await db.execute("DELETE FROM runner_watchlist_wallets")
    await db.executemany(
        "INSERT INTO runner_watchlist (entity, wallets, score, runners, runner_mints, contributions, labels, "
        "updated_at, radar_conviction) VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["entity"], json.dumps(r["wallets"]), r["score"], r["runners"], json.dumps(r["runner_mints"]),
          json.dumps(r["contributions"]), json.dumps(r["labels"]), int(now), r.get("radar_conviction")) for r in wl])
    await db.executemany("INSERT OR REPLACE INTO runner_watchlist_wallets (wallet, entity) VALUES (?,?)",
                         [(w, r["entity"]) for r in wl for w in r["wallets"]])
    # unconfirmed single-runner candidates: populated, never mixed into the
    # confirmed list above (source='candidate' in watchlist_for)
    cand = compute_singles(buys, labels, now, wallet_labels.EXCLUDED_FROM_WATCHLIST)
    await db.execute("DELETE FROM runner_watchlist_candidates")
    await db.executemany(
        "INSERT INTO runner_watchlist_candidates (entity, wallets, score, runners, runner_mints, "
        "contributions, labels, updated_at, symbol) VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["entity"], json.dumps(r["wallets"]), r["score"], r["runners"],
          json.dumps(r["runner_mints"]), json.dumps(r["contributions"]), json.dumps(r["labels"]),
          int(now), r["contributions"][0].get("symbol")) for r in cand])
    await db.executemany("INSERT OR REPLACE INTO runner_watchlist_wallets (wallet, entity) VALUES (?,?)",
                         [(w, r["entity"]) for r in cand for w in r["wallets"]
                          if w not in {ww for rr in wl for ww in rr["wallets"]}])
    await db.executemany("INSERT OR IGNORE INTO runner_watchlist_wallets (wallet, entity) VALUES (?,?)",
                         [(a, f"radar:{a}") for a in radar_by])
    await db.commit()
    return {"entities": len(wl), "candidates": len(cand), "buys_considered": len(buys),
            "runners": len({b["mint"] for b in buys}), "radar_wallets": len(radar_by),
            "radar_generated_at": radar.get("generated_at")}


async def watchlist_for(db, wallets: List[str]) -> Dict[str, Dict[str, Any]]:
    """wallet -> {entity, score, source, radar_conviction} for galaxy nodes.

    runner_watchlist_wallets also holds radar-only wallets (entity
    'radar:<address>') that have no runner_watchlist row, so an INNER JOIN on
    runner_watchlist dropped every one of them -- and with zero computed
    entities (live: runner_watchlist empty, 124 radar wallets) the galaxy
    decoration matched nothing. LEFT JOINs keep them, with the radar
    conviction as their score. Addresses are base58 (case-sensitive): exact
    match, never lower()."""
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(wallets), 500):
        chunk = wallets[i:i + 500]
        ph = ",".join("?" * len(chunk))
        base = ("FROM runner_watchlist_wallets ww LEFT JOIN runner_watchlist w ON w.entity = ww.entity ")
        try:
            rows = await (await db.execute(
                f"SELECT ww.wallet, ww.entity, w.score, w.radar_conviction, r.conviction, c.score {base}"
                f"LEFT JOIN radar_wallets r ON r.wallet = ww.wallet "
                f"LEFT JOIN runner_watchlist_candidates c ON c.entity = ww.entity "
                f"WHERE ww.wallet IN ({ph})", chunk)).fetchall()
        except Exception:  # noqa: BLE001 -- radar/candidate tables absent: computed entities only
            try:
                rows = await (await db.execute(
                    f"SELECT ww.wallet, ww.entity, w.score, w.radar_conviction, NULL, NULL {base}"
                    f"WHERE ww.wallet IN ({ph})", chunk)).fetchall()
            except Exception:  # noqa: BLE001 -- watchlist tables absent
                return out
        for wallet, ent, score, ent_radar, radar, cand in rows:
            if score is not None:             # confirmed (>= MIN_RUNNERS runners)
                src, val = "runner", score
            elif cand is not None:            # one runner only: unconfirmed
                src, val = "candidate", cand
            elif radar is not None:
                src, val = "radar", radar
            else:
                continue                      # orphan mapping: nothing to show
            out[wallet] = {"entity": ent, "score": val, "source": src,
                           "unconfirmed": src != "runner",
                           "radar_conviction": radar if radar is not None else ent_radar}
    return out


async def watchlist_loop(interval_s: int = 900) -> None:
    from .db import get_db
    while True:
        await asyncio.sleep(120)
        if enabled():
            try:
                async with get_db() as db:
                    out = await rebuild(db)
                log.info("watchlist rebuilt: %s", out)
            except Exception as e:  # noqa: BLE001
                log.warning("watchlist rebuild failed: %s", e)
        await asyncio.sleep(max(0, interval_s - 120))


async def get(limit: int = 50) -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        for d in DDL:
            await db.execute(d)
        cols = ["entity", "wallets", "score", "runners", "runner_mints", "contributions", "labels", "updated_at",
                "radar_conviction"]
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(cols)} FROM runner_watchlist ORDER BY score DESC LIMIT ?", (limit,))).fetchall()]
        total = (await (await db.execute("SELECT COUNT(*) FROM runner_watchlist")).fetchone())[0]
        rad = await (await db.execute(
            "SELECT COUNT(*), MAX(generated_at), MAX(loaded_at) FROM radar_wallets")).fetchone()
        radar_top = [{"wallet": w, "conviction": c, "tier": t} for w, c, t in await (await db.execute(
            "SELECT wallet, conviction, tier FROM radar_wallets ORDER BY conviction DESC LIMIT 10")).fetchall()]
        # additive: unconfirmed single-runner candidates. `total` stays the
        # confirmed count so existing callers keep their meaning, but a UI that
        # only reads `total` showed 0 while 138 candidates were ranked.
        ccols = ["entity", "wallets", "score", "runners", "runner_mints", "contributions", "labels",
                 "updated_at", "symbol"]
        crows = [dict(zip(ccols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(ccols)} FROM runner_watchlist_candidates ORDER BY score DESC LIMIT ?",
            (limit,))).fetchall()]
        ctotal = (await (await db.execute("SELECT COUNT(*) FROM runner_watchlist_candidates")).fetchone())[0]
    for r in rows + crows:
        for k in ("wallets", "runner_mints", "contributions", "labels"):
            r[k] = json.loads(r[k])
    return {"enabled": enabled(), "total": total, "min_runners": MIN_RUNNERS,
            "half_life_days": HALF_LIFE_DAYS, "entities": rows,
            "candidates": {"total": ctotal, "unconfirmed": True, "entities": crows},
            "radar": {"wallets": rad[0], "generated_at": rad[1], "loaded_at": rad[2],
                      "min_conviction": RADAR_MIN_CONVICTION, "top": radar_top}}
