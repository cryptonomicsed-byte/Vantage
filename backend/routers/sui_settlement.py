"""Sui on-chain settlement for guild execution receipts.

Flow:
  1. POST /api/receipts/{receipt_id}/settle/sui  → returns unsigned PTB intent
  2. Frontend wallet signs + submits → calls confirm endpoint
  3. POST /api/receipts/{receipt_id}/settle/sui/confirm {tx_digest} → stores digest
  4. GET  /api/receipts/{receipt_id}/settlement  → public settlement status
"""
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.db import get_db
from backend.deps import get_agent
from backend.event_bus import VantageEvent, emit
from backend.sui_client import build_settlement_ptb, verify_sui_tx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/receipts", tags=["sui-settlement"])


class ConfirmBody(BaseModel):
    tx_digest: str


@router.post("/{receipt_id}/settle/sui")
async def settle_receipt_sui(receipt_id: str, agent: dict = Depends(get_agent)):
    """Initiate Sui settlement for a guild execution receipt.

    Returns an unsigned PTB intent that the caller's Sui wallet should sign
    and submit on-chain, then confirm via the /confirm endpoint.
    """
    agent_id: int = agent["id"]
    sui_address: Optional[str] = agent.get("sui_address") or ""

    if not sui_address:
        raise HTTPException(
            status_code=400,
            detail=(
                "Agent has no verified Sui address. "
                "Bind one via POST /api/agents/me/sui-address first."
            ),
        )

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM guild_execution_receipts WHERE id = ?",
            (receipt_id,),
        )).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Derive receipt hash from the receipt body if a dedicated column doesn't exist
    import hashlib, json as _json
    receipt_body = row["receipt_body"] or ""
    receipt_hash = hashlib.sha256(receipt_body.encode()).hexdigest()

    unsigned_ptb = await build_settlement_ptb(
        sender_address=sui_address,
        receipt_hash=receipt_hash,
        receipt_id=receipt_id,
        agent_id=agent_id,
    )

    return {
        "status": "pending_signature",
        "unsigned_ptb": unsigned_ptb,
        "receipt_hash": receipt_hash,
        "sui_address": sui_address,
    }


@router.post("/{receipt_id}/settle/sui/confirm")
async def confirm_receipt_settlement(
    receipt_id: str,
    body: ConfirmBody,
    agent: dict = Depends(get_agent),
):
    """Confirm a Sui settlement by providing the submitted transaction digest.

    Verifies the TX on-chain, stores the digest, and emits SettlementCompleted.
    """
    agent_id: int = agent["id"]
    tx_digest = body.tx_digest.strip()

    if not tx_digest:
        raise HTTPException(status_code=400, detail="tx_digest is required")

    # Verify TX is confirmed on-chain
    confirmed = await verify_sui_tx(tx_digest)
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail=(
                "Transaction not confirmed on Sui. "
                "Ensure the TX has been submitted and wait for finality."
            ),
        )

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, agent_id FROM guild_execution_receipts WHERE id = ?",
            (receipt_id,),
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Receipt not found")

        # Store settlement info (columns added by tasks_db.py ALTER TABLE)
        try:
            await db.execute(
                """UPDATE guild_execution_receipts
                   SET sui_tx_digest = ?, settled_at = datetime('now')
                   WHERE id = ?""",
                (tx_digest, receipt_id),
            )
            await db.commit()
        except Exception as exc:
            logger.error("confirm_receipt_settlement: DB update failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to store settlement")

    # Emit event
    try:
        await emit(VantageEvent(
            event_type="SettlementCompleted",
            aggregate_id=receipt_id,
            aggregate_type="receipt",
            actor_id=agent_id,
            payload={
                "receipt_id": receipt_id,
                "sui_tx_digest": tx_digest,
                "agent_id": agent_id,
            },
        ))
    except Exception as exc:
        logger.warning("confirm_receipt_settlement: emit failed: %s", exc)

    return {
        "settled": True,
        "tx_digest": tx_digest,
        "receipt_id": receipt_id,
    }


@router.get("/{receipt_id}/settlement")
async def get_receipt_settlement(receipt_id: str):
    """Return public settlement status for a receipt."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # Build query defensively — columns may not exist if migration hasn't run
        try:
            row = await (await db.execute(
                """SELECT id,
                          receipt_body,
                          COALESCE(sui_tx_digest, NULL) AS sui_tx_digest,
                          COALESCE(settled_at, NULL) AS settled_at
                   FROM guild_execution_receipts
                   WHERE id = ?""",
                (receipt_id,),
            )).fetchone()
        except Exception:
            # Fallback: columns not yet migrated
            row = await (await db.execute(
                "SELECT id, receipt_body FROM guild_execution_receipts WHERE id = ?",
                (receipt_id,),
            )).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    import hashlib
    receipt_body = row["receipt_body"] or ""
    receipt_hash = hashlib.sha256(receipt_body.encode()).hexdigest()

    sui_tx_digest = row["sui_tx_digest"] if "sui_tx_digest" in row.keys() else None
    settled_at = row["settled_at"] if "settled_at" in row.keys() else None

    return {
        "settled": bool(sui_tx_digest),
        "sui_tx_digest": sui_tx_digest,
        "settled_at": settled_at,
        "receipt_hash": receipt_hash,
    }
