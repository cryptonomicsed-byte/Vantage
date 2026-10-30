import uuid, time, json
from backend.db import get_db

async def get_sniper_targets(limit: int = 50) -> list[dict]:
    """Pull wallets from early_buyers that qualify as sniper targets."""
    async with get_db() as db:
        try:
            rows = await (await db.execute(
                """SELECT wallet, mint, buy_ts, profit_multiplier
                   FROM alpha_early_buyers
                   WHERE buy_ts > ? AND profit_multiplier > 2.0
                   ORDER BY profit_multiplier DESC LIMIT ?""",
                (int(time.time()) - 86400 * 7, limit)
            )).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

async def enqueue_sniper_target(wallet: str, mint: str, context: dict = None):
    """Mark a wallet as a sniper target for copy-trade consideration."""
    async with get_db() as db:
        try:
            await db.execute(
                """INSERT OR IGNORE INTO sniper_targets (id, wallet, mint, context_json, queued_ts, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (str(uuid.uuid4()), wallet, mint, json.dumps(context or {}), int(time.time()))
            )
            await db.commit()
        except Exception:
            pass  # table may not exist yet

async def handoff_early_buyers_to_sniper():
    """Main handoff: pull early buyers, enqueue qualified ones."""
    targets = await get_sniper_targets()
    for t in targets:
        await enqueue_sniper_target(t["wallet"], t["mint"], {"profit_multiplier": t.get("profit_multiplier", 0)})
    return len(targets)
