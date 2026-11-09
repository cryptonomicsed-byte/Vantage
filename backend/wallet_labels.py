"""P4-05 -- wallet and KOL labels, each with its evidence.

Eight labels, all derived from data already on the box (no extra RPC):

  dev          deployed the token (token_wallet_roles 'deployer'), or the
               buyer in the create tx the curve listener saw
  sniper       bought within SNIPE_SLOTS slots / SNIPE_S seconds of the create
  bundler      one of >= BUNDLE_MIN non-dev buyers landing in the same slot
               (or second) inside the first 20 buys
  insider      funded by that token's dev
  bot          a first-100 buyer in >= BOT_MINTS distinct mints in 7 days
  funding_hub  funded >= HUB_FANOUT distinct wallets (an exchange or a
               distributor; no hand-made exchange address list is used)
  kol          linked to a social account (social_wallet_links) AND has
               actually bought as a wallet -- the table is mostly token
               contract addresses scraped from posts, so a link alone is not
               a wallet, let alone a KOL
  whale        a first-100 buy of >= WHALE_SOL SOL

wallet_labels(wallet, label, evidence JSON list, updated_at): evidence keeps
the latest EVIDENCE_KEEP items, each saying which mint/rule produced it.
P4-06 excludes dev/sniper/bundler/insider/bot/funding_hub from the watchlist
and never clusters wallets through a funding hub.

Flag WALLET_LABELS_ENABLED (default off), read each cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("vantage.labels")

LABELS = ("dev", "sniper", "bundler", "insider", "bot", "funding_hub", "kol", "whale")
EXCLUDED_FROM_WATCHLIST = frozenset({"dev", "sniper", "bundler", "insider", "bot", "funding_hub"})
SNIPE_SLOTS = 2
SNIPE_S = 3
BUNDLE_MIN = 3
BOT_MINTS = int(os.environ.get("LABEL_BOT_MINTS", "40"))
HUB_FANOUT = int(os.environ.get("LABEL_HUB_FANOUT", "25"))
WHALE_SOL = float(os.environ.get("LABEL_WHALE_SOL", "10"))
EVIDENCE_KEEP = 10

DDL = """CREATE TABLE IF NOT EXISTS wallet_labels (
    wallet TEXT NOT NULL, label TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]',
    first_seen INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY (wallet, label))"""
IDX = "CREATE INDEX IF NOT EXISTS wallet_labels_label ON wallet_labels(label)"


def enabled() -> bool:
    return os.environ.get("WALLET_LABELS_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


async def _ensure(db) -> None:
    from .runner_trace import _ensure as _trace_ensure
    await _trace_ensure(db)
    await db.execute(DDL)
    await db.execute(IDX)


async def put(db, items: Iterable[Tuple[str, str, Dict[str, Any]]], now: Optional[float] = None) -> int:
    """Upsert (wallet, label, evidence) items; evidence lists are merged
    (deduplicated, newest last, capped)."""
    now = int(now or time.time())
    n = 0
    for wallet, label, ev in items:
        if not wallet or label not in LABELS:
            continue
        row = await (await db.execute(
            "SELECT evidence FROM wallet_labels WHERE wallet=? AND label=?", (wallet, label))).fetchone()
        evs = json.loads(row[0]) if row else []
        if ev not in evs:
            evs.append(ev)
        evs = evs[-EVIDENCE_KEEP:]
        await db.execute(
            "INSERT INTO wallet_labels (wallet, label, evidence, first_seen, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(wallet, label) DO UPDATE SET evidence=excluded.evidence, updated_at=excluded.updated_at",
            (wallet, label, json.dumps(evs), now, now))
        n += 1
    return n


def _derive_runner(mint: str, buyers: List[dict], origin_slot: Optional[int], origin_time: Optional[int],
                   origin_sig: Optional[str], deployers: set, source: Optional[str]) -> List[tuple]:
    """Pure: labels implied by one runner's first buyers."""
    out: List[tuple] = []
    devs = set(deployers)
    if source == "curve" and buyers and origin_sig and buyers[0].get("signature") == origin_sig:
        devs.add(buyers[0]["wallet"])  # the buy inside the create tx
    for d in devs:
        out.append((d, "dev", {"mint": mint, "via": "deployer" if d in deployers else "create_tx_buy"}))
    groups: Dict[Any, List[str]] = defaultdict(list)
    for b in buyers:
        w = b["wallet"]
        if w in devs:
            continue
        if b.get("entry_slot") is not None and origin_slot is not None:
            d = b["entry_slot"] - origin_slot
            if 0 <= d <= SNIPE_SLOTS:
                out.append((w, "sniper", {"mint": mint, "slots_after_create": d}))
        elif b.get("entry_ts") is not None and origin_time is not None:
            d = b["entry_ts"] - origin_time
            if 0 <= d <= SNIPE_S:
                out.append((w, "sniper", {"mint": mint, "seconds_after_create": d}))
        if (b.get("rank") or 999) <= 20:
            key = ("slot", b["entry_slot"]) if b.get("entry_slot") is not None else ("ts", b.get("entry_ts"))
            if key[1] is not None:
                groups[key].append(w)
        if b.get("funded_by") and b["funded_by"] in devs:
            out.append((w, "insider", {"mint": mint, "funded_by_dev": b["funded_by"]}))
        if (b.get("sol") or 0) >= WHALE_SOL:
            out.append((w, "whale", {"mint": mint, "sol": round(b["sol"], 3)}))
    for (kind, at), ws in groups.items():
        if len(set(ws)) >= BUNDLE_MIN:
            for w in set(ws):
                out.append((w, "bundler", {"mint": mint, kind: at, "group": len(set(ws))}))
    return out


async def label_runner(db, mint: str) -> int:
    await _ensure(db)
    q = await (await db.execute(
        "SELECT origin_slot, origin_time, origin_signature, source FROM runner_trace_queue WHERE mint=?",
        (mint,))).fetchone()
    if not q:
        return 0
    cols = ["wallet", "rank", "entry_ts", "entry_slot", "sol", "signature", "funded_by"]
    buyers = [dict(zip(cols, r)) for r in await (await db.execute(
        f"SELECT {', '.join(cols)} FROM runner_early_buyers WHERE mint=? ORDER BY rank", (mint,))).fetchall()]
    try:
        deployers = {r[0] for r in await (await db.execute(
            "SELECT wallet_address FROM token_wallet_roles WHERE mint=? AND role='deployer'", (mint,))).fetchall()}
    except Exception:  # noqa: BLE001 -- table absent on a fresh install
        deployers = set()
    items = _derive_runner(mint, buyers, q[0], q[1], q[2], deployers, q[3])
    n = await put(db, items)
    await db.commit()
    return n


def _bot_wallets(now: float) -> List[Tuple[str, int]]:
    from . import curve_listener
    if not curve_listener.db_path().exists():
        return []
    c = curve_listener.connect()
    try:
        return c.execute(
            "SELECT wallet, COUNT(DISTINCT mint) n FROM curve_trades WHERE side='buy' AND n_in_mint <= 100 "
            "AND block_time >= ? GROUP BY wallet HAVING n >= ?", (int(now - 7 * 86400), BOT_MINTS)).fetchall()
    finally:
        c.close()


async def label_global(db, now: Optional[float] = None) -> Dict[str, int]:
    """Labels that need the whole picture: bot, funding_hub, kol."""
    await _ensure(db)
    now = now or time.time()
    counts = {}
    bots = await asyncio.to_thread(_bot_wallets, now)
    counts["bot"] = await put(db, [(w, "bot", {"first100_mints_7d": n}) for w, n in bots], now)
    try:
        hubs = await (await db.execute(
            "SELECT address_a, COUNT(DISTINCT address_b) n FROM wallet_edges WHERE role='funded' "
            "GROUP BY address_a HAVING n >= ?", (HUB_FANOUT,))).fetchall()
    except Exception:  # noqa: BLE001
        hubs = []
    counts["funding_hub"] = await put(db, [(w, "funding_hub", {"funded_wallets": n}) for w, n in hubs], now)
    # kol: social-linked AND seen buying as a wallet AND not a mint
    try:
        links = await (await db.execute(
            "SELECT wallet_address, platform, username, post_url FROM social_wallet_links "
            "WHERE chain='solana' OR chain IS NULL")).fetchall()
    except Exception:  # noqa: BLE001
        links = []
    buyer_set = {r[0] for r in await (await db.execute("SELECT DISTINCT wallet FROM runner_early_buyers")).fetchall()}
    mint_like = await asyncio.to_thread(_known_mints, [l[0] for l in links])
    curve_buyers = await asyncio.to_thread(_curve_buyers, [l[0] for l in links])
    kol_items = [(w, "kol", {"platform": p, "username": u, "post_url": url})
                 for w, p, u, url in links
                 if w and not w.endswith("pump") and w not in mint_like and (w in buyer_set or w in curve_buyers)]
    counts["kol"] = await put(db, kol_items, now)
    await db.commit()
    return counts


def _known_mints(addrs: List[str]) -> set:
    from . import curve_listener
    if not addrs or not curve_listener.db_path().exists():
        return set()
    c = curve_listener.connect()
    try:
        out = set()
        for i in range(0, len(addrs), 500):
            chunk = addrs[i:i + 500]
            out |= {r[0] for r in c.execute(
                f"SELECT mint FROM curve_mints WHERE mint IN ({','.join('?' * len(chunk))})", chunk)}
        return out
    finally:
        c.close()


def _curve_buyers(addrs: List[str]) -> set:
    from . import curve_listener
    if not addrs or not curve_listener.db_path().exists():
        return set()
    c = curve_listener.connect()
    try:
        out = set()
        for i in range(0, len(addrs), 500):
            chunk = addrs[i:i + 500]
            out |= {r[0] for r in c.execute(
                f"SELECT DISTINCT wallet FROM curve_trades WHERE side='buy' AND wallet IN ({','.join('?' * len(chunk))})",
                chunk)}
        return out
    finally:
        c.close()


async def labels_for(db, wallets: List[str]) -> Dict[str, List[str]]:
    await _ensure(db)
    out: Dict[str, List[str]] = defaultdict(list)
    for i in range(0, len(wallets), 500):
        chunk = wallets[i:i + 500]
        if not chunk:
            continue
        for w, l in await (await db.execute(
                f"SELECT wallet, label FROM wallet_labels WHERE wallet IN ({','.join('?' * len(chunk))})", chunk)).fetchall():
            out[w].append(l)
    return dict(out)


async def label_loop(interval_s: int = 3600) -> None:
    from .db import get_db
    while True:
        await asyncio.sleep(60)
        if not enabled():
            continue
        try:
            async with get_db() as db:
                await _ensure(db)
                mints = [r[0] for r in await (await db.execute(
                    "SELECT mint FROM runner_trace_queue WHERE status='done'")).fetchall()]
                for m in mints:
                    await label_runner(db, m)
                counts = await label_global(db)
            log.info("labels: %d runners, %s", len(mints), counts)
        except Exception as e:  # noqa: BLE001
            log.warning("label cycle failed: %s", e)
        await asyncio.sleep(max(0, interval_s - 60))


async def summary() -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        await _ensure(db)
        counts = {l: n for l, n in await (await db.execute(
            "SELECT label, COUNT(*) FROM wallet_labels GROUP BY label")).fetchall()}
    return {"enabled": enabled(), "labels": {l: counts.get(l, 0) for l in LABELS},
            "excluded_from_watchlist": sorted(EXCLUDED_FROM_WATCHLIST)}


async def wallet(wallet_addr: str) -> Dict[str, Any]:
    from .db import get_db
    async with get_db() as db:
        await _ensure(db)
        rows = await (await db.execute(
            "SELECT label, evidence, first_seen, updated_at FROM wallet_labels WHERE wallet=?",
            (wallet_addr,))).fetchall()
    return {"wallet": wallet_addr, "labels": [
        {"label": l, "evidence": json.loads(e), "first_seen": f, "updated_at": u} for l, e, f, u in rows]}
