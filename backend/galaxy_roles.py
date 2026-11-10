"""Money Flow Galaxy role maintenance: top traders from curve.db + a one-time
first_buyer relabel.

Why this exists
---------------
* top_trader: the only producer was /opt/ares/pumpfun_wallet_intel.py
  (outside this repo). Its loop enriches ``watchlist[:MAX_TOKENS_PER_CYCLE]``
  and skips mints already in ``processed``; the reset only fires once
  ``len(processed) >= len(watchlist)``, which never happens while the
  candidate list (65 live) is longer than 3. After the first cycle it
  enriches nothing, so top_trader rows stopped landing (the newest live row
  predates the 24 h window). Its "top trader" was also a signature count
  over 15 signatures, not a trading metric.
  ``refresh_top_traders`` derives top traders per mint from real curve
  trades (data/curve.db, free, already collected) instead: per-mint wallets
  ranked by realised SOL PnL on matched buy/sell size, dust trades
  (< DUST_SOL) and pool/liquidity accounts excluded. Flag
  GALAXY_TOP_TRADERS_ENABLED (default off), read each cycle.

* first_buyer relabel: rows written before the early_buyer/galaxy_wallets
  fixes carry 'Entry rank #n' (raw rank, dust bots included) or
  'block_time' (metric is a unix time, not SOL). ``relabel_first_buyers``
  drops dust, re-ranks the rest per mint in their existing rank order and
  rewrites the label to 'Entry #n · x SOL' / 'Entry #n (size unknown)'.
  Idempotent (rewritten rows match 'Entry #%'), small batches, commit per
  batch. Flag GALAXY_RELABEL_FIRST_BUYERS (default off).

Additive only: no scoring/trading code reads anything written here except
the galaxy view (and aggregate_score's existing role join, which already
expects top_trader rows).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

TOP_LABEL_PREFIX = "PnL "          # marks rows this module owns
TOP_N = 10                         # alpha.RANKED_ROLE_MAX_RANK['top_trader']
RELABEL_BATCH_MINTS = 20

# Program / AMM accounts that can show up as the "wallet" of a trade row on
# some feeds; never traders.
POOL_ACCOUNTS = frozenset({
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun bonding curve program
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # pump.fun AMM (PumpSwap)
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # pump.fun migration account
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "So11111111111111111111111111111111111111112",
})


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def top_traders_enabled() -> bool:
    return _flag("GALAXY_TOP_TRADERS_ENABLED")


def relabel_enabled() -> bool:
    return _flag("GALAXY_RELABEL_FIRST_BUYERS")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ── top traders ──────────────────────────────────────────────────────

def rank_top_traders(trades: Iterable[Dict[str, Any]], mint: str = "",
                     top_n: int = TOP_N) -> List[Dict[str, Any]]:
    """Pure. trades: dicts with wallet, side ('buy'|'sell'), sol, tokens.
    Realised PnL = sell SOL for the tokens matched against window buys minus
    the cost basis of those tokens. Only wallets with a positive realised
    PnL rank (a 'top trader' who only bought has realised nothing)."""
    from .early_runner_intel import DUST_SOL, classify
    agg: Dict[str, Dict[str, float]] = {}
    pools = set(POOL_ACCOUNTS) | ({mint} if mint else set())
    for t in trades:
        w = t.get("wallet")
        if not w or w in pools:
            continue
        kind = classify({"sol": t.get("sol"), "tokens": t.get("tokens")})
        if kind == "liquidity":      # pool seeding / migration: not a trader
            pools.add(w)
            continue
        if kind == "dust":
            continue
        a = agg.setdefault(w, {"buy_sol": 0.0, "buy_tok": 0.0, "sell_sol": 0.0, "sell_tok": 0.0, "n": 0})
        tok = float(t.get("tokens") or 0)
        if t.get("side") == "buy":
            a["buy_sol"] += float(t["sol"])
            a["buy_tok"] += tok
        elif t.get("side") == "sell":
            a["sell_sol"] += float(t["sol"])
            a["sell_tok"] += tok
        else:
            continue
        a["n"] += 1
    out = []
    for w, a in agg.items():
        if w in pools or a["buy_tok"] <= 0 or a["sell_tok"] <= 0:
            continue
        matched = min(a["buy_tok"], a["sell_tok"])
        pnl = a["sell_sol"] * (matched / a["sell_tok"]) - a["buy_sol"] * (matched / a["buy_tok"])
        if pnl < DUST_SOL:
            continue
        out.append({"wallet": w, "pnl": pnl, "trades": int(a["n"]),
                    "volume": a["buy_sol"] + a["sell_sol"]})
    out.sort(key=lambda r: (-r["pnl"], -r["volume"], r["wallet"]))
    for i, r in enumerate(out[:top_n], 1):
        r["rank"] = i
        r["label"] = f"PnL +{r['pnl']:.3g} SOL · {r['trades']} trades"
    return out[:top_n]


def _curve_trades(mints: List[str], since: int, active: int, active_since: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Read-only curve.db access (blocking; run in a thread)."""
    from .curve_listener import db_path
    p = db_path()
    if not p.exists():
        return {}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    try:
        mset = list(dict.fromkeys(mints))
        if active > 0:   # plus the most-traded curve mints of the last hour (bounded scan)
            mset += [m for (m,) in c.execute(
                # "+mint": keep the planner on the time index; a plain GROUP BY mint
                # walks the whole (mint, n_in_mint) index -- a full-table scan
                "SELECT mint FROM curve_trades WHERE block_time >= ? GROUP BY +mint "
                "ORDER BY COUNT(*) DESC LIMIT ?", (active_since or since, active)).fetchall() if m not in mset]
        out: Dict[str, List[Dict[str, Any]]] = {}
        for m in mset:
            rows = c.execute(
                "SELECT wallet, side, sol, tokens FROM curve_trades WHERE mint=? AND block_time >= ? "
                "AND (quote IS NULL OR quote='sol') LIMIT 20000", (m, since)).fetchall()
            if rows:
                out[m] = [{"wallet": w, "side": s, "sol": sol, "tokens": tok} for w, s, sol, tok in rows]
        return out
    finally:
        c.close()


async def refresh_top_traders(db, now: Optional[float] = None) -> Dict[str, int]:
    """One cycle: galaxy mints (first_buyer/top_holder rows in the window)
    plus the most active curve mints -> top_trader rows. Never raises."""
    now = now or time.time()
    hours = _env_int("GALAXY_TOP_TRADERS_HOURS", 24)
    max_mints = _env_int("GALAXY_TOP_TRADERS_MAX_MINTS", 60)
    active = _env_int("GALAXY_TOP_TRADERS_ACTIVE_MINTS", 20)
    out = {"mints": 0, "rows": 0, "removed": 0}
    try:
        syms: Dict[str, str] = {}
        for role in ("first_buyer", "top_holder"):
            for m, s in await (await db.execute(
                    "SELECT mint, MAX(symbol) FROM token_wallet_roles WHERE role=? "
                    "AND discovered_at >= datetime('now', ?) GROUP BY mint LIMIT ?",
                    (role, f"-{hours} hours", max_mints))).fetchall():
                syms.setdefault(m, s)
        trades = await asyncio.to_thread(_curve_trades, list(syms)[:max_mints],
                                         int(now - hours * 3600), active,
                                         int(now - min(hours, 1) * 3600))
        for mint, rows in trades.items():
            top = rank_top_traders(rows, mint)
            if not top:
                continue
            sym = syms.get(mint) or f"{mint[:4]}…{mint[-4:]}"
            for r in top:
                await db.execute(
                    """INSERT INTO token_wallet_roles
                       (mint, symbol, wallet_address, role, rank, metric, metric_label, discovered_at)
                       VALUES (?,?,?,'top_trader',?,?,?,datetime('now'))
                       ON CONFLICT(mint, wallet_address, role) DO UPDATE SET
                         rank=excluded.rank, metric=excluded.metric,
                         metric_label=excluded.metric_label, discovered_at=excluded.discovered_at""",
                    (mint, sym, r["wallet"], r["rank"], round(r["pnl"], 6), r["label"]))
            keep = [r["wallet"] for r in top]
            ph = ",".join("?" * len(keep))
            cur = await db.execute(   # only our own stale rows, never another producer's
                f"DELETE FROM token_wallet_roles WHERE mint=? AND role='top_trader' "
                f"AND metric_label LIKE '{TOP_LABEL_PREFIX}%' AND wallet_address NOT IN ({ph})",
                (mint, *keep))
            out["removed"] += cur.rowcount or 0
            out["mints"] += 1
            out["rows"] += len(top)
            await db.commit()   # per mint: short write transactions on the live DB
    except Exception as e:  # noqa: BLE001 -- the galaxy is a view
        log.warning("top_trader refresh failed: %s", e)
        out["error"] = str(e)[:200]  # type: ignore[assignment]
    return out


# ── first_buyer relabel (one-time, idempotent) ───────────────────────

def _relabel_plan(rows: List[Dict[str, Any]], start_rank: int = 0):
    """Pure. rows: one mint's legacy first_buyer rows (id, rank, metric,
    metric_label). Returns (delete_ids, [(id, rank, metric, label)])."""
    from .early_runner_intel import DUST_SOL
    delete, update, n = [], [], start_rank
    for r in sorted(rows, key=lambda x: (x["rank"] is None, x["rank"] or 0, x["id"])):
        metric = r["metric"]
        if r.get("metric_label") == "block_time":   # metric is a unix time, not SOL
            metric = None
        if metric is not None and metric < DUST_SOL:
            delete.append(r["id"])
            continue
        n += 1
        label = f"Entry #{n} · {metric:.3g} SOL" if metric is not None else f"Entry #{n} (size unknown)"
        update.append((r["id"], n, metric, label))
    return delete, update


async def relabel_first_buyers(db, batch_mints: int = RELABEL_BATCH_MINTS) -> Dict[str, int]:
    out = {"mints": 0, "deleted": 0, "relabelled": 0}
    legacy = "role='first_buyer' AND (metric_label IS NULL OR metric_label NOT LIKE 'Entry #%')"
    mints = [m for (m,) in await (await db.execute(
        f"SELECT DISTINCT mint FROM token_wallet_roles WHERE {legacy}")).fetchall()]
    for i in range(0, len(mints), batch_mints):
        for mint in mints[i:i + batch_mints]:
            cols = ["id", "rank", "metric", "metric_label"]
            rows = [dict(zip(cols, r)) for r in await (await db.execute(
                f"SELECT {', '.join(cols)} FROM token_wallet_roles WHERE mint=? AND {legacy}",
                (mint,))).fetchall()]
            # already-converted rows on the same mint keep their ranks; legacy
            # rows rank after them so no two first buyers share a rank
            top = (await (await db.execute(
                "SELECT MAX(rank) FROM token_wallet_roles WHERE mint=? AND role='first_buyer' "
                "AND metric_label LIKE 'Entry #%'", (mint,))).fetchone())[0] or 0
            delete, update = _relabel_plan(rows, top)
            if delete:
                await db.executemany("DELETE FROM token_wallet_roles WHERE id=?", [(d,) for d in delete])
            await db.executemany(
                "UPDATE token_wallet_roles SET rank=?, metric=?, metric_label=? WHERE id=?",
                [(rk, m, lbl, rid) for rid, rk, m, lbl in update])
            out["mints"] += 1
            out["deleted"] += len(delete)
            out["relabelled"] += len(update)
        await db.commit()   # commit per batch
    log.info("first_buyer relabel: %s", out)
    return out


# ── loop ─────────────────────────────────────────────────────────────

def any_enabled() -> bool:
    return top_traders_enabled() or relabel_enabled()


async def galaxy_roles_loop(interval_s: int = 900) -> None:
    """Started by main.py only when a flag is on; flags re-read each cycle."""
    from .db import get_db
    relabelled = False
    await asyncio.sleep(90)
    while True:
        try:
            if relabel_enabled() and not relabelled:
                async with get_db() as db:
                    await relabel_first_buyers(db)
                relabelled = True
            if top_traders_enabled():
                async with get_db() as db:
                    log.info("top_trader refresh: %s", await refresh_top_traders(db))
        except Exception as e:  # noqa: BLE001
            log.warning("galaxy roles cycle failed: %s", e)
        await asyncio.sleep(_env_int("GALAXY_TOP_TRADERS_INTERVAL_S", interval_s))
