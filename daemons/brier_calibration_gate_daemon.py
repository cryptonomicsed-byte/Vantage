#!/opt/ares/venv/bin/python3
"""Brier calibration gate daemon -- gather settlements, then report calibration.

Two jobs in one tick, in this order:
  1. resolve_pending_outcomes(): ask the venue for settlements on the rows that
     are still pending. Without this, nothing ever writes `outcome`, so no
     prediction is ever scored and the gate can only ever report 0.000.
  2. brier_stats(): log the score together with the counts behind it, so
     "0.000 because nothing is scored" is never printed as "0.000 because we
     are perfectly calibrated".
"""
import asyncio, os, sys, logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brier-gate")

POLL_INTERVAL = int(os.environ.get("BRIER_POLL_INTERVAL", "600"))
RESOLVE_LIMIT = int(os.environ.get("BRIER_RESOLVE_LIMIT", "25"))


async def main():
    logger.info(
        "Brier calibration gate daemon started (poll_interval=%ds, resolve_limit=%d)",
        POLL_INTERVAL, RESOLVE_LIMIT,
    )
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
                resolved = await gate.resolve_pending_outcomes(limit=RESOLVE_LIMIT)
                if any(resolved.values()):
                    logger.info("Brier resolve: %s", resolved)

                stats = await gate.brier_stats()
                if stats["vacuous"]:
                    logger.warning(
                        "Brier tick: gate VACUOUS - no scored directional predictions "
                        "(recorded=%d pending=%d abstentions_pending=%d abstentions_resolved=%d void=%d)",
                        stats["recorded"], stats["pending"],
                        stats["abstentions_pending"], stats["abstentions_resolved"],
                        stats["void"],
                    )
                else:
                    logger.info(
                        "Brier tick -- score=%.3f threshold=%.2f gated=%s scored=%d "
                        "abstentions_resolved=%d void=%d pending=%d",
                        stats["brier_score"], stats["threshold"], stats["gated"],
                        stats["scored"], stats["abstentions_resolved"],
                        stats["void"], stats["pending"],
                    )
            else:
                logger.debug("Brier gate tick (stub mode)")
        except Exception as exc:
            logger.warning("Brier tick error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
