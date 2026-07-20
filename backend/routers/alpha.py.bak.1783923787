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
import time
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent
from backend.alpha_engine import composite_alpha_score, assemble_features

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

    async with aiosqlite.connect(DB_PATH) as db:
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


@router.get("/moneyflow", operation_id="money_flow_graph")
async def money_flow(
    _caller: dict = Depends(get_agent),
    hours: int = Query(24, ge=1, le=720, description="Activity window for brightness"),
    limit: int = Query(400, le=2000, description="Max trades to fold in"),
):
    """Money Flow Galaxy graph: wallets + tokens as nodes, capital flows as edges.

    Node `brightness` (0..1) is recent-activity share; `size` (0..1) is capital
    weight. The frontend maps brightness→glow and size→radius; inactive nodes
    fade toward the background nebulae exactly as specified.
    """
    since = int(time.time()) - hours * 3600
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_WALLET_TRADES_DDL)
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (await db.execute(
            """SELECT wallet, token_mint, sol_change, token_amount, timestamp, type
               FROM wallet_trades ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        )).fetchall()]
        # Human labels for known wallets.
        labels: dict = {}
        try:
            for r in await (await db.execute(
                "SELECT address, label FROM tracked_wallets WHERE label != ''")).fetchall():
                labels[r[0]] = r[1]
        except aiosqlite.Error:
            pass

    nodes: dict = {}
    edges: dict = {}

    def touch(nid: str, ntype: str, label: str, sol: float, ts: int) -> None:
        n = nodes.setdefault(nid, {
            "id": nid, "type": ntype, "label": label,
            "trades": 0, "recent": 0, "volume_sol": 0.0,
        })
        n["trades"] += 1
        n["volume_sol"] += abs(sol)
        if ts >= since:
            n["recent"] += 1

    for r in rows:
        w = r.get("wallet")
        mint = r.get("token_mint")
        sol = float(r.get("sol_change") or 0.0)
        ts = int(r.get("timestamp") or 0)
        if not w:
            continue
        wlabel = labels.get(w) or (f"{w[:4]}…{w[-4:]}" if len(w) > 8 else w)
        touch(f"wallet:{w}", "wallet", wlabel, sol, ts)
        if mint:
            touch(f"token:{mint}", "token", f"{mint[:4]}…{mint[-4:]}" if len(mint) > 8 else mint, sol, ts)
            key = (f"wallet:{w}", f"token:{mint}")
            e = edges.setdefault(key, {"source": key[0], "target": key[1], "trades": 0, "volume_sol": 0.0, "net_sol": 0.0})
            e["trades"] += 1
            e["volume_sol"] += abs(sol)
            e["net_sol"] += sol  # +ve = wallet received SOL (sold token), -ve = bought

    # Normalise brightness (recent activity share) and size (capital weight).
    max_recent = max((n["recent"] for n in nodes.values()), default=0) or 1
    max_vol = max((n["volume_sol"] for n in nodes.values()), default=0.0) or 1.0
    node_list = []
    for n in nodes.values():
        n["brightness"] = round(n["recent"] / max_recent, 4)
        n["size"] = round((n["volume_sol"] / max_vol) ** 0.5, 4)  # sqrt for gentler spread
        n["volume_sol"] = round(n["volume_sol"], 4)
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
        "window_hours": hours,
    }
