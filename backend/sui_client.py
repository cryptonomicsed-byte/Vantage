"""Sui RPC client for settlement anchoring.

Constructs unsigned PTBs for receipt settlement.
No private keys stored here — signing is done by the agent's wallet (frontend).
"""
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SUI_RPC_URL: str = os.getenv("SUI_RPC_URL", "https://fullnode.testnet.sui.io")

_HTTP_TIMEOUT = 10.0


async def get_sui_coin_object(sui_address: str) -> Optional[str]:
    """Call suix_getCoins RPC to find a gas coin for the address.

    Returns the first coin object_id, or None if none found or on error.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "suix_getCoins",
        "params": [sui_address, None, None, 1],
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(SUI_RPC_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            coins = data.get("result", {}).get("data", [])
            if coins:
                return coins[0].get("coinObjectId")
    except Exception as exc:
        logger.warning("get_sui_coin_object: RPC error for %s: %s", sui_address, exc)
    return None


async def build_settlement_ptb(
    sender_address: str,
    receipt_hash: str,
    receipt_id: str,
    agent_id: int,
) -> dict:
    """Return an unsigned PTB intent dict for receipt settlement.

    Real PTB construction requires full Sui BCS serialization — we return the
    intent dict here; the frontend wallet SDK handles actual signing.
    """
    return {
        "kind": "ProgrammableTransaction",
        "sender": sender_address,
        "memo": f"vantage-receipt-v1:{receipt_hash}",
        "inputs": [
            {"type": "pure", "value": receipt_hash},
            {"type": "pure", "value": receipt_id},
        ],
        "_note": "Sign this intent with your Sui wallet and POST tx_digest to /confirm",
        "_agent_id": agent_id,
    }


async def verify_sui_tx(
    tx_digest: str,
    expected_memo_prefix: str = "vantage-receipt-v1:",
) -> bool:
    """Thin wrapper kept for callers that only need a boolean.

    Prefer verify_sui_settlement_effects() for settlement confirmation — it
    verifies sender identity and receipt commitment, not just TX finality.
    """
    ok, _ = await verify_sui_settlement_effects(tx_digest)
    return ok


async def verify_sui_settlement_effects(
    tx_digest: str,
    expected_sender: Optional[str] = None,
    expected_receipt_hash: Optional[str] = None,
) -> tuple[bool, str]:
    """Verify a Sui settlement TX against expected effects.

    Returns (ok, reason) where reason describes the first failed check.

    Verifies:
      1. Network reachability and tx finality (effects.status == 'success')
      2. Sender matches the agent's registered Sui address (identity check)
      3. expected_receipt_hash appears as a pure input in the programmable
         transaction (commitment check — proves this TX settles THIS receipt)

    Limitations (documented, not silently ignored):
      - Asset type, transfer amount, and recipient cannot be verified until
        build_settlement_ptb() includes an explicit coin-transfer move call.
        Until then this function attests identity+commitment but not payment.
      - Network cannot be verified from tx data alone; the check is implicitly
        correct when SUI_RPC_URL is set to the expected network's fullnode.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sui_getTransactionBlock",
        "params": [
            tx_digest,
            {"showInput": True, "showEffects": True},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(SUI_RPC_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("verify_sui_settlement_effects: RPC error for %s: %s", tx_digest, exc)
        return False, f"RPC error: {exc}"

    rpc_error = data.get("error")
    if rpc_error:
        msg = (rpc_error.get("message") or str(rpc_error))[:200]
        return False, f"RPC returned error: {msg}"

    result = data.get("result") or {}
    effects = result.get("effects") or {}
    status = (effects.get("status") or {}).get("status", "")
    if status != "success":
        err = (effects.get("status") or {}).get("error", "unknown")
        return False, f"TX not successful: status={status!r} error={err!r}"

    # Sender check — confirm the agent who owns the receipt signed the TX
    if expected_sender:
        tx_data = (result.get("transaction") or {}).get("data") or {}
        actual_sender = tx_data.get("sender", "")
        if actual_sender.lower() != expected_sender.lower():
            return False, (
                f"Sender mismatch: TX was signed by {actual_sender!r}, "
                f"expected {expected_sender!r}"
            )

    # Receipt-hash commitment check — confirm this TX commits to this receipt
    if expected_receipt_hash:
        tx_data = (result.get("transaction") or {}).get("data") or {}
        tx_body = tx_data.get("transaction") or {}
        inputs = tx_body.get("inputs") or []
        # Pure inputs arrive as base64-encoded bytes; check both raw hex and
        # base64 representations since different SDK versions encode differently
        import base64 as _b64
        hash_b64 = _b64.b64encode(expected_receipt_hash.encode()).decode()
        found = any(
            (inp.get("value") == expected_receipt_hash or
             inp.get("value") == hash_b64)
            for inp in inputs
            if inp.get("type") == "pure"
        )
        if not found:
            return False, (
                f"Receipt hash {expected_receipt_hash!r} not found in TX inputs — "
                "this TX does not commit to the expected receipt"
            )

    return True, "ok"
