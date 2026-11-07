"""Crash-recovery reconciliation for the ARP receipt outbox.

Background task started by lifespan().  Every SCAN_INTERVAL_SECONDS it:
  1. Queries arp_receipts WHERE outcome='pending' AND accepted older than
     STALENESS_THRESHOLD_SECONDS (default 300 s = 5 min).
  2. For each stale row, looks for a matching order in trading_orders to
     determine whether a tx_hash was ever written there.
  3. If a tx_hash exists on the order → re-verify on-chain via
     verify_sui_settlement_effects and promote to 'success' or 'failed'.
  4. If no tx_hash → the order is still open or the submission was lost;
     marks the receipt 'failed' so it leaves the pending scan.
  5. Emits a ReconciliationAlert event for each row it touches so the
     event bus can page oncall or update dashboards.

The daemon is intentionally conservative:
  - It never re-submits transactions.
  - It only demotes 'pending' rows, never modifies 'success'/'failed' ones.
  - Every outcome change is logged at WARNING level so it's visible in prod.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .db import DB_PATH, get_db
from .event_bus import VantageEvent, emit
from .sui_client import verify_sui_settlement_effects

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60          # how often the loop runs
STALENESS_THRESHOLD_SECONDS = 300   # pending rows older than this are stale
MAX_ROWS_PER_SCAN = 50              # cap per cycle to bound RPC call volume


async def _get_order_tx_hash(action_kind: Optional[str], target: Optional[str],
                              agent_id: str, timestamp: Optional[int]) -> Optional[str]:
    """Look up a tx_hash from trading_orders for the receipt's order."""
    if not timestamp:
        return None
    try:
        async with get_db() as db:
            # Match on agent + symbol + side + timestamp window (±60 s)
            row = await (await db.execute(
                """SELECT tx_hash FROM trading_orders
                   WHERE agent_id = ?
                     AND tx_hash IS NOT NULL
                     AND ABS(CAST(strftime('%s', created_at) AS INTEGER) - ?) < 60
                   ORDER BY id DESC LIMIT 1""",
                (agent_id, timestamp),
            )).fetchone()
        return row[0] if row else None
    except Exception as exc:
        logger.debug("reconciler: order lookup failed: %s", exc)
        return None


async def _resolve_pending_receipt(row: aiosqlite.Row) -> str:
    """Determine and apply the correct final outcome for a stale pending row.

    Returns the outcome that was written ('success' or 'failed').
    """
    receipt_id: str = row["receipt_id"]
    agent_id: str = row["agent_id"]
    action_kind: Optional[str] = row["action_kind"]
    target: Optional[str] = row["target"]
    timestamp: Optional[int] = row["timestamp"]

    tx_hash = await _get_order_tx_hash(action_kind, target, agent_id, timestamp)

    final_outcome = "failed"
    verification_reason = "no tx_hash found on matching order"

    if tx_hash:
        try:
            ok, reason = await verify_sui_settlement_effects(tx_hash)
            if ok:
                final_outcome = "success"
                verification_reason = f"chain verified: {reason}"
            else:
                verification_reason = f"chain rejected: {reason}"
        except Exception as exc:
            verification_reason = f"verification error: {exc}"

    # Write the resolved outcome
    try:
        async with get_db() as db:
            await db.execute(
                "UPDATE arp_receipts SET outcome=? WHERE receipt_id=? AND outcome='pending'",
                (final_outcome, receipt_id),
            )
            await db.commit()
    except Exception as exc:
        logger.error("reconciler: failed to write outcome for %s: %s", receipt_id, exc)
        return "failed"

    logger.warning(
        "reconciler: stale pending receipt resolved receipt_id=%s outcome=%s reason=%s",
        receipt_id, final_outcome, verification_reason,
    )

    try:
        await emit(VantageEvent(
            event_type="ReconciliationAlert",
            aggregate_id=receipt_id,
            aggregate_type="receipt",
            actor_id=int(agent_id) if agent_id and agent_id.isdigit() else 0,
            payload={
                "receipt_id": receipt_id,
                "resolved_outcome": final_outcome,
                "reason": verification_reason,
                "reconciled_at": datetime.now(timezone.utc).isoformat(),
            },
        ))
    except Exception as exc:
        logger.debug("reconciler: event emit failed: %s", exc)

    return final_outcome


async def _scan_once() -> int:
    """Single reconciliation pass. Returns number of rows resolved."""
    cutoff_iso = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - STALENESS_THRESHOLD_SECONDS,
        tz=timezone.utc,
    ).isoformat()

    try:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                """SELECT receipt_id, agent_id, action_kind, target, timestamp
                   FROM arp_receipts
                   WHERE outcome = 'pending'
                     AND accepted_at < ?
                   ORDER BY accepted_at ASC
                   LIMIT ?""",
                (cutoff_iso, MAX_ROWS_PER_SCAN),
            )).fetchall()
    except Exception as exc:
        logger.warning("reconciler: scan query failed: %s", exc)
        return 0

    if not rows:
        return 0

    logger.info("reconciler: found %d stale pending receipt(s)", len(rows))
    resolved = 0
    for row in rows:
        try:
            await _resolve_pending_receipt(row)
            resolved += 1
        except Exception as exc:
            logger.error("reconciler: unhandled error on %s: %s", row["receipt_id"], exc)

    return resolved


async def reconciliation_loop() -> None:
    """Long-running coroutine; run as an asyncio task from lifespan()."""
    logger.info(
        "receipt reconciler started: scan_interval=%ds staleness=%ds",
        SCAN_INTERVAL_SECONDS, STALENESS_THRESHOLD_SECONDS,
    )
    while True:
        try:
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            resolved = await _scan_once()
            if resolved:
                logger.warning("reconciler: resolved %d stale receipt(s) this cycle", resolved)
        except asyncio.CancelledError:
            logger.info("receipt reconciler stopped")
            return
        except Exception as exc:
            # Never let an unhandled exception kill the loop
            logger.error("reconciler: unexpected error in loop: %s", exc)
