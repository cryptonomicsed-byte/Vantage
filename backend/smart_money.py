import logging
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def compute_smart_money_score(wallet_cluster: dict) -> float:
    """
    Score a wallet cluster for smart money characteristics.
    Returns 0.0-1.0. Higher = more likely smart money.
    """
    score = 0.0
    wallets = wallet_cluster.get("wallets", [])
    if not wallets:
        return 0.0

    n = len(wallets)

    # Factor 1: cluster concentration (fewer wallets = more coordinated)
    if n <= 3:
        score += 0.3
    elif n <= 10:
        score += 0.15

    # Factor 2: early entry (bought before 10% of cumulative volume)
    early_count = sum(1 for w in wallets if w.get("entry_percentile", 100) < 10)
    if early_count > 0:
        score += min(0.3, early_count / max(n, 1) * 0.3)

    # Factor 3: profitable exits
    profitable = sum(1 for w in wallets if w.get("realized_pnl", 0) > 0)
    score += min(0.2, profitable / max(n, 1) * 0.2)

    # Factor 4: repeat coordination (cluster seen across multiple tokens)
    seen_tokens = wallet_cluster.get("tokens_seen", 1)
    if seen_tokens >= 3:
        score += 0.2

    return round(min(score, 1.0), 4)


async def refresh_smart_money_scores(db) -> int:
    """
    Recompute scores for all wallet clusters.
    Groups wallet_clusters rows by cluster_id, scores each cluster,
    and upserts into smart_money_scores.
    Returns count of clusters updated.
    """
    import uuid

    updated = 0
    now_ts = int(datetime.now(timezone.utc).timestamp())

    try:
        rows = await (await db.execute(
            "SELECT cluster_id, wallet_address, cluster_tags, cluster_score "
            "FROM wallet_clusters ORDER BY cluster_id"
        )).fetchall()
    except Exception as exc:
        logger.warning(f"smart_money: could not read wallet_clusters: {exc}")
        return 0

    # Group into cluster_id -> list of wallet dicts
    clusters: dict = {}
    for row in rows:
        cid = row[0]
        if cid not in clusters:
            clusters[cid] = {"wallets": [], "tokens_seen": 1, "raw_score": row[3]}
        try:
            tags = json.loads(row[2]) if row[2] else []
        except Exception:
            tags = []
        clusters[cid]["wallets"].append({
            "address": row[1],
            "tags": tags,
        })

    # Count distinct tokens per cluster from wallet_trades
    try:
        token_counts = await (await db.execute(
            "SELECT wc.cluster_id, COUNT(DISTINCT wt.token_mint) AS cnt "
            "FROM wallet_clusters wc "
            "JOIN wallet_trades wt ON wt.wallet = wc.wallet_address "
            "GROUP BY wc.cluster_id"
        )).fetchall()
        for cid, cnt in token_counts:
            if cid in clusters:
                clusters[cid]["tokens_seen"] = cnt
    except Exception:
        pass  # wallet_trades may be empty; default tokens_seen=1 is fine

    for cid, cluster_data in clusters.items():
        try:
            score = compute_smart_money_score(cluster_data)
            await db.execute(
                "UPDATE smart_money_scores SET score=?, computed_ts=? WHERE cluster_id=?",
                (score, now_ts, cid)
            )
            # INSERT if no row existed (UPDATE touches 0 rows)
            await db.execute(
                "INSERT OR IGNORE INTO smart_money_scores "
                "(id, wallet, score, win_rate, avg_roi, trade_count, cluster_id, tags, computed_ts) "
                "VALUES (?, ?, ?, 0.0, 0.0, ?, ?, '[]', ?)",
                (str(uuid.uuid4()), cid, score, len(cluster_data["wallets"]), cid, now_ts)
            )
            updated += 1
        except Exception as exc:
            logger.warning(f"smart_money: score failed for cluster {cid}: {exc}")

    try:
        await db.commit()
    except Exception as exc:
        logger.warning(f"smart_money: commit failed: {exc}")

    return updated
