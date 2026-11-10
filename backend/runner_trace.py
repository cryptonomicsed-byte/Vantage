"""P4-03 -- trace each runner back to its origin.

For every runner (P4-02) the first buyers are recorded in
runner_early_buyers, from one of two sources:

  curve  the curve listener saw the create (created_seen=1): the first 100
         distinct buyers come straight from curve_trades in order. No RPC.
         Origin = the listener's first slot for the mint.
  rpc    otherwise early_buyer.backtrack_early_buyers(oldest_first=True)
         pages back to the mint's first signature (up to
         RUNNER_TRACE_MAX_SIGS) at ~1 rps (pace 1 s) over RUNNER_TRACE_RPC
         (default: the free public mainnet RPC) and parses the oldest
         100. reached_origin says whether the walk really got to the create;
         if it ran out first, the buyers are early but not proven first.

runner_trace_queue holds one row per runner (pending -> tracing -> done |
partial | failed, with attempts and the last error); the worker takes one at
a time. A big runner can have hundreds of thousands of signatures: a walk
that stops short is 'walking' with a resume cursor, and the next attempt
continues from it (attempts reset whenever a walk makes progress). runner_tokens.trace_status mirrors it for the UI.

Flag RUNNER_TRACE_ENABLED (default off), read each cycle.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("vantage.runner_trace")

FIRST_N = 100
# A curve can complete inside its own create tx (a dev buy of ~85 SOL fills
# it), after which everything trades on the AMM and the curve feed holds a
# single buyer. Fewer curve buyers than this -> walk RPC instead, which parses
# AMM buys too.
CURVE_MIN_BUYERS = int(os.environ.get("RUNNER_TRACE_CURVE_MIN_BUYERS", "20"))
MAX_SIGS = int(os.environ.get("RUNNER_TRACE_MAX_SIGS", "100000"))
PACE_S = float(os.environ.get("RUNNER_TRACE_PACE_S", "1.0"))
# how many of the oldest txs a trace may read to find FIRST_N real buys
SCAN_SIGS = int(os.environ.get("RUNNER_TRACE_SCAN_SIGS", "600"))
REAL_MIN = 20  # a finished RPC trace with fewer real buys is re-walked once
# Free public RPC by default: the Helius key here has run out of quota, and a
# paced (~1 rps) walk is well within the public endpoint's limits.
TRACE_RPC = os.environ.get("RUNNER_TRACE_RPC", "https://api.mainnet-beta.solana.com")
MAX_ATTEMPTS = 3        # attempts that make no progress
MAX_RESUMES = 200       # attempts that each walk further back

DDL = [
    """CREATE TABLE IF NOT EXISTS runner_trace_queue (
        mint TEXT PRIMARY KEY, enqueued_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, source TEXT,
        reached_origin INTEGER, origin_slot INTEGER, origin_signature TEXT, origin_time INTEGER,
        signatures_scanned INTEGER, buyers INTEGER, finished_at INTEGER,
        resume_before TEXT, resumes INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS runner_early_buyers (
        mint TEXT NOT NULL, wallet TEXT NOT NULL, rank INTEGER NOT NULL, entry_ts INTEGER,
        entry_slot INTEGER, sol REAL, tokens REAL, signature TEXT, funded_by TEXT,
        is_major INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL,
        PRIMARY KEY (mint, wallet))""",
    "CREATE INDEX IF NOT EXISTS runner_early_buyers_wallet ON runner_early_buyers(wallet)",
]


def enabled() -> bool:
    return os.environ.get("RUNNER_TRACE_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


async def _ensure(db) -> None:
    from .runner_detect import DDL as RUNNER_DDL
    await db.execute(RUNNER_DDL)
    for d in DDL:
        await db.execute(d)
    # traces finished from a too-thin curve list (before CURVE_MIN_BUYERS
    # existed) are re-walked over RPC
    try:
        await db.execute(
            "UPDATE runner_trace_queue SET status='pending', attempts=0, resume_before=NULL "
            "WHERE status='done' AND source='curve' AND buyers < ?", (CURVE_MIN_BUYERS,))
    except Exception:  # noqa: BLE001 -- columns not there yet on a fresh table
        pass
    for col, decl in (("resume_before", "TEXT"), ("resumes", "INTEGER NOT NULL DEFAULT 0"),
                      ("dust_rescanned", "INTEGER NOT NULL DEFAULT 0")):
        try:  # a queue table from an earlier build lacks these
            await db.execute(f"ALTER TABLE runner_trace_queue ADD COLUMN {col} {decl}")
            if col == "dust_rescanned":
                await requeue_dust_traces(db)   # one-time, when this build first runs
        except Exception:  # noqa: BLE001 -- already there
            pass


async def requeue_dust_traces(db) -> int:
    """One-time migration: RPC traces that finished before real buys counted
    toward FIRST_N, with fewer than REAL_MIN real buys, are re-walked (now
    reading up to SCAN_SIGS txs). Never re-applies to traces finished later."""
    from .early_runner_intel import DUST_SOL
    cur = await db.execute(
        "UPDATE runner_trace_queue SET status='pending', attempts=0, resume_before=NULL, dust_rescanned=1 "
        "WHERE status='done' AND source='rpc' AND dust_rescanned=0 AND "
        "(SELECT COUNT(*) FROM runner_early_buyers b WHERE b.mint=runner_trace_queue.mint AND b.sol >= ?) < ?",
        (DUST_SOL, REAL_MIN))
    return cur.rowcount or 0


async def enqueue_new_runners(db) -> int:
    """Every runner without a queue row gets one."""
    await _ensure(db)
    from .runner_detect import ensure_table
    await ensure_table(db)
    # only liquidity-backed runners spend the free-RPC trace budget
    cur = await db.execute(
        "INSERT OR IGNORE INTO runner_trace_queue (mint, enqueued_at) "
        "SELECT mint, ? FROM runner_tokens WHERE backed = 1 "
        "AND mint NOT IN (SELECT mint FROM runner_trace_queue)",
        (int(time.time()),))
    await db.commit()
    return cur.rowcount or 0


def curve_first_buyers(mint: str, n: int = FIRST_N) -> Optional[Dict[str, Any]]:
    """First n distinct buyers from curve_trades, if the listener saw the
    create. None when it didn't (so the caller falls back to RPC)."""
    from . import curve_listener
    if not curve_listener.db_path().exists():
        return None
    c = curve_listener.connect()
    try:
        m = c.execute("SELECT created_seen, first_slot, first_seen FROM curve_mints WHERE mint=?", (mint,)).fetchone()
        if not m or not m[0]:
            return None
        rows = c.execute(
            "SELECT wallet, block_time, slot, sol, tokens, sig FROM curve_trades "
            "WHERE mint=? AND side='buy' ORDER BY n_in_mint", (mint,)).fetchall()
    finally:
        c.close()
    seen: Dict[str, dict] = {}
    for w, bt, slot, sol, tok, sig in rows:
        if w in seen:
            seen[w]["sol"] = (seen[w]["sol"] or 0) + (sol or 0)
            continue
        seen[w] = {"wallet": w, "rank": len(seen) + 1, "entry_ts": bt, "entry_slot": slot,
                   "sol": sol, "tokens": tok, "signature": sig, "funded_by": None}
        if len(seen) >= n:
            break
    buyers = list(seen.values())
    if len(buyers) < CURVE_MIN_BUYERS:
        return None  # too thin to stand for "early buyers"; the RPC walk decides
    top_sol = {b["wallet"] for b in sorted(buyers, key=lambda b: -(b["sol"] or 0))[:10]}
    for b in buyers:
        b["is_major"] = 1 if (b["rank"] <= 5 or b["wallet"] in top_sol) else 0
    return {"buyers": buyers, "reached_origin": True, "origin_slot": m[1], "origin_time": m[2],
            "origin_signature": buyers[0]["signature"] if buyers else None,
            "signatures_scanned": None, "source": "curve"}


async def rpc_first_buyers(mint: str, resume_before: Optional[str] = None,
                           resume_count: int = 0) -> Dict[str, Any]:
    from .early_buyer import backtrack_early_buyers
    out = await backtrack_early_buyers(mint, max_buyers=FIRST_N, max_total=MAX_SIGS,
                                       oldest_first=True, use_cache=False, pace_s=PACE_S,
                                       rpc_url=TRACE_RPC or None,
                                       resume_before=resume_before, resume_count=resume_count,
                                       scan_sigs=SCAN_SIGS)
    if out.get("error"):
        raise RuntimeError(out["error"])
    buyers = [{"wallet": b["wallet_address"], "rank": b["entry_rank"], "entry_ts": b.get("entry_ts"),
               "entry_slot": None, "sol": b.get("sol_spent"), "tokens": b.get("token_amount"),
               "signature": b.get("tx_signature"), "funded_by": b.get("funded_by"),
               "is_major": b.get("is_major", 0)} for b in out.get("buyers") or []]
    o = out.get("origin") or {}
    return {"buyers": buyers, "reached_origin": out.get("reached_origin", False),
            "origin_slot": o.get("slot"), "origin_time": o.get("block_time"),
            "origin_signature": o.get("signature"), "signatures_scanned": out.get("signatures_scanned"),
            "cursor": out.get("cursor"), "source": "rpc"}


async def trace_one(mint: str) -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        await _ensure(db)
        await db.execute("INSERT OR IGNORE INTO runner_trace_queue (mint, enqueued_at) VALUES (?,?)",
                         (mint, int(time.time())))
        prev = await (await db.execute(
            "SELECT resume_before, signatures_scanned FROM runner_trace_queue WHERE mint=?", (mint,))).fetchone()
        await db.execute("UPDATE runner_trace_queue SET status='tracing', attempts=attempts+1 WHERE mint=?", (mint,))
        await db.execute("UPDATE runner_tokens SET trace_status='tracing' WHERE mint=?", (mint,))
        await db.commit()
    try:
        resume_before = prev[0] if prev else None
        resume_count = (prev[1] or 0) if (prev and prev[0]) else 0
        res = await asyncio.to_thread(curve_first_buyers, mint) or await rpc_first_buyers(
            mint, resume_before, resume_count)
        if res["reached_origin"] and res["buyers"]:
            status, err = "done", None
        elif not res["reached_origin"]:
            status = "walking"  # still paging back to the create; resumes next cycle
            err = f"{res.get('signatures_scanned'):,} signatures scanned, create not reached yet"
        else:
            status, err = "partial", "reached the create but parsed no buyers"
    except Exception as e:  # noqa: BLE001
        res, status, err = None, "failed", f"{type(e).__name__}: {str(e)[:200]}"
    async with get_db() as db:
        if res and res["buyers"]:
            await db.execute("DELETE FROM runner_early_buyers WHERE mint=?", (mint,))
            await db.executemany(
                "INSERT OR REPLACE INTO runner_early_buyers (mint, wallet, rank, entry_ts, entry_slot, sol, "
                "tokens, signature, funded_by, is_major, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(mint, b["wallet"], b["rank"], b["entry_ts"], b["entry_slot"], b["sol"], b["tokens"],
                  b["signature"], b["funded_by"], b["is_major"], res["source"]) for b in res["buyers"]])
        progressed = bool(res and status == "walking" and (res.get("signatures_scanned") or 0) > resume_count)
        await db.execute(
            "UPDATE runner_trace_queue SET status=?, last_error=?, source=?, reached_origin=?, origin_slot=?, "
            "origin_signature=?, origin_time=?, signatures_scanned=?, buyers=?, finished_at=?, "
            "resume_before=?, resumes=resumes+?, attempts=CASE WHEN ? THEN 0 ELSE attempts END WHERE mint=?",
            (status, err, res and res["source"], res and int(bool(res["reached_origin"])),
             res and res["origin_slot"], res and res["origin_signature"], res and res["origin_time"],
             res and res["signatures_scanned"], len(res["buyers"]) if res else 0, int(time.time()),
             res and res.get("cursor"), 1 if progressed else 0, progressed, mint))
        await db.execute("UPDATE runner_tokens SET trace_status=? WHERE mint=?", (status, mint))
        await db.commit()
    return {"mint": mint, "status": status, "error": err,
            "buyers": len(res["buyers"]) if res else 0, "source": res and res["source"],
            "reached_origin": bool(res and res["reached_origin"]), "origin_slot": res and res["origin_slot"]}


async def next_job() -> Optional[str]:
    from .db import get_db
    async with get_db() as db:
        await enqueue_new_runners(db)
        row = await (await db.execute(
            "SELECT mint FROM runner_trace_queue WHERE status IN ('pending','failed','tracing','walking') "
            "AND attempts < ? AND resumes < ? "
            "AND mint NOT IN (SELECT mint FROM runner_tokens WHERE backed = 0) "  # fake runners skipped
            "ORDER BY attempts, enqueued_at LIMIT 1",
            (MAX_ATTEMPTS, MAX_RESUMES))).fetchone()
    return row[0] if row else None


async def trace_loop(idle_s: int = 30) -> None:
    backfilled = False
    while True:
        await asyncio.sleep(idle_s)
        if enabled() and not backfilled:
            # once per process: every finished trace into the Money Flow Galaxy
            try:
                from . import galaxy_wallets
                from .db import get_db
                async with get_db() as db:
                    log.info("galaxy backfill %s", await galaxy_wallets.backfill(db))
            except Exception as e:  # noqa: BLE001
                log.warning("galaxy backfill failed: %s", e)
            backfilled = True
        if not enabled():
            continue
        try:
            mint = await next_job()
            if mint:
                out = await trace_one(mint)
                log.info("runner trace %s", out)
                if out["status"] in ("done", "partial"):
                    from . import wallet_labels, galaxy_wallets
                    from .db import get_db
                    async with get_db() as db:
                        if out["status"] == "done" and wallet_labels.enabled():
                            await wallet_labels.label_runner(db, mint)
                        # the traced buyers, ranked, into the Money Flow Galaxy
                        await galaxy_wallets.sync_runner(db, mint)
        except Exception as e:  # noqa: BLE001
            log.warning("runner trace cycle failed: %s", e)


async def early_buyers(mint: str) -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        await _ensure(db)
        q = await (await db.execute(
            "SELECT status, attempts, last_error, source, reached_origin, origin_slot, origin_signature, "
            "origin_time, signatures_scanned, buyers, finished_at FROM runner_trace_queue WHERE mint=?",
            (mint,))).fetchone()
        cols = ["wallet", "rank", "entry_ts", "entry_slot", "sol", "tokens", "signature", "funded_by",
                "is_major", "source"]
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(cols)} FROM runner_early_buyers WHERE mint=? ORDER BY rank", (mint,))).fetchall()]
        from .wallet_labels import labels_for
        labels = await labels_for(db, [r["wallet"] for r in rows] + [r["funded_by"] for r in rows if r["funded_by"]])
    for r in rows:
        r["labels"] = labels.get(r["wallet"], [])
        r["funder_labels"] = labels.get(r["funded_by"], []) if r["funded_by"] else []
    from .early_runner_intel import annotate
    annotate(rows, q[7] if q else None)  # kind (buy/dust/liquidity), real_rank, delay_s
    qcols = ["status", "attempts", "last_error", "source", "reached_origin", "origin_slot",
             "origin_signature", "origin_time", "signatures_scanned", "buyers", "finished_at"]
    return {"mint": mint, "trace": dict(zip(qcols, q)) if q else None, "buyers": rows}
