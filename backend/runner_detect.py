"""P4-02 -- runner detection.

A runner is a pump.fun token whose market cap crossed a RUNNER_TIERS
threshold. The curve listener (P4-01) only sees the bonding curve (it ends
around graduation, ~$25-30k); runners are made after migration. So every
cycle this checks curve mints that reached graduation range (plus every
known runner, so tiers can upgrade) against DexScreener's free tokens
endpoint (30 addresses per call), taking each token's highest pair market
cap.

    RUNNER_TIERS     "100000:0.5,1000000:1.0,5000000:1.5,10000000:2.0"
                     (threshold_usd:weight)
    RUNNER_TIER_100K "0" drops the 100k tier (default: dropped)

runner_tokens (vantage.db, low volume): one row per mint -- current tier and
weight, peak market cap *observed by this poller* (DexScreener reports the
current cap, not an all-time high; the peak can only be as good as the
polling), when each tier was first crossed, and the creation slot when the
curve listener saw the create. Runners are pinned in curve.db so their early
trades outlive retention.

Flag RUNNER_DETECT_ENABLED (default off), read each cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("vantage.runners")

DEX_URL = "https://api.dexscreener.com/tokens/v1/solana/"
BATCH = 30
CANDIDATE_MCAP_USD = float(os.environ.get("RUNNER_CANDIDATE_MCAP_USD", "25000"))
CANDIDATE_DAYS = float(os.environ.get("RUNNER_CANDIDATE_DAYS", "14"))
DEFAULT_TIERS = "100000:0.5,1000000:1.0,5000000:1.5,10000000:2.0"
# A quoted market cap only counts when the pool's liquidity backs it. Found
# live: 85 of 114 "runners" in a day were $10M+ on paper -- e.g. FOTF, peak
# recorded $699M, actually ~$1.6k cap on $1.7k liquidity with 18k bot buys of
# wash volume. Real $1M+ tokens sit on tens to hundreds of $k of liquidity.
MIN_LIQ_USD = float(os.environ.get("RUNNER_MIN_LIQ_USD", "10000"))
MIN_LIQ_FRAC = float(os.environ.get("RUNNER_MIN_LIQ_FRAC", "0.02"))


def backed(mcap: float, liq: Optional[float]) -> bool:
    return isinstance(liq, (int, float)) and liq >= max(MIN_LIQ_USD, mcap * MIN_LIQ_FRAC)

DDL = """CREATE TABLE IF NOT EXISTS runner_tokens (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    tier_usd REAL NOT NULL,
    tier_weight REAL NOT NULL,
    peak_mcap_usd REAL NOT NULL,
    peak_at INTEGER NOT NULL,
    last_mcap_usd REAL,
    last_checked INTEGER,
    tiers_crossed TEXT NOT NULL DEFAULT '{}',
    created_slot INTEGER,
    created_seen INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER,
    pair_created_at INTEGER,
    detected_at INTEGER NOT NULL,
    trace_status TEXT NOT NULL DEFAULT 'pending'
)"""


def enabled() -> bool:
    return os.environ.get("RUNNER_DETECT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def tiers() -> List[Tuple[float, float]]:
    """[(threshold_usd, weight)] ascending, honouring RUNNER_TIER_100K."""
    out = []
    for part in os.environ.get("RUNNER_TIERS", DEFAULT_TIERS).split(","):
        try:
            t, w = part.split(":")
            out.append((float(t), float(w)))
        except ValueError:
            continue
    if os.environ.get("RUNNER_TIER_100K", "0").strip() not in ("1", "true", "yes", "on"):
        out = [(t, w) for t, w in out if t != 100000]
    return sorted(out)


def tier_for(mcap: Optional[float], tier_list: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    best = None
    for t, w in tier_list:
        if mcap is not None and mcap >= t:
            best = (t, w)
    return best


def best_by_mint(pairs: List[dict]) -> Dict[str, dict]:
    """DexScreener returns one row per pair; keep each token's highest cap."""
    out: Dict[str, dict] = {}
    for p in pairs or []:
        bt = p.get("baseToken") or {}
        mint = bt.get("address")
        mc = p.get("marketCap") or p.get("fdv")
        if not mint or not isinstance(mc, (int, float)):
            continue
        liq = (p.get("liquidity") or {}).get("usd")
        ok = backed(float(mc), liq)
        cur = out.get(mint)
        # a liquidity-backed quote always beats an unbacked one; within the
        # same class the higher cap wins
        if cur is None or (ok, mc) > (cur["backed"], cur["mcap"]):
            out[mint] = {"mcap": float(mc), "symbol": bt.get("symbol"), "name": bt.get("name"),
                         "pair_created_at": p.get("pairCreatedAt"), "liq_usd": liq, "backed": ok}
    return out


async def _fetch(mints: List[str]) -> Dict[str, dict]:
    import httpx
    out: Dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=20) as c:
        for i in range(0, len(mints), BATCH):
            chunk = mints[i:i + BATCH]
            try:
                r = await c.get(DEX_URL + ",".join(chunk))
                if r.status_code == 200:
                    out.update(best_by_mint(r.json()))
                elif r.status_code == 429:
                    await asyncio.sleep(5)
            except Exception as e:  # noqa: BLE001
                log.debug("dexscreener batch failed: %s", e)
            await asyncio.sleep(0.25)  # well under 300 req/min
    return out


def _candidates(curve_db: sqlite3.Connection, known: List[str], now: float) -> Dict[str, dict]:
    cut = int(now - CANDIDATE_DAYS * 86400)
    rows = curve_db.execute(
        "SELECT mint, first_slot, created_seen, first_seen FROM curve_mints "
        "WHERE COALESCE(max_mcap_usd,0) >= ? AND COALESCE(last_trade_at, first_seen, 0) >= ?",
        (CANDIDATE_MCAP_USD, cut)).fetchall()
    out = {m: {"first_slot": s, "created_seen": cs, "first_seen": fs} for m, s, cs, fs in rows}
    for m in known:
        out.setdefault(m, {})
    return out


async def apply_quotes(db, quotes: Dict[str, dict], meta: Dict[str, dict], now: float) -> List[str]:
    """Upsert runner_tokens from fresh quotes. Returns mints that became (or
    upgraded as) runners this cycle."""
    tl = tiers()
    await ensure_table(db)
    changed = []
    for mint, q in quotes.items():
        mc = q["mcap"]
        ok = bool(q.get("backed", True))
        cur = await (await db.execute(
            "SELECT tier_usd, peak_mcap_usd, tiers_crossed FROM runner_tokens WHERE mint=?", (mint,))).fetchone()
        if cur is not None:
            # every re-quote records whether the cap is liquidity-backed;
            # unbacked quotes never raise peak or tier
            await db.execute("UPDATE runner_tokens SET backed=?, liq_usd=? WHERE mint=?",
                             (1 if ok else 0, q.get("liq_usd"), mint))
            if not ok:
                await db.execute("UPDATE runner_tokens SET last_mcap_usd=?, last_checked=? WHERE mint=?",
                                 (mc, int(now), mint))
                continue
        elif not ok:
            continue  # never detect a runner on an unbacked quote
        if cur is None:
            t = tier_for(mc, tl)
            if not t:
                continue
            crossed = {str(int(x)): int(now) for x, _ in tl if mc >= x}
            m = meta.get(mint, {})
            await db.execute(
                "INSERT INTO runner_tokens (mint, symbol, name, tier_usd, tier_weight, peak_mcap_usd, peak_at, "
                "last_mcap_usd, last_checked, tiers_crossed, created_slot, created_seen, first_seen, "
                "pair_created_at, detected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mint, q.get("symbol"), q.get("name"), t[0], t[1], mc, int(now), mc, int(now),
                 json.dumps(crossed), m.get("first_slot"), int(bool(m.get("created_seen"))),
                 m.get("first_seen"), q.get("pair_created_at"), int(now)))
            await db.execute("UPDATE runner_tokens SET backed=1, liq_usd=? WHERE mint=?", (q.get("liq_usd"), mint))
            changed.append(mint)
            continue
        tier_usd, peak, crossed_s = cur
        crossed = json.loads(crossed_s or "{}")
        new_peak = mc > peak
        for x, _ in tl:
            if mc >= x and str(int(x)) not in crossed:
                crossed[str(int(x))] = int(now)
        t = tier_for(max(mc, peak), tl) or (tier_usd, dict(tl).get(tier_usd, 0.0))
        if t[0] > tier_usd:
            changed.append(mint)
        await db.execute(
            "UPDATE runner_tokens SET last_mcap_usd=?, last_checked=?, tier_usd=?, tier_weight=?, "
            "peak_mcap_usd=CASE WHEN ? THEN ? ELSE peak_mcap_usd END, "
            "peak_at=CASE WHEN ? THEN ? ELSE peak_at END, tiers_crossed=? WHERE mint=?",
            (mc, int(now), t[0], t[1], new_peak, mc, new_peak, int(now), json.dumps(crossed), mint))
    await db.commit()
    return changed


async def ensure_table(db) -> None:
    await db.execute(DDL)
    cols = {r[1] for r in await (await db.execute("PRAGMA table_info(runner_tokens)")).fetchall()}
    # backed: 1 = last quote liquidity-backed, 0 = not, NULL = not re-quoted
    # since this check existed (treated as unverified downstream)
    for col, decl in (("backed", "INTEGER"), ("liq_usd", "REAL")):
        if col not in cols:
            await db.execute(f"ALTER TABLE runner_tokens ADD COLUMN {col} {decl}")


async def detect_once() -> Dict[str, Any]:
    from . import curve_listener
    from .db import get_db
    now = time.time()
    async with get_db() as db:
        await ensure_table(db)
        known = [r[0] for r in await (await db.execute("SELECT mint FROM runner_tokens")).fetchall()]
    if not curve_listener.db_path().exists():
        return {"candidates": 0, "quoted": 0, "new_or_upgraded": 0, "note": "no curve.db yet"}
    def _read():
        cdb = curve_listener.connect()
        try:
            return _candidates(cdb, known, now)
        finally:
            cdb.close()
    meta = await asyncio.to_thread(_read)
    quotes = await _fetch(list(meta))
    async with get_db() as db:
        changed = await apply_quotes(db, quotes, meta, now)
        runners = [r[0] for r in await (await db.execute("SELECT mint FROM runner_tokens")).fetchall()]

    def _pin():
        c = curve_listener.connect()
        try:
            for m in runners:
                curve_listener.pin(c, m)
        finally:
            c.close()
    await asyncio.to_thread(_pin)
    return {"candidates": len(meta), "quoted": len(quotes), "new_or_upgraded": len(changed)}


async def runner_loop(interval_s: int = 300) -> None:
    while True:
        await asyncio.sleep(interval_s)
        if not enabled():
            continue
        try:
            out = await detect_once()
            if out.get("new_or_upgraded"):
                log.info("runners: %s", out)
        except Exception as e:  # noqa: BLE001
            log.warning("runner detection failed: %s", e)


async def list_runners(limit: int = 50, include_unbacked: bool = False) -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        await ensure_table(db)
        cols = ["mint", "symbol", "name", "tier_usd", "tier_weight", "peak_mcap_usd", "peak_at",
                "last_mcap_usd", "last_checked", "tiers_crossed", "created_slot", "created_seen",
                "first_seen", "pair_created_at", "detected_at", "trace_status", "backed", "liq_usd"]
        where = "" if include_unbacked else "WHERE backed = 1 "
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            f"SELECT {', '.join(cols)} FROM runner_tokens {where}ORDER BY peak_mcap_usd DESC LIMIT ?",
            (limit,))).fetchall()]
        unverified = (await (await db.execute(
            "SELECT COUNT(*) FROM runner_tokens WHERE backed IS NOT 1")).fetchone())[0]
    for r in rows:
        r["tiers_crossed"] = json.loads(r["tiers_crossed"] or "{}")
    return {"enabled": enabled(), "tiers": [{"usd": t, "weight": w} for t, w in tiers()], "runners": rows,
            "unbacked_or_unverified": unverified,
            "liquidity_rule": {"min_usd": MIN_LIQ_USD, "min_frac_of_cap": MIN_LIQ_FRAC}}


async def check_mints(mints: List[str]) -> Dict[str, Any]:
    """Evaluate given mints now (e.g. a known runner that launched before the
    curve listener was on). Same tiering and storage as the loop."""
    from .db import get_db
    mints = [m.strip() for m in mints if m and m.strip()][:BATCH * 3]
    quotes = await _fetch(mints)
    async with get_db() as db:
        changed = await apply_quotes(db, quotes, {}, time.time())
    return {"checked": len(mints), "quoted": len(quotes), "runners": changed,
            "not_found": [m for m in mints if m not in quotes]}
