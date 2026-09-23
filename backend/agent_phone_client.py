"""
Agent-Phone client — thin async wrapper over the agent-phone signaling layer.

agent-phone uses Nostr NIP-17 gift-wrapped signaling (offer/answer/ICE/hangup)
for call setup and a Reticulum/LXMF fallback for store-and-forward messages.
This client exposes two convenience helpers that the Vantage messaging router
can call without knowing the underlying protocol:

  • initiate_call(caller_id, callee_pubkey)  — build + POST a NIP-17 offer
  • send_message(from_id, to_pubkey, content) — deliver a DM via agent-phone

The base URL is read from ``AGENT_PHONE_URL`` (default http://localhost:8765).
If the service is unreachable the helpers return an error dict rather than
raising so callers can degrade gracefully.
"""

from __future__ import annotations

import logging
import os
import uuid

import httpx

logger = logging.getLogger(__name__)

AGENT_PHONE_URL: str = os.environ.get("AGENT_PHONE_URL", "http://localhost:8765")

# Default httpx timeout — generous because agent-phone may need relay round-trips.
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def initiate_call(caller_id: str, callee_pubkey: str) -> dict:
    """Send a NIP-17 gift-wrapped call offer via agent-phone.

    Args:
        caller_id:     Vantage agent name or npub of the calling agent.
        callee_pubkey: Hex-encoded Nostr pubkey of the target agent.

    Returns:
        Dict with ``call_id`` on success, or ``{"error": ...}`` on failure.
    """
    call_id = uuid.uuid4().hex
    payload = {
        "caller_id": caller_id,
        "callee_pubkey": callee_pubkey,
        "call_id": call_id,
        "signal_type": "offer",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{AGENT_PHONE_URL}/call/initiate",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("agent-phone call initiation failed: %s", exc)
        return {"error": str(exc), "call_id": call_id}
    except httpx.RequestError as exc:
        logger.warning("agent-phone unreachable: %s", exc)
        return {"error": f"agent-phone unreachable: {exc}", "call_id": call_id}


async def send_message(from_id: str, to_pubkey: str, content: str) -> dict:
    """Deliver a message through agent-phone (NIP-44 DM or Reticulum fallback).

    Args:
        from_id:    Vantage agent name or npub of the sending agent.
        to_pubkey:  Hex-encoded Nostr pubkey of the recipient agent.
        content:    Plaintext message body (will be NIP-44 encrypted on the wire).

    Returns:
        Dict with ``message_id`` on success, or ``{"error": ...}`` on failure.
    """
    payload = {
        "from_id": from_id,
        "to_pubkey": to_pubkey,
        "content": content,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{AGENT_PHONE_URL}/message/send",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("agent-phone message delivery failed: %s", exc)
        return {"error": str(exc)}
    except httpx.RequestError as exc:
        logger.warning("agent-phone unreachable: %s", exc)
        return {"error": f"agent-phone unreachable: {exc}"}
