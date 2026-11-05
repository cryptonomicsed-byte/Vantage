#!/usr/bin/env python3
"""Prediction market signal daemon: fetch signals -> Jev decision -> Brier record.

Deliberately ends at the record. A prediction decision is a claim about a future
event, not an order, and there is no code path from this daemon to the trading
engine (tests/test_no_prediction_orders.py enforces it).
"""
import asyncio, time, sys, os
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/data/vantage.db")

POLL_INTERVAL = 300

async def process_unprocessed_signals():
    from backend.db import get_db
    from backend.jev_decision import jev_decide
    from backend.brier_calibration_gate import should_gate, record_prediction

    gated, brier_score = await should_gate()
    if gated:
        print(f"[jev_daemon] Brier gate CLOSED (score={brier_score:.3f}). Skipping.", flush=True)
        return

    async with get_db() as db:
        # Rows come back as tuples (no row_factory), so dict(row) raised
        # "dictionary update sequence element #0 has length 36" on every tick.
        cur = await db.execute(
            "SELECT * FROM prediction_signals WHERE processed=0 ORDER BY ingested_ts DESC LIMIT 20"
        )
        rows = await cur.fetchall()
        _cols = [d[0] for d in (cur.description or [])]

    processed = 0
    for row in rows:
        signal = dict(zip(_cols, row))
        decision = await jev_decide(signal)

        # Record EVERY decision, including skip. Recording only yes/no above
        # threshold is why brier_predictions stayed empty while this daemon
        # reported success every five minutes: the decision path returns
        # "skip" by default, so the filter admitted nothing and the gate was
        # left permanently open. A skip is stored with abstained=1 and is
        # excluded from the score by the gate itself, not by this filter.
        await record_prediction(
            signal["id"],
            decision["verdict"],
            decision["confidence"],
            direction=decision.get("direction"),
            conviction=decision.get("conviction"),
            should_trade=decision.get("should_trade"),
            market_id=signal.get("market_id"),
            reasoning=decision.get("reasoning"),
        )

        # There used to be a POST to /api/trading/signals/ingest here: the
        # order-creating endpoint. It never worked (it sends no
        # X-Vantage-Tool header, and the env carries no tool key, so every call
        # 401'd into a bare except), which is exactly why it was dangerous --
        # dead code aimed at a live order path. That endpoint turns
        # direction in (BUY, LONG, BULLISH) + conviction > 0.7 into a real
        # order, and BULLISH is the direction word the context signals emit, so
        # one "forward contexts to the decision bus" change would have armed it.
        #
        # A prediction decision is a claim, not an order. There is no code path
        # from here to the trading engine; tests/test_no_prediction_orders.py
        # enforces that by grepping this daemon directory.

        async with get_db() as db:
            await db.execute("UPDATE prediction_signals SET processed=1 WHERE id=?", (signal["id"],))
            await db.commit()
        processed += 1

    if processed:
        print(f"[jev_daemon] Processed {processed} signals (Brier={brier_score:.3f})", flush=True)

async def main():
    print("[jev_daemon] Starting prediction market signal daemon", flush=True)
    while True:
        try:
            await process_unprocessed_signals()
        except Exception as e:
            print(f"[jev_daemon] Error: {e}", flush=True)
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
