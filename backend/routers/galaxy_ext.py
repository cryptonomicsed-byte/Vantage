"""
Galaxy Extension API — social linking, early-buyer backtracking, and clustering
endpoints that feed the Money Flow / Galaxy graph.

Mounted at /api/alpha (same prefix as moneyflow) so the frontend can reach
them via the same base URL.
"""
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from backend.db import get_db
from backend.deps import get_agent
from backend import social_linker, early_buyer, wallet_cluster_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alpha", tags=["galaxy_ext"])


# ── Social-to-chain linker ────────────────────────────────────────────────

class SocialLinkRequest(BaseModel):
    handle: str
    platform: str = "twitter"   # "twitter" | "telegram"
    refresh: bool = False       # force re-scan even if cached results exist


@router.post("/social-link")
async def social_link(
    req: SocialLinkRequest,
    _caller: dict = Depends(get_agent),
):
    """
    Discover Solana wallets linked to a social handle.

    - Mines existing social_signals and social_wallet_links tables (fast, cached).
    - For Telegram + refresh=true, also scrapes the public channel page fresh.
    - Injects discovered wallets into wallet_edges so they appear in /moneyflow
      immediately as nodes linked to the social handle node.

    Returns: {handle, platform, wallets: [addr], wallet_details: [...], new: int, cached: bool}

    Seed a handle:
        POST /api/alpha/social-link
        {"handle": "cryptoinfluencer", "platform": "twitter"}

    See results in Galaxy graph:
        GET /api/alpha/moneyflow?twitter_handle=cryptoinfluencer
    """
    if not req.handle.strip():
        raise HTTPException(400, "handle is required")
    return await social_linker.link_handle(
        handle=req.handle,
        platform=req.platform,
        refresh=req.refresh,
    )


@router.get("/social-link/{handle}")
async def get_social_links(
    handle: str,
    platform: str = Query("twitter"),
    _caller: dict = Depends(get_agent),
):
    """Get cached wallet links for a social handle."""
    wallets = await social_linker.get_linked_wallets(handle, platform)
    return {"handle": handle.lower().strip("@"), "platform": platform, "wallets": wallets}


# ── Early-buyer backtracker ───────────────────────────────────────────────

class BacktrackRequest(BaseModel):
    mint: str
    max_buyers: int = 50
    dry_run: bool = False       # return mock data without Helius calls (for testing)


@router.post("/backtrack")
async def backtrack_mint(
    req: BacktrackRequest,
    _caller: dict = Depends(get_agent),
):
    """
    Walk the earliest transactions for a token mint and identify first buyers.

    Uses Helius to paginate getSignaturesForAddress(mint), parse token
    transfers, extract buyer wallets, rank them by entry timestamp, and
    identify who funded each wallet. Results are stored in token_early_buyers
    and injected into token_wallet_roles + wallet_edges so they appear in the
    Galaxy graph as first_buyer nodes.

    Returns:
        {mint, buyers: [{wallet_address, entry_rank, entry_ts, sol_spent,
          token_amount, tx_signature, funded_by, is_major}], new: int, total: int}

    Seed a mint:
        POST /api/alpha/backtrack
        {"mint": "TokenMintAddressHere", "dry_run": true}

    See results in Galaxy graph:
        GET /api/alpha/moneyflow?mint_backtrack=TokenMintAddressHere&early_buyers_only=true
    """
    if len(req.mint) < 32:
        raise HTTPException(400, "mint must be a valid Solana address")
    if req.max_buyers < 1 or req.max_buyers > 200:
        raise HTTPException(400, "max_buyers must be 1..200")

    return await early_buyer.backtrack_early_buyers(
        mint=req.mint,
        max_buyers=req.max_buyers,
        dry_run=req.dry_run,
    )


@router.get("/early-buyers/{mint}")
async def get_early_buyers(
    mint: str,
    _caller: dict = Depends(get_agent),
):
    """Get cached early buyers for a token mint (no new Helius calls)."""
    buyers = await early_buyer.get_cached_early_buyers(mint)
    return {"mint": mint, "buyers": buyers, "total": len(buyers)}


# ── Wallet clusters ───────────────────────────────────────────────────────

@router.post("/clusters/rebuild")
async def rebuild_clusters(
    max_wallets: int = Query(5000, ge=100, le=50000),
    _caller: dict = Depends(get_agent),
):
    """
    Rebuild wallet clusters from scratch using three signals:
      1. Shared funding source (wallet_edges role='funded')
      2. Co-trading temporal windows (wallet_trades + token_early_buyers)
      3. Behavioral fingerprints (tracked_wallets degen_score / trade_count)

    This is a write-heavy operation; expect 5-30s depending on DB size.
    Results are stored in wallet_clusters and immediately visible in /moneyflow.

    Returns: {clusters_found, wallets_clustered, elapsed_sec}
    """
    return await wallet_cluster_engine.rebuild_clusters(max_wallets=max_wallets)


@router.get("/clusters")
async def list_clusters(
    limit: int = Query(100, ge=1, le=1000),
    _caller: dict = Depends(get_agent),
):
    """List all clusters with their member count and tags."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            await db.execute(wallet_cluster_engine._CLUSTERS_DDL)
            rows = await (await db.execute(
                """SELECT cluster_id, cluster_label, wallet_category,
                          cluster_tags, cluster_score, COUNT(*) as member_count
                   FROM wallet_clusters
                   GROUP BY cluster_id
                   ORDER BY cluster_score DESC, member_count DESC
                   LIMIT ?""",
                (limit,),
            )).fetchall()
        except aiosqlite.Error:
            rows = []

    import json
    clusters = []
    for r in rows:
        d = dict(r)
        d["cluster_tags"] = json.loads(d.get("cluster_tags") or "[]")
        clusters.append(d)

    return {"clusters": clusters, "total": len(clusters)}


@router.get("/clusters/{cluster_id}")
async def get_cluster(
    cluster_id: str,
    _caller: dict = Depends(get_agent),
):
    """Get all wallets in a specific cluster."""
    members = await wallet_cluster_engine.get_cluster_members(cluster_id)
    if not members:
        raise HTTPException(404, f"Cluster {cluster_id} not found")
    return {"cluster_id": cluster_id, "members": members, "count": len(members)}


@router.get("/wallet-cluster/{wallet}")
async def get_wallet_cluster(
    wallet: str,
    _caller: dict = Depends(get_agent),
):
    """Get cluster info for a specific wallet address."""
    info = await wallet_cluster_engine.get_wallet_cluster(wallet)
    if not info:
        return {"wallet_address": wallet, "cluster_id": None, "cluster_tags": [], "wallet_category": "unknown"}
    return info
