"""
Sniper pipeline: handoff from early_buyers detection to sniper targeting.
Takes early buyer wallet clusters and scores them for sniper targeting.
"""
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def build_sniper_targets(early_buyers, db):
    targets = []
    for buyer in early_buyers:
        try:
            wallet = buyer.get("wallet", "")
            mint = buyer.get("mint", "")
            if not wallet or not mint:
                continue

            score = 0.0
            reasons = []

            entry_pct = buyer.get("entry_percentile", 100)
            if entry_pct < 5:
                score += 0.4
                reasons.append("top-5pct-entry")
            elif entry_pct < 15:
                score += 0.2
                reasons.append("early-entry")

            try:
                prev_count = db.execute(
                    "SELECT COUNT(*) FROM sniper_targets WHERE wallet=? AND mint != ?",
                    (wallet, mint)
                ).fetchone()[0]
                if prev_count >= 2:
                    score += 0.3
                    reasons.append(f"repeat-sniper({prev_count})")
            except Exception:
                pass

            hold_minutes = buyer.get("hold_minutes", 9999)
            if hold_minutes < 60:
                score += 0.2
                reasons.append("fast-exit")

            targets.append({
                "wallet": wallet,
                "mint": mint,
                "sniper_score": round(min(score, 1.0), 4),
                "reason": ",".join(reasons),
            })
        except Exception as e:
            logger.warning(f"sniper_pipeline error on buyer {buyer}: {e}")

    targets.sort(key=lambda x: x["sniper_score"], reverse=True)
    return targets


def upsert_sniper_targets(targets, db):
    import time
    now_ts = int(time.time())
    count = 0
    for t in targets:
        try:
            db.execute(
                "INSERT INTO sniper_targets (wallet, mint, sniper_score, reason, updated_ts) VALUES (?,?,?,?,?) "
                "ON CONFLICT(wallet, mint) DO UPDATE SET sniper_score=excluded.sniper_score, reason=excluded.reason, updated_ts=excluded.updated_ts",
                (t["wallet"], t["mint"], t["sniper_score"], t["reason"], now_ts)
            )
            count += 1
        except Exception as e:
            logger.warning(f"upsert_sniper_target failed: {e}")
    db.commit()
    return count
