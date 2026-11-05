#!/usr/bin/env python3
"""Context signal daemon — polls the keyless evidence sources on their cadence.

FRED changes monthly, the weather forecast hourly, a play-money crowd in
minutes, so one interval for all three is either wasteful (hammering FRED) or
stale (a 6h-old crowd read). Each source carries its own cadence from
`signal_sources.CADENCE`; the tick only decides how often we check what is due.

Fail-open per source: one venue being down must not stop the other two, and an
empty collect() is not an error (a quiet market is a real state).
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, "/opt/ares/Vantage")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("context-signals")

TICK = int(os.environ.get("CONTEXT_TICK_SECONDS", "300"))


async def main() -> None:
    from backend.signal_sources import CADENCE, SOURCES
    from backend.signal_sources import store

    logger.info("context daemon started tick=%ss sources=%s", TICK, ",".join(SOURCES))
    last: dict[str, float] = {}

    while True:
        now = time.time()
        due = [name for name in SOURCES if now - last.get(name, 0.0) >= CADENCE[name]]
        for name in due:
            mod = SOURCES[name]
            try:
                items = await mod.collect()
                written = await store.upsert(items)
                logger.info("%s: %d signals (upserted=%d)", name, len(items), written)
            except Exception as exc:  # fail-open, per source
                logger.warning("%s collect failed: %s", name, exc)
            last[name] = time.time()
            # Pace the batch: GDELT-style limits are the norm, not the exception.
            await asyncio.sleep(1)
        await asyncio.sleep(TICK)


if __name__ == "__main__":
    asyncio.run(main())
