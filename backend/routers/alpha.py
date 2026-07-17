"""
Pre-migration alpha API — the sniper's scoring + money-flow surfaces.

  • POST /api/alpha/score      — composite play-quality score for a candidate
    token from its early-curve features, gated by the Mandelbrot robustness
    filter (see backend/alpha_engine.py). This is the meta-score the scanner,
    the multi-agent evaluation, and the galaxy all read.
  • GET  /api/moneyflow        — the Money Flow Galaxy graph: wallets and tokens
    as nodes (brightness = recent activity, size = capital weight), capital
    flows as edges, built from the on-chain trades the wallet tracker ingests.

Both are auto-exposed as MCP tools (fastapi-mcp), so agents call them directly.
"""
import asyncio
import time
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent
from backend.alpha_engine import composite_alpha_score, assemble_features
from backend import wallet_blacklist as wb
from ..db import get_db

router = APIRouter(prefix="/api", tags=["alpha"])

# wallet_trades is written by pipeline/wallet_tracker.py; create it defensively
# so the money-flow graph works even before the daemon has ever run.
_WALLET_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS wallet_trades (
    signature TEXT PRIMARY KEY, wallet TEXT, timestamp INTEGER, ts_iso TEXT,
    type TEXT, source TEXT, description TEXT, fee_sol REAL, sol_change REAL,
    token_mint TEXT, token_amount REAL, raw TEXT
)
"""

# social_signals is written by daemons/social_tracker.py; create it defensively
# so the token-intel endpoint works even before that daemon has ever run.
_SOCIAL_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS social_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, platform TEXT,
    username TEXT, ticker TEXT, contract_address TEXT, sentiment TEXT,
    confidence REAL, post_text TEXT, post_url TEXT,
    signal_type TEXT DEFAULT 'mention', created_at TEXT DEFAULT (datetime('now'))
)
"""


async def _collect_signals(db, symbol: str, ca: str, hours: int) -> list:
    """Gather every live intel signal that references this token, normalised to
    the {source,type,direction,conviction,detail} shape the alpha engine reads.

    Sources folded in:
      • social_signals   — persisted Twitter/Telegram sentiment (social_tracker)
      • intel signal pool — in-memory TG/Twitter/predictor signals (intel.py)
      • trading_signals   — persisted pump.fun scanner rows (if the table exists)
    """
    sym = (symbol or "").upper().lstrip("$")
    ca_l = (ca or "").strip()
    out: list = []

    # 1. Persisted social sentiment — matched by ticker OR contract address.
    await db.execute(_SOCIAL_SIGNALS_DDL)
    where = ["UPPER(ticker) = ?"]
    params: list = [sym]
    if ca_l:
        where.append("contract_address = ?")
        params.append(ca_l)
    rows = await (await db.execute(
        f"""SELECT platform, username, sentiment, confidence, signal_type, post_text
            FROM social_signals WHERE ({' OR '.join(where)})
            AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 200""",
        (*params, f"-{int(hours)} hours"),
    )).fetchall()
    for r in rows:
        out.append({
            "source": f"social_{r['platform'] or ''}".rstrip("_"),
            "type": r["signal_type"] or "mention",
            "direction": (r["sentiment"] or "NEUTRAL").upper(),
            "conviction": float(r["confidence"] or 0.5),
            "detail": (r["post_text"] or "")[:200],
        })

    # 2. In-memory intel pool (social_tracker + telegram + predictor ingest).
    try:
        from backend.routers.intel import _signal_pool, _signal_lock
        with _signal_lock:
            pool = list(_signal_pool)
        for s in pool:
            psym = str(s.get("symbol", "")).upper().lstrip("$")
            if psym != sym and (not ca_l or psym != ca_l.upper()):
                continue
            out.append({
                "source": s.get("source", "pool"),
                "type": s.get("type", "signal"),
                "direction": (s.get("direction") or "").upper(),
                "conviction": float(s.get("conviction", 0.5) or 0.5),
                "detail": str(s.get("detail", ""))[:200],
            })
    except Exception:
        pass

    # 3. Persisted pump.fun scanner rows (best-effort; schema varies).
    try:
        trows = await (await db.execute(
            "SELECT * FROM trading_signals WHERE UPPER(symbol) = ? "
            "ORDER BY rowid DESC LIMIT 50", (sym,))).fetchall()
        for r in trows:
            d = dict(r)
            out.append({
                "source": d.get("source", "trading_signals"),
                "type": d.get("type", "pumpfun"),
                "direction": str(d.get("direction", d.get("signal", ""))).upper(),
                "conviction": float(d.get("conviction", d.get("confidence", 0.5)) or 0.5),
                "detail": str(d.get("detail", d.get("reason", "")))[:200],
            })
    except aiosqlite.Error:
        pass

    return out


async def _velocity_from_trades(db, ca: str, hours: int) -> Optional[float]:
    """Buy-side velocity 0..1 from on-chain wallet_trades on this mint: how many
    distinct wallets bought it inside the window, normalised (≥12 wallets → 1.0).
    Returns None when the mint has no observed trades (no hint)."""
    if not ca:
        return None
    since = int(time.time()) - hours * 3600
    await db.execute(_WALLET_TRADES_DDL)
    row = await (await db.execute(
        """SELECT COUNT(DISTINCT wallet) FROM wallet_trades
           WHERE token_mint = ? AND timestamp >= ? AND sol_change < 0""",
        (ca, since))).fetchone()
    buyers = int(row[0] or 0) if row else 0
    if buyers == 0:
        return None
    return min(1.0, buyers / 12.0)


class AlphaFeatures(BaseModel):
    # All 0..1. Provided by the scanner / wallet-profiler; documented so agents
    # know exactly what each axis means.
    wallet_quality: float = 0.5    # creator + early-holder + smart-money quality
    velocity: float = 0.5          # bonding-curve fill / buy velocity (healthy momentum)
    concentration: float = 0.5     # holder concentration (LOWER is better)
    social: float = 0.5            # TG/Twitter momentum weighted by influencer PV
    security: float = 0.5          # mint/freeze/LP/rug checks (HIGHER is safer)
    ca: str = ""                   # optional contract address, echoed back
    symbol: str = ""


@router.post("/alpha/score", operation_id="score_token_alpha")
async def score_alpha(f: AlphaFeatures, _caller: dict = Depends(get_agent)):
    """Composite pre-migration alpha score + Mandelbrot robustness verdict."""
    result = composite_alpha_score(
        wallet_quality=f.wallet_quality, velocity=f.velocity,
        concentration=f.concentration, social=f.social, security=f.security,
    )
    if f.ca:
        result["ca"] = f.ca
    if f.symbol:
        result["symbol"] = f.symbol
    return result


@router.get("/alpha/token/{ident}", operation_id="score_token_from_intel")
async def score_token_from_intel(
    ident: str,
    _caller: dict = Depends(get_agent),
    ca: str = Query("", description="Contract address (mint) if ident is a ticker"),
    hours: int = Query(24, ge=1, le=336, description="Lookback window for intel"),
    wallet_quality: Optional[float] = Query(None, ge=0, le=1),
    velocity: Optional[float] = Query(None, ge=0, le=1),
    concentration: Optional[float] = Query(None, ge=0, le=1),
    social: Optional[float] = Query(None, ge=0, le=1),
    security: Optional[float] = Query(None, ge=0, le=1),
):
    """Score a candidate token straight from the live incoming intel.

    Assembles the five alpha features from the real signal stream the daemons
    feed (social_signals sentiment, the intel signal pool, pump.fun rows, and
    on-chain wallet_trades velocity), then runs the same Mandelbrot-gated
    composite scorer as POST /alpha/score. Any feature the scanner already knows
    on-chain (wallet_quality, concentration, …) can be pinned via query params;
    everything else is derived here. The response echoes per-feature provenance
    and the exact signals that contributed, so a grade is always explainable.
    """
    # `ident` may be a ticker (SOL address goes in ?ca=) or the mint itself.
    is_addr = len(ident) >= 32 and not ident.isalpha()
    symbol = "" if is_addr else ident
    mint = ca or (ident if is_addr else "")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        signals = await _collect_signals(db, symbol or ident, mint, hours)
        velocity_hint = await _velocity_from_trades(db, mint, hours)

    overrides = {
        "wallet_quality": wallet_quality, "velocity": velocity,
        "concentration": concentration, "social": social, "security": security,
    }
    features, provenance = assemble_features(signals, overrides, velocity_hint)
    result = composite_alpha_score(**features)

    result["symbol"] = symbol or ident
    if mint:
        result["ca"] = mint
    result["provenance"] = provenance
    result["signal_count"] = len(signals)
    result["signals"] = signals[:25]
    result["window_hours"] = hours
    return result


# ── Token lifecycle tiers — market-cap + age bucketing for the money-flow
# galaxy's token nodes. DexScreener's per-mint pairs endpoint returns fdv/
# marketCap directly (no supply lookup needed), cached 60s like the rest of
# market_sources. Best-effort: a token with no DexScreener listing yet (fresh
# pump.fun mint, pre-liquidity) just has no market_cap/tier and the frontend
# treats it as "just_launch" from age alone. ──────────────────────────────────
_TIER_BOUNDARIES = [
    (1_000_000, "migrated_1m"), (10_000_000, "migrated_10m"),
    (20_000_000, "migrated_20m"), (100_000_000, "migrated_100m"),
    (500_000_000, "migrated_500m"), (1_000_000_000, "migrated_1b"),
]


def _classify_tier(market_cap: Optional[float], first_seen_ts: int, now: int) -> str:
    age_h = (now - first_seen_ts) / 3600 if first_seen_ts else 999
    if age_h < 1:
        return "just_launch"
    if not market_cap or market_cap <= 0:
        return "pumpfun_10k_20k" if age_h < 24 else "pre_migration"
    for cap, tier in _TIER_BOUNDARIES:
        if market_cap < cap:
            return tier
    return "billion_club"


async def _dexscreener_mcap(mint: str) -> Optional[float]:
    """Best-effort market cap for a single mint via DexScreener's token
    endpoint (fdv/marketCap on the highest-liquidity pair). None on any
    failure — never blocks the graph on a flaky/missing listing."""
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                                  headers={"User-Agent": "Vantage/1.0"})
            if r.status_code != 200:
                return None
            pairs = (r.json() or {}).get("pairs") or []
            if not pairs:
                return None
            best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            return best.get("marketCap") or best.get("fdv")
    except Exception:
        return None


@router.get("/moneyflow", operation_id="money_flow_graph")
async def money_flow(
    _caller: dict = Depends(get_agent),
    hours: int = Query(24, ge=1, le=720, description="Activity window for brightness"),
    limit: int = Query(400, le=2000, description="Max trades to fold in"),
    tier_lookups: int = Query(15, ge=0, le=40, description="Top-N tokens by volume to fetch live market-cap tiers for"),
    include_counterparties: bool = Query(True, description="Fold in wallet_edges (all /trace + watchlist counterparty activity, not just on-chain trades)"),
    edge_limit: int = Query(600, le=2000, description="Max wallet_edges rows to fold in"),
    exclude_exchanges: bool = Query(True, description="Exclude major exchange/custodian wallets — they're false hubs (everything routes through them) with no follow-the-money value"),
):
    """Money Flow Galaxy graph: wallets + tokens + social accounts as nodes,
    capital flows and mentions as edges.

    Two wallet data sources feed this, merged into one graph:
      1. wallet_trades — on-chain Solana swaps for the small set of wallets
         wallet_tracker.py actively polls via Helius (rich: SOL amounts, token
         mints, timestamps).
      2. wallet_edges — every counterparty any /trace lookup or
         /watchlist/refresh has ever surfaced, across all 259+ tracked_wallets
         (broader coverage, coarser data: tx_count + total_value only). This is
         the ONLY source for most tracked wallets, so it's included by default
         — without it the graph silently drops to just the ~2 wallets with
         on-chain trade history and looks like tracked wallets vanished.

    Node `brightness` (0..1) is recent-activity share; `size` (0..1) is capital
    weight — this is the Mandelbrot fade: dormant nodes drop toward 0 brightness
    and sink toward the background nebulae, active ones glow and stay large.
    Wallet nodes carry `degen_score`/`trade_count`/`unique_tokens` from the
    watchlist. Token nodes carry `tier` (lifecycle/market-cap bucket) for the
    top `tier_lookups` tokens by volume. Social nodes (Twitter/Telegram
    handles that mentioned a token) link into the token they mentioned, from
    the same social_signals table social_tracker.py's daemon already fills.
    """
    now = int(time.time())
    since = now - hours * 3600
    async with get_db() as db:
        await db.execute(_WALLET_TRADES_DDL)
        await db.execute(_SOCIAL_SIGNALS_DDL)
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (await db.execute(
            """SELECT wallet, token_mint, sol_change, token_amount, timestamp, type
               FROM wallet_trades ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        )).fetchall()]

        counterparty_rows: list[dict] = []
        if include_counterparties:
            try:
                counterparty_rows = [dict(r) for r in await (await db.execute(
                    """SELECT chain, address_a, address_b, role, tx_count, total_value,
                              first_seen, last_seen FROM wallet_edges
                       ORDER BY total_value DESC LIMIT ?""",
                    (edge_limit,),
                )).fetchall()]
            except aiosqlite.Error:
                pass

        # Every tracked wallet, even ones with zero observed edges yet — so
        # adding a wallet via the watchlist makes it appear immediately.
        tracked_rows: list[dict] = []
        try:
            tracked_rows = [dict(r) for r in await (await db.execute(
                "SELECT chain, address, label, address_type, degen_score, "
                "trade_count, unique_tokens, notes, balance_usd FROM tracked_wallets")).fetchall()]
        except aiosqlite.Error:
            pass

        # Wallet profile enrichment — label + degen intel already gathered by
        # pumpfun_wallet_intel.py / degen_alpha_fusion.py into tracked_wallets.
        wallet_meta: dict = {r["address"]: r for r in tracked_rows}

        # Exchange blacklist — explicit table + pattern-matched labels
        # (backend/wallet_blacklist.py, shared with degen.py's smart-wallets
        # filter). Major exchanges are false hubs: everything routes through
        # them eventually, so including them makes every wallet look
        # connected to every other wallet and drowns out real signal.
        blacklisted: set = set()
        if exclude_exchanges:
            blacklisted = wb.get_blacklisted_addresses("solana")
            for addr, meta in wallet_meta.items():
                if meta.get("address_type") == "exchange" or wb.is_exchange_label(meta.get("label", "")):
                    blacklisted.add(addr)

        # Social mentions — Twitter/Telegram → CA, from social_tracker.py.
        social_rows = [dict(r) for r in await (await db.execute(
            """SELECT platform, username, ticker, contract_address, sentiment,
                      confidence, post_url, created_at
               FROM social_signals
               WHERE created_at >= datetime('now', ?)
               ORDER BY id DESC LIMIT 500""",
            (f"-{int(hours)} hours",),
        )).fetchall()]

        # Meaningful token wallets — deployer/top_holder/top_trader/first_buyer,
        # from pumpfun_wallet_intel.py. These create BOTH the token node (even
        # if it has no trade activity yet) and a typed wallet→token edge, so
        # "every token that pops up as a top play/signal gets parsed and its
        # deployer/first-buyers/top-holders put in the graph" holds even
        # before any on-chain trade has been observed for it.
        role_rows: list[dict] = []
        try:
            role_rows = [dict(r) for r in await (await db.execute(
                """SELECT mint, symbol, wallet_address, role, rank, metric, metric_label
                   FROM token_wallet_roles
                   WHERE discovered_at >= datetime('now', ?)
                   ORDER BY discovered_at DESC LIMIT 500""",
                (f"-{int(hours)} hours",),
            )).fetchall()]
        except aiosqlite.Error:
            pass

        # Wallets a social account has claimed as its own (PnL-post address
        # extraction) — a real social→wallet edge, "tie their wallet to
        # their node in the graph."
        claim_rows: list[dict] = []
        try:
            claim_rows = [dict(r) for r in await (await db.execute(
                """SELECT platform, username, wallet_address, chain, post_url, post_excerpt
                   FROM social_wallet_links
                   WHERE extracted_at >= datetime('now', ?)
                   ORDER BY extracted_at DESC LIMIT 300""",
                (f"-{int(hours)} hours",),
            )).fetchall()]
        except aiosqlite.Error:
            pass

        # Migration state — has this mint ever crossed the real pump.fun
        # graduation threshold, and what was its mcap at that moment? Used
        # below to tell "still climbing toward migration" apart from "was
        # migrated, then rugged/collapsed back" — those need very different
        # graph treatment (drifting closer vs. fading into the dormant void).
        migration_state: dict = {}
        try:
            migration_state = {
                r["mint"]: dict(r) for r in await (await db.execute(
                    "SELECT mint, migration_mcap, peak_mcap FROM token_migration_state"
                )).fetchall()
            }
        except aiosqlite.Error:
            pass

    nodes: dict = {}
    edges: dict = {}
    first_seen: dict = {}
    last_seen: dict = {}  # max activity ts per node — dormancy fade uses this, not first_seen

    def wallet_extra(addr: str) -> dict:
        wm = wallet_meta.get(addr, {})
        return {
            "address": addr, "chain": wm.get("chain", "solana"),
            "degen_score": wm.get("degen_score", 0),
            "trade_count": wm.get("trade_count", 0),
            "unique_tokens": wm.get("unique_tokens", 0),
            "address_type": wm.get("address_type", "wallet"),
            "balance_usd": wm.get("balance_usd") or 0,
        }

    def wallet_label(addr: str) -> str:
        wm = wallet_meta.get(addr, {})
        return wm.get("label") or (f"{addr[:4]}…{addr[-4:]}" if len(addr) > 8 else addr)

    def touch(nid: str, ntype: str, label: str, sol: float, ts: int, extra: Optional[dict] = None) -> None:
        n = nodes.setdefault(nid, {
            "id": nid, "type": ntype, "label": label,
            "trades": 0, "recent": 0, "volume_sol": 0.0, **(extra or {}),
        })
        n["trades"] += 1
        n["volume_sol"] += abs(sol)
        if ts >= since:
            n["recent"] += 1
        first_seen[nid] = min(first_seen.get(nid, ts or now), ts or now)
        last_seen[nid] = max(last_seen.get(nid, ts or 0), ts or 0)

    def parse_sqlite_ts(s: Optional[str]) -> int:
        if not s:
            return now
        try:
            import datetime as _dt
            return int(_dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc).timestamp())
        except Exception:
            return now

    # 1. On-chain trades (richest: real SOL amounts + token mints).
    for r in rows:
        w = r.get("wallet")
        mint = r.get("token_mint")
        sol = float(r.get("sol_change") or 0.0)
        ts = int(r.get("timestamp") or 0)
        if not w or w in blacklisted:
            continue
        touch(f"wallet:{w}", "wallet", wallet_label(w), sol, ts, wallet_extra(w))
        if mint:
            touch(f"token:{mint}", "token", f"{mint[:4]}…{mint[-4:]}" if len(mint) > 8 else mint, sol, ts, {
                "ca": mint, "chain": "solana",
            })
            key = (f"wallet:{w}", f"token:{mint}")
            e = edges.setdefault(key, {"source": key[0], "target": key[1], "type": "traded", "trades": 0, "volume_sol": 0.0, "net_sol": 0.0, "last_ts": 0})
            e["trades"] += 1
            e["volume_sol"] += abs(sol)
            e["net_sol"] += sol  # +ve = wallet received SOL (sold token), -ve = bought
            e["last_ts"] = max(e["last_ts"], ts)

    # 2. Wallet↔wallet counterparties from wallet_edges — every /trace and
    # /watchlist/refresh lookup ever surfaced, across all tracked wallets.
    # This is the primary coverage source (259+ wallets) vs. the handful with
    # on-chain trade rows above, so it must stay on by default.
    for r in counterparty_rows:
        a, b = r.get("address_a"), r.get("address_b")
        if not a or not b or a in blacklisted or b in blacklisted:
            continue
        ts = parse_sqlite_ts(r.get("last_seen"))
        tx_count = int(r.get("tx_count") or 0)
        value = float(r.get("total_value") or 0.0)
        touch(f"wallet:{a}", "wallet", wallet_label(a), value, ts, wallet_extra(a))
        touch(f"wallet:{b}", "wallet", wallet_label(b), 0.0, ts, wallet_extra(b))
        key = (f"wallet:{a}", f"wallet:{b}")
        e = edges.setdefault(key, {"source": key[0], "target": key[1], "type": "counterparty", "trades": 0, "volume_sol": 0.0, "net_sol": 0.0, "role": r.get("role"), "last_ts": 0})
        e["trades"] += tx_count
        e["volume_sol"] += value
        e["last_ts"] = max(e["last_ts"], ts)

    # 3. Every tracked wallet gets a node even with zero observed activity —
    # so adding a wallet to the watchlist makes it appear immediately instead
    # of waiting on a trace/refresh to happen first. Brightness naturally
    # comes out 0 (dormant) until it has real activity, which is the correct
    # Mandelbrot-fade behavior, not a bug.
    for r in tracked_rows:
        addr = r.get("address")
        if not addr or addr in blacklisted:
            continue
        nid = f"wallet:{addr}"
        if nid not in nodes:
            nodes[nid] = {
                "id": nid, "type": "wallet", "label": wallet_label(addr),
                "trades": 0, "recent": 0, "volume_sol": 0.0, **wallet_extra(addr),
            }
            first_seen[nid] = now

    # Fold in social mentions as nodes linked to the token they mentioned —
    # only for tokens/tickers that also appear as trade nodes (keeps the graph
    # to entities with real capital flow, not every drive-by tweet).
    mint_by_ticker: dict = {}
    for nid, n in nodes.items():
        if n["type"] == "token":
            mint_by_ticker.setdefault(n["label"].upper(), nid)
    for s in social_rows:
        ca = (s.get("contract_address") or "").strip()
        ticker = (s.get("ticker") or "").strip().upper().lstrip("$")
        token_nid = f"token:{ca}" if ca and f"token:{ca}" in nodes else mint_by_ticker.get(ticker)
        if not token_nid:
            continue
        platform = s.get("platform") or "social"
        username = s.get("username") or "unknown"
        social_nid = f"social:{platform}:{username}"
        sn = nodes.setdefault(social_nid, {
            "id": social_nid, "type": "social", "label": f"@{username}",
            "platform": platform, "trades": 0, "recent": 0, "volume_sol": 0.0,
            "mentions": 0,
        })
        sn["mentions"] += 1
        sn["recent"] += 1  # social rows are already window-filtered by the query
        first_seen[social_nid] = min(first_seen.get(social_nid, now), now)
        key = (social_nid, token_nid)
        e = edges.setdefault(key, {"source": social_nid, "target": token_nid, "type": "mentioned", "trades": 0, "volume_sol": 0.0, "net_sol": 0.0, "sentiment": s.get("sentiment"), "last_ts": now})
        e["trades"] += 1
        e["last_ts"] = now

    # Meaningful token wallets — deployer/top_holder/top_trader/first_buyer.
    # Creates the token node too if it doesn't exist yet (a token can show up
    # here from signal-driven enrichment before any trade has been observed).
    ROLE_EDGE_STYLE = {
        "deployer": {"color": "#f5a623"}, "top_holder": {"color": "#a855f7"},
        "top_trader": {"color": "#4ade80"}, "first_buyer": {"color": "#22d3ee"},
    }
    for r in role_rows:
        mint, wallet = r.get("mint"), r.get("wallet_address")
        if not mint or not wallet or wallet in blacklisted:
            continue
        token_nid = f"token:{mint}"
        if token_nid not in nodes:
            nodes[token_nid] = {
                "id": token_nid, "type": "token",
                "label": r.get("symbol") or f"{mint[:4]}…{mint[-4:]}",
                "ca": mint, "chain": "solana",
                "trades": 0, "recent": 1, "volume_sol": 0.0,
            }
            first_seen[token_nid] = now
        wallet_nid = f"wallet:{wallet}"
        if wallet_nid not in nodes:
            nodes[wallet_nid] = {"id": wallet_nid, "type": "wallet", "label": wallet_label(wallet),
                                  **wallet_extra(wallet), "trades": 0, "recent": 1, "volume_sol": 0.0}
            first_seen[wallet_nid] = now
        role = r.get("role", "")
        key = (wallet_nid, token_nid, role)
        edges[key] = {
            "source": wallet_nid, "target": token_nid, "type": f"role:{role}",
            "trades": 0, "volume_sol": 0.0, "net_sol": 0.0,
            "role": role, "rank": r.get("rank"), "metric": r.get("metric"), "metric_label": r.get("metric_label"),
            "color": ROLE_EDGE_STYLE.get(role, {}).get("color"),
            "last_ts": now,
        }

    # Wallets a social account has claimed as its own via PnL-style posts.
    for r in claim_rows:
        wallet, platform, username = r.get("wallet_address"), r.get("platform") or "social", r.get("username") or "unknown"
        if not wallet or wallet in blacklisted:
            continue
        wallet_nid = f"wallet:{wallet}"
        if wallet_nid not in nodes:
            nodes[wallet_nid] = {"id": wallet_nid, "type": "wallet", "label": wallet_label(wallet),
                                  **wallet_extra(wallet), "trades": 0, "recent": 1, "volume_sol": 0.0}
            first_seen[wallet_nid] = now
        social_nid = f"social:{platform}:{username}"
        sn = nodes.setdefault(social_nid, {
            "id": social_nid, "type": "social", "label": f"@{username}",
            "platform": platform, "trades": 0, "recent": 0, "volume_sol": 0.0, "mentions": 0,
        })
        sn["mentions"] += 1
        sn["recent"] += 1
        first_seen[social_nid] = min(first_seen.get(social_nid, now), now)
        key = (social_nid, wallet_nid)
        edges[key] = {
            "source": social_nid, "target": wallet_nid, "type": "claimed_wallet",
            "trades": edges.get(key, {}).get("trades", 0) + 1, "volume_sol": 0.0, "net_sol": 0.0,
            "post_url": r.get("post_url"), "post_excerpt": r.get("post_excerpt"),
            "color": "#ec4899", "last_ts": now,
        }

    # Live market-cap tiers for the top N token nodes by volume (bounded,
    # concurrent, fail-soft — a dead DexScreener listing just leaves tier unset).
    token_nodes = sorted((n for n in nodes.values() if n["type"] == "token"),
                         key=lambda n: -n["volume_sol"])
    lookup_targets = token_nodes[:tier_lookups]
    if lookup_targets:
        mcaps = await asyncio.gather(*[_dexscreener_mcap(n["ca"]) for n in lookup_targets],
                                      return_exceptions=True)
        for n, mcap in zip(lookup_targets, mcaps):
            mc = mcap if isinstance(mcap, (int, float)) else None
            n["market_cap"] = mc
            n["tier"] = _classify_tier(mc, first_seen.get(n["id"], now), now)
    for n in token_nodes[tier_lookups:]:
        n["tier"] = _classify_tier(None, first_seen.get(n["id"], now), now)

    # ── Migration gravity — Raydium, not a CEX wallet, is the real anchor.
    # Pump.fun graduations move their bonding-curve LP to Raydium specifically
    # (the on-chain destination) — Jupiter is a swap router with no fixed
    # liquidity venue of its own, and DexScreener is an analytics site with
    # no on-chain presence at all, so neither is a real "place tokens migrate
    # to." CEX wallets (Binance/OKX/etc, still in tracked_wallets) are a
    # different, legitimate thing — where profit-taking flows go — kept as
    # ordinary wallet nodes, just no longer used as the physics anchor.
    #
    # Distance is now continuous by real market cap for anything that has
    # crossed the actual pump.fun graduation threshold (~$69k, same value
    # /pumpfun/bonding-curve already uses), log-interpolated between the
    # graduation floor (0.15) and $1B (0.01) — replacing the old discrete
    # per-tier snap, which bucketed a $70k token and a $900k token
    # identically. Below that floor, the old discrete pre-migration
    # distances still apply (that phase is about launch/curve progress, not
    # market cap, so continuous mcap distance doesn't mean anything there).
    #
    # Dormant void: a token that DID cross the graduation floor at some
    # point (has a token_migration_state row) but has since fallen back
    # under the rug floor ($7k) gets pushed far out and flagged rugged —
    # distinct from a token that simply hasn't migrated yet.
    MIGRATION_MCAP_FLOOR = 69_000.0   # pump.fun's real graduation threshold
    RUG_MCAP_FLOOR = 7_000.0          # user-specified "fell back under this = dead"
    PRE_MIGRATION_DISTANCE_BY_TIER = {
        "just_launch": 1.0, "pumpfun_10k_20k": 0.8, "pre_migration": 0.55,
    }
    RAYDIUM_ANCHOR_ADDRESS = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"  # Raydium AMM V4 program

    import math

    def _migration_distance_and_state(n: dict) -> tuple[float, bool]:
        """Returns (distance, is_dormant_void)."""
        mint = n.get("ca")
        mc = n.get("market_cap")
        prior = migration_state.get(mint) if mint else None
        if mc and mc >= MIGRATION_MCAP_FLOOR:
            t = (math.log10(mc) - math.log10(MIGRATION_MCAP_FLOOR)) / (math.log10(1_000_000_000) - math.log10(MIGRATION_MCAP_FLOOR))
            t = min(1.0, max(0.0, t))
            return round(0.15 - t * (0.15 - 0.01), 4), False
        if prior and mc is not None and mc < RUG_MCAP_FLOOR:
            # Previously graduated, now collapsed — dormant void, not just "far".
            return 2.0, True
        return PRE_MIGRATION_DISTANCE_BY_TIER.get(n.get("tier"), 0.6), False

    anchor_nid = f"exchange:raydium"
    nodes[anchor_nid] = {
        "id": anchor_nid, "type": "exchange", "label": "Raydium",
        "address": RAYDIUM_ANCHOR_ADDRESS, "chain": "solana",
        "trades": 0, "recent": 1, "volume_sol": 0.0, "is_migration_anchor": True,
    }
    first_seen[anchor_nid] = now
    last_seen[anchor_nid] = now

    migration_updates = []
    for n in token_nodes:
        dist, dormant_void = _migration_distance_and_state(n)
        n["migration_distance"] = dist
        n["dormant_void"] = dormant_void
        mc = n.get("market_cap")
        mint = n.get("ca")
        if mc and mc >= MIGRATION_MCAP_FLOOR and mint:
            prior = migration_state.get(mint)
            migration_updates.append((mint, mc, prior))
        key = (n["id"], anchor_nid)
        edges[key] = {
            "source": n["id"], "target": anchor_nid, "type": "migration_gravity",
            "trades": 0, "volume_sol": 0.0, "net_sol": 0.0,
            "migration_distance": dist, "last_ts": now,
        }

    if migration_updates:
        async with get_db() as db2:
            await db2.execute("PRAGMA busy_timeout=15000")  # real contention from ~15 concurrent daemons, not a stuck lock — verified 3.5s typical wait
            for mint, mc, prior in migration_updates:
                if prior is None:
                    await db2.execute(
                        "INSERT INTO token_migration_state (mint, migration_mcap, peak_mcap) VALUES (?,?,?) "
                        "ON CONFLICT(mint) DO NOTHING",
                        (mint, mc, mc),
                    )
                elif mc > (prior.get("peak_mcap") or 0):
                    await db2.execute(
                        "UPDATE token_migration_state SET peak_mcap=?, updated_at=datetime('now') WHERE mint=?",
                        (mc, mint),
                    )
            await db2.commit()

    # ── Wallet dormancy — "don't hoard wallets." Active (touched within 3
    # days) wallets are untouched. Dormant + balance >= $10k dim (low
    # brightness) but stay, so real whale positions don't disappear just
    # because they're between trades. Dormant + low/unknown balance are
    # dropped entirely — every tracked wallet used to get a permanent node
    # the moment it was added regardless of activity, which is exactly the
    # hoarding this removes. Never drops the Raydium anchor or any node with
    # real capital weight already established this render (size uses
    # volume_sol, computed after this filter, so this only looks at raw
    # activity/balance signals, not the derived score).
    DORMANT_AFTER_SECONDS = 3 * 86400
    DORMANT_SURVIVE_BALANCE_USD = 10_000.0
    excluded_ids: set = set()
    for nid, n in nodes.items():
        if n["type"] != "wallet" or n.get("is_migration_anchor"):
            continue
        last_active = last_seen.get(nid, 0)
        is_dormant = (now - last_active) > DORMANT_AFTER_SECONDS
        if not is_dormant:
            continue
        balance = n.get("balance_usd") or 0
        if balance >= DORMANT_SURVIVE_BALANCE_USD:
            n["dormant_dim"] = True  # frontend caps brightness for these
        else:
            excluded_ids.add(nid)
    if excluded_ids:
        for nid in excluded_ids:
            nodes.pop(nid, None)
        edges = {k: e for k, e in edges.items() if e["source"] not in excluded_ids and e["target"] not in excluded_ids}

    # Normalise brightness (recent activity share) and size (capital weight) —
    # the Mandelbrot fade: dormant nodes → brightness 0, sink into background.
    max_recent = max((n["recent"] for n in nodes.values()), default=0) or 1
    max_vol = max((n["volume_sol"] for n in nodes.values()), default=0.0) or 1.0
    max_mentions = max((n.get("mentions", 0) for n in nodes.values()), default=0) or 1
    node_list = []
    for n in nodes.values():
        n["brightness"] = round(n["recent"] / max_recent, 4)
        if n.get("dormant_dim"):
            n["brightness"] = min(n["brightness"], 0.12)  # visible, but clearly not active
        vol_component = n["volume_sol"] / max_vol
        mention_component = n.get("mentions", 0) / max_mentions if n["type"] == "social" else 0
        n["size"] = round(max(vol_component, mention_component) ** 0.5, 4)  # sqrt for gentler spread
        n["volume_sol"] = round(n["volume_sol"], 4)
        n["first_seen"] = first_seen.get(n["id"], now)
        n["last_seen"] = last_seen.get(n["id"], n["first_seen"])
        node_list.append(n)
    node_list.sort(key=lambda x: -x["size"])
    edge_list = [
        {**e, "volume_sol": round(e["volume_sol"], 4), "net_sol": round(e["net_sol"], 4)}
        for e in edges.values()
    ]

    return {
        "nodes": node_list,
        "edges": edge_list,
        "wallets": sum(1 for n in node_list if n["type"] == "wallet"),
        "tokens": sum(1 for n in node_list if n["type"] == "token"),
        "social": sum(1 for n in node_list if n["type"] == "social"),
        "window_hours": hours,
        "generated_at": now,
    }
