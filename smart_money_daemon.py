#!/usr/bin/env python3
"""Smart money score refresh daemon -- runs every 2 hours."""
import asyncio
import logging
import sys
import os

sys.path.insert(0, "/opt/ares/Vantage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smart_money_daemon")

INTERVAL = int(os.environ.get("SMART_MONEY_INTERVAL", "7200"))  # 2 hours


async def main():
    from backend.db import get_db
    from backend.smart_money import refresh_smart_money_scores
    logger.info("smart_money_daemon started")
    while True:
        try:
            async with get_db() as db:
                count = await refresh_smart_money_scores(db)
            if not count:
                # Zero clusters means the upstream rebuild produced nothing.
                # Logging INFO made a dead pipeline look healthy, so escalate
                # and eventually fail the unit where systemd can see it.
                globals()["_zero_streak"] = globals().get("_zero_streak", 0) + 1
                logger.error(
                    "smart money scored 0 clusters (%d consecutive) - "
                    "wallet_clusters is empty; check ares-auto-refresh cluster rebuild",
                    globals()["_zero_streak"])
                if globals()["_zero_streak"] >= 3:
                    raise SystemExit(1)
            else:
                globals()["_zero_streak"] = 0
                logger.info(f"smart_money refresh complete: {count} clusters scored")
        except Exception as e:
            logger.error(f"smart_money refresh error: {e}")
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
