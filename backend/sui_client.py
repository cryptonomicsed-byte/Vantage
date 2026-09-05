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
    """Verify a Sui transaction exists and was successful.

    Calls sui_getTransactionBlock RPC.  Returns True if the TX is confirmed
    (status == 'success'), False otherwise.  Never raises.
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
            result = data.get("result", {})
            effects = result.get("effects", {})
            status = effects.get("status", {}).get("status", "")
            return status == "success"
    except Exception as exc:
        logger.warning("verify_sui_tx: RPC error for digest %s: %s", tx_digest, exc)
        return False
