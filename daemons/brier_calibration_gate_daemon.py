#!/opt/ares/venv/bin/python3
"""Brier calibration gate daemon — periodic tick wrapper."""
import asyncio, time, logging, os, sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brier-gate")

POLL_INTERVAL = int(os.environ.get("BRIER_POLL_INTERVAL", "600"))

async def main():
    logger.info("Brier calibration gate daemon started (poll_interval=%ds)", POLL_INTERVAL)
    try:
        sys.path.insert(0, "/opt/ares/Vantage")
        from backend import brier_calibration_gate as gate
        logger.info("brier_calibration_gate module loaded")
    except Exception as exc:
        logger.warning("Could not import brier_calibration_gate: %s -- running in stub mode", exc)
        gate = None

    while True:
        try:
            if gate is not None:
                from backend.db import get_db
                async with get_db() as _db:
                    _c = await (await _db.execute(
                        "SELECT COUNT(*) FROM brier_predictions WHERE scored=0")).fetchone()
                if not _c or not _c[0]:
                    logger.warning("Brier tick: no predictions recorded yet - gate is vacuous, skipping")
                    await asyncio.sleep(TICK)
                    continue
                score = await gate.compute_rolling_brier()
                status = await gate.gate_status()
                logger.info("Brier tick -- score=%s gate=%s", score, status)
            else:
                logger.debug("Brier gate tick (stub mode)")
        except Exception as exc:
            logger.warning("Brier tick error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
