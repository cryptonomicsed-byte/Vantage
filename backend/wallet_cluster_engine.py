"""
Wallet clustering and categorization layer.

Groups wallets by three signals:
  1. Shared funding source — wallets funded by the same address are likely
     related actors, a bot farm, or a single operator with multiple wallets.
  2. Co-trading temporal window — wallets that bought the same token within
     COTRADE_WINDOW_SEC of each other show coordinated behavior.
  3. Behavioral fingerprint — trade frequency, wallet age, token diversity,
     degen_score from tracked_wallets classify each wallet independently.

Results are stored in wallet_clusters (one row per wallet-in-cluster) and
exposed on moneyflow nodes as cluster_id + cluster_tags.

Run via the /api/alpha/clusters/rebuild endpoint or call rebuild_clusters()
from a scheduled task.
"""
import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Optional

import aiosqlite

from backend.db import get_db

logger = logging.getLogger(__name__)

COTRADE_WINDOW_SEC = 300    # 5-minute co-trade window
MIN_CLUSTER_SIZE = 2        # ignore singleton "clusters"

_CLUSTERS_DDL = """
CREATE TABLE IF NOT EXISTS wallet_clusters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      TEXT NOT NULL,
    wallet_address  TEXT NOT NULL,
    cluster_tags    TEXT DEFAULT '[]',
    cluster_label   TEXT,
    wallet_category TEXT DEFAULT 'unknown',
    cluster_score   REAL DEFAULT 0.0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(cluster_id, wallet_address)
)
"""
_CLUSTERS_IDX_W = "CREATE INDEX IF NOT EXISTS idx_wc_wallet ON wallet_clusters(wallet_address)"
_CLUSTERS_IDX_C = "CREATE INDEX IF NOT EXISTS idx_wc_cluster ON wallet_clusters(cluster_id)"


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    await db.execute(_CLUSTERS_DDL)
    await db.execute(_CLUSTERS_IDX_W)
    await db.execute(_CLUSTERS_IDX_C)
    await db.commit()


def _cluster_id(members: list[str]) -> str:
    """Deterministic cluster ID from sorted member set."""
    key = ",".join(sorted(members))
    return "c_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def _categorize(wallet: dict) -> str:
    """Classify a wallet into a human-readable category."""
    score = float(wallet.get("degen_score") or 0)
    trades = int(wallet.get("trade_count") or 0)
    tokens = int(wallet.get("unique_tokens") or 0)
    balance = float(wallet.get("balance_usd") or 0)

    if balance < 1.0 and trades == 0:
        return "dust"
    if trades == 0:
        return "dormant"
    if score >= 0.8 and trades >= 50:
        return "high_conviction"
    if trades >= 20 and tokens >= 5:
        return "active"
    if trades < 5:
        return "low_traction"
    return "active"


async def rebuild_clusters(max_wallets: int = 5000) -> dict:
    """
    Rebuild wallet_clusters from wallet_edges (funding), wallet_trades
    (co-trading), and tracked_wallets (behavioral).

    Returns summary: {clusters_found, wallets_clustered, elapsed_sec}
    """
    t0 = time.time()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _ensure_tables(db)

        # ── Load wallet metadata ──────────────────────────────────────────
        try:
            tw_rows = {
                r["address"]: dict(r)
                for r in await (await db.execute(
                    "SELECT address, degen_score, trade_count, unique_tokens, "
                    "balance_usd, address_type FROM tracked_wallets "
                    "WHERE archived_at IS NULL LIMIT ?",
                    (max_wallets,),
                )).fetchall()
            }
        except aiosqlite.Error:
            tw_rows = {}

        # ── Signal 1: Shared funding source (wallet_edges role='funded') ─
        # funder_address → [funded_wallets]
        funded_by: dict[str, list[str]] = defaultdict(list)
        try:
            edge_rows = await (await db.execute(
                "SELECT address_a, address_b FROM wallet_edges "
                "WHERE role='funded' ORDER BY address_a"
            )).fetchall()
            for r in edge_rows:
                funder, funded = r[0], r[1]
                funded_by[funder].append(funded)
        except aiosqlite.Error:
            pass

        # ── Signal 2: Co-trading temporal window (wallet_trades) ─────────
        # token_mint → sorted list of (timestamp, wallet)
        cotrade_map: dict[str, list[tuple[int, str]]] = defaultdict(list)
        try:
            trade_rows = await (await db.execute(
                "SELECT wallet, token_mint, timestamp FROM wallet_trades "
                "WHERE type='buy' ORDER BY token_mint, timestamp"
            )).fetchall()
            for r in trade_rows:
                w, mint, ts = r[0], r[1], int(r[2] or 0)
                if mint and ts:
                    cotrade_map[mint].append((ts, w))
        except aiosqlite.Error:
            pass

        # Token early buyers also contribute co-trade signal
        try:
            eb_rows = await (await db.execute(
                "SELECT wallet_address, mint, entry_ts FROM token_early_buyers "
                "WHERE entry_ts IS NOT NULL"
            )).fetchall()
            for r in eb_rows:
                w, mint, ts = r[0], r[1], int(r[2] or 0)
                if mint and ts:
                    cotrade_map[mint].append((ts, w))
        except aiosqlite.Error:
            pass

    # ── Build clusters from signals ──────────────────────────────────────
    # Union-Find for merging clusters
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), x)
            x = parent.get(x, x)
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Shared funder: all wallets funded by same address → same cluster
    for funder, funded_wallets in funded_by.items():
        if len(funded_wallets) < MIN_CLUSTER_SIZE:
            continue
        for w in funded_wallets[1:]:
            union(funded_wallets[0], w)

    # Co-trading: wallets buying same token within window → same cluster
    for mint, entries in cotrade_map.items():
        entries.sort(key=lambda e: e[0])
        for i in range(len(entries)):
            ts_i, w_i = entries[i]
            for j in range(i + 1, len(entries)):
                ts_j, w_j = entries[j]
                if ts_j - ts_i > COTRADE_WINDOW_SEC:
                    break
                if w_i != w_j:
                    union(w_i, w_j)

    # Group wallets into clusters
    clusters: dict[str, list[str]] = defaultdict(list)
    all_wallets = set(tw_rows.keys())
    # Also include wallets from edges/trades even if not in tracked_wallets
    for funder, ws in funded_by.items():
        all_wallets.update(ws)
    for entries in cotrade_map.values():
        all_wallets.update(w for _, w in entries)

    for w in all_wallets:
        root = find(w)
        clusters[root].append(w)

    # Discard singletons
    clusters = {root: members for root, members in clusters.items() if len(members) >= MIN_CLUSTER_SIZE}

    # ── Compute tags per cluster ──────────────────────────────────────────
    def _cluster_tags(members: list[str]) -> list[str]:
        tags = []
        has_early_buyer = False
        high_conviction_count = 0
        for w in members:
            meta = tw_rows.get(w, {})
            cat = _categorize(meta)
            if cat == "high_conviction":
                high_conviction_count += 1
        if high_conviction_count >= 2:
            tags.append("smart_money")
        # Check if any member is a first_buyer via token_wallet_roles (sync check below)
        return tags

    # ── Persist clusters ──────────────────────────────────────────────────
    cluster_count = 0
    wallet_clustered = 0

    async with get_db() as db:
        await _ensure_tables(db)

        # Load early_buyer wallet set for tagging
        try:
            eb_wallets = {
                r[0] for r in await (await db.execute(
                    "SELECT DISTINCT wallet_address FROM token_early_buyers WHERE is_major=1"
                )).fetchall()
            }
        except aiosqlite.Error:
            eb_wallets = set()

        # Clear stale data
        await db.execute("DELETE FROM wallet_clusters")

        for root, members in clusters.items():
            cid = _cluster_id(members)
            cluster_count += 1

            # Compute aggregate score (mean degen_score of known members)
            scores = [float(tw_rows.get(w, {}).get("degen_score") or 0) for w in members]
            cluster_score = round(sum(scores) / len(scores), 4) if scores else 0.0

            # Cluster-level tags
            tags: list[str] = []
            any_high_conviction = any(
                _categorize(tw_rows.get(w, {})) == "high_conviction" for w in members
            )
            if any_high_conviction:
                tags.append("smart_money")
            has_eb = any(w in eb_wallets for w in members)
            if has_eb:
                tags.append("early_buyer")
            # Shared funding = likely coordinated
            for funder, funded in funded_by.items():
                if len(set(funded) & set(members)) >= 2:
                    tags.append("shared_funder")
                    break

            label_parts = []
            if "smart_money" in tags:
                label_parts.append("Smart Money")
            if "early_buyer" in tags:
                label_parts.append("Early Buyers")
            if "shared_funder" in tags:
                label_parts.append("Coordinated")
            cluster_label = " + ".join(label_parts) if label_parts else f"Cluster ({len(members)})"

            for w in members:
                meta = tw_rows.get(w, {})
                category = _categorize(meta)
                if w in eb_wallets:
                    category = "early_buyer"
                wallet_tags = list(tags)
                if w in eb_wallets:
                    wallet_tags.append("first_buyer")

                try:
                    await db.execute(
                        """INSERT INTO wallet_clusters
                           (cluster_id, wallet_address, cluster_tags, cluster_label,
                            wallet_category, cluster_score, updated_at)
                           VALUES (?,?,?,?,?,?,datetime('now'))
                           ON CONFLICT(cluster_id, wallet_address) DO UPDATE SET
                             cluster_tags=excluded.cluster_tags,
                             cluster_label=excluded.cluster_label,
                             wallet_category=excluded.wallet_category,
                             cluster_score=excluded.cluster_score,
                             updated_at=datetime('now')""",
                        (cid, w, json.dumps(wallet_tags), cluster_label, category, cluster_score),
                    )
                    wallet_clustered += 1
                except aiosqlite.Error:
                    pass

        await db.commit()

    elapsed = round(time.time() - t0, 2)
    logger.info("Cluster rebuild: %d clusters, %d wallets, %.1fs", cluster_count, wallet_clustered, elapsed)
    return {
        "clusters_found": cluster_count,
        "wallets_clustered": wallet_clustered,
        "elapsed_sec": elapsed,
    }


async def get_wallet_cluster(wallet_address: str) -> Optional[dict]:
    """Get cluster info for a specific wallet."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            row = await (await db.execute(
                "SELECT * FROM wallet_clusters WHERE wallet_address=? LIMIT 1",
                (wallet_address,),
            )).fetchone()
            if row:
                r = dict(row)
                r["cluster_tags"] = json.loads(r.get("cluster_tags") or "[]")
                return r
        except aiosqlite.Error:
            pass
    return None


async def get_cluster_members(cluster_id: str) -> list[dict]:
    """Get all wallets in a cluster."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                "SELECT * FROM wallet_clusters WHERE cluster_id=? ORDER BY cluster_score DESC",
                (cluster_id,),
            )).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["cluster_tags"] = json.loads(d.get("cluster_tags") or "[]")
                result.append(d)
            return result
        except aiosqlite.Error:
            return []


async def load_cluster_map() -> dict[str, dict]:
    """Load all wallet→cluster mappings into memory for moneyflow decoration."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                "SELECT wallet_address, cluster_id, cluster_tags, cluster_label, "
                "wallet_category, cluster_score FROM wallet_clusters"
            )).fetchall()
            result = {}
            for r in rows:
                d = dict(r)
                d["cluster_tags"] = json.loads(d.get("cluster_tags") or "[]")
                result[r["wallet_address"]] = d
            return result
        except aiosqlite.Error:
            return {}
