"""P4-09 -- confluence ping: several proven early wallets or KOLs on the same
pre-migration mint inside one window.

Every CONFLUENCE_POLL_S (60 s), for mints the curve listener saw buys in
during the last WINDOW_S (30 min):

  watch  = distinct runner-watchlist entities (P4-06/07: computed entities and
           strategy-lab radar wallets; a cluster counts once) that bought
  kols   = distinct social accounts (social_signals) that posted the EXACT
           contract address in the window -- a ticker mention does not count

  fires when  watch >= 3   or   kols >= 2   or   (watch >= 1 and kols >= 1)
  and         last mcap < MAX_MCAP_USD ($60k, SOL-quoted curve only)
  and         degen_filters.anti_pump() passes (the shared filter)

Each firing is one confluence_events row (one per mint per window; a later
firing in the same window only if the evidence grew). With CONFLUENCE_ENABLED
the event is also posted to the signal pool as source 'runner_confluence',
type 'confluence', direction BUY, conviction <= 0.69 (new-source cap). The
pool entry is a signal like any other: ranking, the Brier gate, risk caps and
the arming logic decide what happens next; nothing here places an order.

enrich_from_confluence() (signal_fusion, CONFLUENCE_ENRICHER) only adds
'confluence_*' keys to a signal for the same mint; it never changes a field.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

log = logging.getLogger("vantage.confluence")

WINDOW_S = 30 * 60
MAX_MCAP_USD = float(os.environ.get("CONFLUENCE_MAX_MCAP_USD", "60000"))
POLL_S = int(os.environ.get("CONFLUENCE_POLL_S", "60"))
SOURCE = "runner_confluence"
BASE_CONVICTION = 0.55
MAX_CONVICTION = 0.69  # new-source cap -- never raised here

DDL = """CREATE TABLE IF NOT EXISTS confluence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, symbol TEXT,
    rule TEXT NOT NULL, watch_entities INTEGER NOT NULL, kols INTEGER NOT NULL,
    mcap_usd REAL, conviction REAL NOT NULL, evidence TEXT NOT NULL,
    anti_pump TEXT NOT NULL, window_start INTEGER NOT NULL, fired_at INTEGER NOT NULL,
    posted INTEGER NOT NULL DEFAULT 0)"""
IDX = "CREATE INDEX IF NOT EXISTS confluence_events_mint ON confluence_events(mint, fired_at)"


def enabled() -> bool:
    return os.environ.get("CONFLUENCE_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def enricher_enabled() -> bool:
    return os.environ.get("CONFLUENCE_ENRICHER", "0").strip().lower() in ("1", "true", "yes", "on")


def rule_for(watch: int, kols: int) -> Optional[str]:
    if watch >= 3:
        return "watch>=3"
    if kols >= 2:
        return "kols>=2"
    if watch >= 1 and kols >= 1:
        return "watch+kol"
    return None


def conviction_for(watch: int, kols: int) -> float:
    extra = max(0, watch - 1) + max(0, kols - 1)
    return round(min(MAX_CONVICTION, BASE_CONVICTION + 0.03 * extra), 4)


def _curve_window(since: int) -> Dict[str, Any]:
    """Buys in the window (wallet, sol, n_in_mint) per mint + mint state, from curve.db."""
    from . import curve_listener
    if not curve_listener.db_path().exists():
        return {"buys": {}, "mints": {}}
    c = curve_listener.connect()
    try:
        buys: Dict[str, List[dict]] = defaultdict(list)
        for mint, wallet, sol, n, side, quote in c.execute(
                "SELECT mint, wallet, sol, n_in_mint, side, quote FROM curve_trades WHERE block_time >= ?",
                (since,)):
            buys[mint].append({"wallet": wallet, "sol": sol or 0.0, "n_in_mint": n, "side": side,
                               "quote": quote})
        mints = {}
        ms = list(buys)
        for i in range(0, len(ms), 500):
            chunk = ms[i:i + 500]
            for row in c.execute(
                    f"SELECT mint, last_mcap_usd, created_seen, first_seen, trades_seen FROM curve_mints "
                    f"WHERE mint IN ({','.join('?' * len(chunk))})", chunk):
                mints[row[0]] = {"last_mcap_usd": row[1], "created_seen": row[2], "first_seen": row[3],
                                 "trades_seen": row[4]}
        return {"buys": dict(buys), "mints": mints}
    finally:
        c.close()


async def _kol_posts(db, since: int) -> Dict[str, List[dict]]:
    """Exact-contract posts per mint in the window."""
    # P4-08: when the post's own time is known (posted_at, set by the guild
    # router), the window is about when it was posted, not when it was stored.
    try:
        rows = await (await db.execute(
            "SELECT contract_address, platform, username, ticker, post_url FROM social_signals "
            "WHERE contract_address IS NOT NULL AND contract_address != '' "
            "AND COALESCE(posted_at, created_at) >= datetime(?, 'unixepoch')", (since,))).fetchall()
    except Exception:  # noqa: BLE001 -- no posted_at column yet: the original query
        try:
            rows = await (await db.execute(
                "SELECT contract_address, platform, username, ticker, post_url FROM social_signals "
                "WHERE contract_address IS NOT NULL AND contract_address != '' "
                "AND created_at >= datetime(?, 'unixepoch')", (since,))).fetchall()
        except Exception:  # noqa: BLE001 -- table absent on a fresh install
            return {}
    out: Dict[str, List[dict]] = defaultdict(list)
    for ca, platform, user, ticker, url in rows:
        out[ca.strip()].append({"platform": platform, "username": user, "ticker": ticker, "post_url": url})
    return dict(out)


async def scan(db, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """One pass. Returns the events written this pass."""
    from . import wallet_labels
    from .degen_filters import anti_pump, is_major_or_stable
    from .runner_watchlist import DDL as WL_DDL
    now = int(now or time.time())
    since = now - WINDOW_S
    await db.execute(DDL)
    await db.execute(IDX)
    for d in WL_DDL:
        await db.execute(d)
    await wallet_labels._ensure(db)
    cw = await asyncio.to_thread(_curve_window, since)
    kol = await _kol_posts(db, since)
    candidates = set(cw["buys"]) | {m for m in kol if m in cw["mints"]}
    if not candidates:
        return []
    all_buyers = list({b["wallet"] for m in candidates for b in cw["buys"].get(m, []) if b["side"] == "buy"})
    entity_of: Dict[str, str] = {}
    for i in range(0, len(all_buyers), 500):
        chunk = all_buyers[i:i + 500]
        for w, e in await (await db.execute(
                f"SELECT wallet, entity FROM runner_watchlist_wallets WHERE wallet IN ({','.join('?' * len(chunk))})",
                chunk)).fetchall():
            entity_of[w] = e
    labels = await wallet_labels.labels_for(db, all_buyers) if all_buyers else {}
    written = []
    for mint in candidates:
        trades = cw["buys"].get(mint, [])
        buys = [b for b in trades if b["side"] == "buy"]
        ents: Dict[str, dict] = {}
        for b in buys:
            e = entity_of.get(b["wallet"])
            if e and e not in ents:
                ents[e] = {"entity": e, "wallet": b["wallet"], "sol": round(b["sol"], 4)}
        kols: Dict[str, dict] = {}
        for p in kol.get(mint, []):
            k = f"{p['platform']}:{(p['username'] or '').lower()}"
            if p["username"] and k not in kols:
                kols[k] = p
        rule = rule_for(len(ents), len(kols))
        if not rule:
            continue
        state = cw["mints"].get(mint) or {}
        mcap = state.get("last_mcap_usd")
        sym = next((p["ticker"] for p in kols.values() if p.get("ticker")), None)
        if mcap is None or mcap >= MAX_MCAP_USD or any(b["quote"] != "sol" for b in trades[-1:]):
            continue  # no SOL-quoted mcap, or past the early window
        if is_major_or_stable(sym, mint):
            continue
        ap = anti_pump(trades, labels)
        if not ap["ok"]:
            continue
        prev = await (await db.execute(
            "SELECT watch_entities, kols FROM confluence_events WHERE mint=? AND fired_at >= ? "
            "ORDER BY id DESC LIMIT 1", (mint, since))).fetchone()
        if prev and len(ents) <= prev[0] and len(kols) <= prev[1]:
            continue  # already pinged this window and nothing new
        conv = conviction_for(len(ents), len(kols))
        ev = {"watch": list(ents.values())[:20], "kols": list(kols.values())[:20],
              "buys_in_window": len(buys), "created_seen": bool(state.get("created_seen"))}
        cur = await db.execute(
            "INSERT INTO confluence_events (mint, symbol, rule, watch_entities, kols, mcap_usd, conviction, "
            "evidence, anti_pump, window_start, fired_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mint, sym, rule, len(ents), len(kols), mcap, conv, json.dumps(ev), json.dumps(ap), since, now))
        written.append({"id": cur.lastrowid, "mint": mint, "symbol": sym, "rule": rule, "watch": len(ents),
                        "kols": len(kols), "mcap_usd": mcap, "conviction": conv})
    await db.commit()
    return written


async def post(events: List[Dict[str, Any]]) -> int:
    """Post fired events to the signal pool (CONFLUENCE_ENABLED)."""
    from .db import get_db
    from .routers.intel import ingest_signal_internal
    n = 0
    for e in events:
        detail = (f"confluence {e['rule']}: {e['watch']} watchlist entities, {e['kols']} KOLs, "
                  f"mcap ${e['mcap_usd']:,.0f}")
        try:
            await ingest_signal_internal((e["symbol"] or e["mint"][:8]), SOURCE, "confluence",
                                         min(MAX_CONVICTION, e["conviction"]), "BUY", detail, e["mint"],
                                         "solana")
            async with get_db() as db:
                await db.execute("UPDATE confluence_events SET posted=1 WHERE id=?", (e["id"],))
                await db.commit()
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("confluence post failed for %s: %s", e["mint"], ex)
    return n


async def confluence_loop() -> None:
    from .db import get_db
    while True:
        await asyncio.sleep(POLL_S)
        if not enabled():
            continue
        try:
            async with get_db() as db:
                events = await scan(db)
            if events:
                posted = await post(events)
                log.info("confluence: %d fired, %d posted", len(events), posted)
        except Exception as e:  # noqa: BLE001
            log.warning("confluence scan failed: %s", e)


async def recent(limit: int = 50, mint: Optional[str] = None) -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        await db.execute(DDL)
        await db.execute(IDX)
        cols = ["id", "mint", "symbol", "rule", "watch_entities", "kols", "mcap_usd", "conviction",
                "evidence", "anti_pump", "window_start", "fired_at", "posted"]
        q = f"SELECT {', '.join(cols)} FROM confluence_events"
        args: list = []
        if mint:
            q += " WHERE mint=?"
            args.append(mint)
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            q + " ORDER BY id DESC LIMIT ?", (*args, limit))).fetchall()]
        total = (await (await db.execute("SELECT COUNT(*) FROM confluence_events")).fetchone())[0]
    for r in rows:
        r["evidence"] = json.loads(r["evidence"])
        r["anti_pump"] = json.loads(r["anti_pump"])
    return {"enabled": enabled(), "enricher": enricher_enabled(), "window_min": WINDOW_S // 60,
            "max_mcap_usd": MAX_MCAP_USD, "max_conviction": MAX_CONVICTION, "total": total, "events": rows}


async def latest_for(mint: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The newest event for this mint inside the window (enricher)."""
    out = await recent(1, mint)
    if not out["events"]:
        return None
    e = out["events"][0]
    if e["fired_at"] < int(now or time.time()) - WINDOW_S:
        return None
    return e
