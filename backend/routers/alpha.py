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
from backend.alpha_engine import composite_alpha_score

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
