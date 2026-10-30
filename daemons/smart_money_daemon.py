#!/usr/bin/env python3
import asyncio, time, sys, os
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

from backend.db import get_db
from backend.smart_money import compute_smart_money_score, upsert_score

async def refresh_cycle():
    async with get_db() as db:
        try:
            rows = await (await db.execute("SELECT DISTINCT wallet FROM alpha_early_buyers LIMIT 500")).fetchall()
        except Exception:
            rows = []
        wallets = [r["wallet"] for r in rows] if rows else []
    for wallet in wallets:
        score_data = await compute_smart_money_score(wallet, [])
        await upsert_score(wallet, score_data)
    print(f"[smart_money] Refreshed {len(wallets)} wallets", flush=True)

async def main():
    print("[smart_money] Starting daemon", flush=True)
    while True:
        try:
            await refresh_cycle()
        except Exception as e:
            print(f"[smart_money] Error: {e}", flush=True)
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
