"""Ọmọ Kọ́dà2 runtime-key binding for Vantage agents.

Allows a Vantage agent to bind itself to an external Ọmọ Kọ́dà2 agent identity
by registering the runtime's secp256k1 public key. After binding, the runtime
can authenticate to Vantage using NIP-98 (its Nostr pubkey IS its identity).

The binding is cryptographically verified: the runtime's key must sign
  sha256(str(vantage_agent_id) + ":" + omokoda_agent_id)
using BIP340 schnorr, proving possession of the private key. The binding JSON
is stored in agents.identity (JSONB-style TEXT column in SQLite).

Endpoints:
  GET  /api/agents/me/binding  — current binding status
  POST /api/agents/me/binding  — bind this agent to an Ọmọ Kọ́dà2 runtime
"""
import hashlib
import json
import logging
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from coincurve import PublicKeyXOnly

from ..db import get_db
from ..deps import get_agent
from ..event_bus import VantageEvent, emit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents/me/binding", tags=["identity"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class BindingRequest(BaseModel):
    omokoda_agent_id: str
    runtime_public_key: str   # 64-char hex, x-only secp256k1 pubkey (NIP-01 format)
    binding_signature: str    # 128-char hex, BIP340 schnorr sig


# ── Helpers ────────────────────────────────────────────────────────────────────

def _verify_binding_signature(
    vantage_agent_id: int,
    omokoda_agent_id: str,
    runtime_pubkey_hex: str,
    sig_hex: str,
) -> bool:
    """Verify that the runtime key signed the canonical binding message.

    Message: sha256(str(vantage_agent_id) + ":" + omokoda_agent_id)
    Signature: BIP340 schnorr over those 32 bytes.
    Returns False on any failure (never raises).
    """
    try:
        msg = f"{vantage_agent_id}:{omokoda_agent_id}".encode("utf-8")
        msg_hash = hashlib.sha256(msg).digest()

        pk = PublicKeyXOnly(bytes.fromhex(runtime_pubkey_hex))
        return pk.verify(bytes.fromhex(sig_hex), msg_hash)
    except Exception as exc:
        logger.debug("binding sig verify failed: %s", exc)
        return False


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("")
async def get_binding(agent: dict = Depends(get_agent)):
    """Return the current Ọmọ Kọ́dà2 binding for the authenticated agent.

    Returns the binding object if one exists, or an empty binding with
    `bound: false` if the agent has not yet registered a runtime key.
    """
    identity_raw = agent.get("identity")
    if identity_raw:
        try:
            identity = json.loads(identity_raw) if isinstance(identity_raw, str) else identity_raw
        except (ValueError, TypeError):
            identity = {}
    else:
        identity = {}

    binding = identity.get("binding")
    if binding:
        return {"bound": True, "binding": binding}
    return {"bound": False, "binding": None}


@router.post("")
async def create_binding(
    body: BindingRequest,
    agent: dict = Depends(get_agent),
):
    """Bind this Vantage agent to an Ọmọ Kọ́dà2 runtime key.

    The client must provide:
      - omokoda_agent_id: the Ọmọ Kọ́dà2 agent's identifier string
      - runtime_public_key: 64-hex x-only secp256k1 pubkey (NIP-01 format)
      - binding_signature: BIP340 schnorr signature by the runtime key over
          sha256(str(vantage_agent_id) + ":" + omokoda_agent_id)

    On success, stores the binding in agents.identity and emits an AgentBound
    VantageEvent. The runtime key can then use NIP-98 to authenticate.
    """
    agent_id: int = agent["id"]

    # Validate pubkey format (64 hex chars = 32 bytes x-only)
    runtime_pubkey = body.runtime_public_key.strip().lower()
    if len(runtime_pubkey) != 64:
        raise HTTPException(status_code=422, detail="runtime_public_key must be 64 hex characters (x-only pubkey)")
    try:
        bytes.fromhex(runtime_pubkey)
    except ValueError:
        raise HTTPException(status_code=422, detail="runtime_public_key is not valid hex")

    # Validate signature format (128 hex chars = 64 bytes schnorr sig)
    sig_hex = body.binding_signature.strip().lower()
    if len(sig_hex) != 128:
        raise HTTPException(status_code=422, detail="binding_signature must be 128 hex characters (64-byte schnorr sig)")
    try:
        bytes.fromhex(sig_hex)
    except ValueError:
        raise HTTPException(status_code=422, detail="binding_signature is not valid hex")

    # Cryptographically verify the binding signature
    if not _verify_binding_signature(agent_id, body.omokoda_agent_id, runtime_pubkey, sig_hex):
        raise HTTPException(
            status_code=401,
            detail="Invalid binding_signature — the runtime_public_key did not sign the expected message",
        )

    # Build the binding record
    binding_record = {
        "omokoda_agent_id": body.omokoda_agent_id,
        "runtime_public_key": runtime_pubkey,
        "binding_signature": sig_hex,
        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vantage_agent_id": agent_id,
    }

    # Patch into agents.identity using json_patch (SQLite json_patch merges objects)
    patch = json.dumps({"binding": binding_record})
    async with get_db() as db:
        await db.execute(
            "UPDATE agents SET identity = json_patch(COALESCE(identity, '{}'), ?) WHERE id = ?",
            (patch, agent_id),
        )
        await db.commit()

    # Emit AgentBound VantageEvent for downstream subscribers
    try:
        await emit(VantageEvent(
            event_type="AgentBound",
            actor_id=agent_id,
            actor_name=agent.get("name"),
            aggregate_id=str(agent_id),
            aggregate_type="agent",
            payload={
                "vantage_agent_id": agent_id,
                "omokoda_agent_id": body.omokoda_agent_id,
                "runtime_public_key": runtime_pubkey,
            },
        ))
    except Exception as exc:
        # Non-fatal — binding is already persisted
        logger.warning("AgentBound event emit failed: %s", exc)

    return {
        "bound": True,
        "binding": binding_record,
        "message": "Runtime key binding established successfully",
    }
