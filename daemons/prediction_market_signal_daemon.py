#!/usr/bin/env python3
"""Prediction market signal daemon: fetch signals -> Jev decision -> Brier gate -> emit to signal bus."""
import asyncio, time, sys, os
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

POLL_INTERVAL = 300
JEV_CONFIDENCE_THRESHOLD = float(os.environ.get("JEV_CONFIDENCE_THRESHOLD", "0.65"))

async def process_unprocessed_signals():
    from backend.db import get_db
    from backend.jev_decision import jev_decide
    from backend.brier_calibration_gate import should_gate, record_prediction

    gated, brier_score = await should_gate()
    if gated:
        print(f"[jev_daemon] Brier gate CLOSED (score={brier_score:.3f}). Skipping.", flush=True)
        return

    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT * FROM prediction_signals WHERE processed=0 ORDER BY ingested_ts DESC LIMIT 20"
        )).fetchall()

    processed = 0
    for row in rows:
        signal = dict(row)
        decision = await jev_decide(signal)
        if decision["verdict"] in ("yes", "no") and decision["confidence"] >= JEV_CONFIDENCE_THRESHOLD:
            try:
                import httpx
                tool_key = os.environ.get("VANTAGE_TOOL_KEY", "")
                async with httpx.AsyncClient(timeout=15) as client:
                    await client.post(
                        "http://localhost:8001/api/trading/signals/ingest",
                        headers={"X-Vantage-Tool-Key": tool_key, "Content-Type": "application/json"},
                        json={
                            "source": "jev-prediction",
                            "signal_type": "prediction_market",
                            "market_id": signal["market_id"],
                            "verdict": decision["verdict"],
                            "confidence": decision["confidence"],
                            "reasoning": decision.get("reasoning", ""),
                        }
                    )
            except Exception as e:
                print(f"[jev_daemon] Signal bus error: {e}", flush=True)
            await record_prediction(signal["id"], decision["verdict"], decision["confidence"])
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
