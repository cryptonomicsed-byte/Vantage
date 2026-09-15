"""Agent compute wallet snapshot endpoint — called by walletd on each heartbeat sync.

walletd (Omo-Koda2/omokoda-core/src/services/walletd.rs) PUTs a WalletSnapshot
here every `sync_interval_secs` (default 60 s).  The snapshot is stored in an
in-process dict for quick retrieval; it is intentionally NOT persisted to SQLite
because the Rust wallet is the authoritative source of truth — Vantage only
holds the last-known view.

Endpoints:
  PUT  /api/agents/{agent_id}/wallet   — walletd heartbeat sync
  GET  /api/agents/{agent_id}/wallet   — read last-known snapshot
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/agents", tags=["wallet"])

# In-memory snapshot store: agent_id (str) → snapshot dict.
# One entry per agent; each PUT overwrites the previous snapshot.
_wallets: dict[str, dict] = {}


class WalletSnapshot(BaseModel):
    agent_id: str
    dopamine_balance: int
    dopamine_earned: int
    synapse_balance: int
    synapse_earned: int
    synapse_spent: int
    synapse_staked: int
    last_decay_tick: int
    wallet_version: int
    # Optional ledger summary counts — present in serialised AgentComputeWallet
    dopamine_burned: Optional[int] = None
    dopamine_decayed: Optional[int] = None
    synapse_decayed: Optional[int] = None


@router.put("/{agent_id}/wallet", summary="Sync compute wallet snapshot from walletd")
async def sync_wallet(agent_id: str, snapshot: WalletSnapshot):
    """Receive a wallet heartbeat from the agent's local walletd daemon.

    The `agent_id` path parameter must match `snapshot.agent_id`.
    Returns `{"status": "ok"}` on success.
    """
    if snapshot.agent_id != agent_id:
        raise HTTPException(
            status_code=400,
            detail=f"agent_id mismatch: path={agent_id!r}, body={snapshot.agent_id!r}",
        )
    _wallets[agent_id] = snapshot.dict()
    return {"status": "ok"}


@router.get("/{agent_id}/wallet", summary="Read last compute wallet snapshot")
async def get_wallet(agent_id: str):
    """Return the most recent wallet snapshot pushed by walletd for this agent.

    Raises 404 if walletd has not yet synced for this agent.
    """
    if agent_id not in _wallets:
        raise HTTPException(
            status_code=404,
            detail=f"no wallet snapshot for agent {agent_id!r} — walletd may not be running",
        )
    return _wallets[agent_id]
