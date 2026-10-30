import uuid, time, json
from backend.memory_engine import aggregate_score as _julia_score
from backend.db import get_db

async def compute_smart_money_score(wallet: str, trades: list) -> dict:
    if not trades:
        return {"wallet": wallet, "score": 0.0, "win_rate": 0.0, "avg_roi": 0.0, "trade_count": 0}
    wins = [t for t in trades if t.get("roi", 0) > 0]
    win_rate = len(wins) / len(trades)
    avg_roi = sum(t.get("roi", 0) for t in trades) / len(trades)
    volume_score = min(len(trades) / 50, 1.0)
    roi_score = min(avg_roi / 100, 1.0)
    score = await _julia_score(
        signals={"win_rate": win_rate, "roi": roi_score, "volume": volume_score},
        weights={"win_rate": 0.5, "roi": 0.3, "volume": 0.2}
    )
    return {"wallet": wallet, "score": round(score, 4), "win_rate": round(win_rate, 4),
            "avg_roi": round(avg_roi, 4), "trade_count": len(trades)}

async def upsert_score(wallet: str, score_data: dict, cluster_id: str = "", tags: list = None):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO smart_money_scores (id, wallet, score, win_rate, avg_roi, trade_count, cluster_id, tags, computed_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet) DO UPDATE SET
                 score=excluded.score, win_rate=excluded.win_rate, avg_roi=excluded.avg_roi,
                 trade_count=excluded.trade_count, cluster_id=excluded.cluster_id,
                 tags=excluded.tags, computed_ts=excluded.computed_ts""",
            (str(uuid.uuid4()), wallet, score_data["score"], score_data["win_rate"],
             score_data["avg_roi"], score_data["trade_count"], cluster_id,
             json.dumps(tags or []), int(time.time()))
        )
        await db.commit()
