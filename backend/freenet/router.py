"""Freenet status and control API endpoints.

GET  /api/freenet/status     — node connectivity, peer count, contracts
GET  /api/freenet/rooms      — known GuildRoom contract keys
POST /api/freenet/rooms      — create a new GuildRoom contract (Phase F3)
GET  /api/freenet/health     — raw node health
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent
from .service import get_freenet_service
from .types import FreenetStatus

router = APIRouter(prefix="/api/freenet", tags=["freenet"])


@router.get("/status")
async def freenet_status(agent: dict = Depends(get_agent)):
    svc = get_freenet_service()
    info = await svc.health()
    return {
        "status": info.status.value,
        "connected": svc.is_connected,
        "node_id": info.node_id,
        "peer_count": info.peer_count,
        "contract_count": info.contract_count,
        "subscription_count": info.subscription_count,
        "version": info.version,
        "error": info.error,
        "phase": "F1",
        "note": "Freenet integration Phase F1 — local node not yet running. "
                "Clone third_party/freenet-core and start the node to activate.",
    }


@router.get("/health")
async def freenet_health(agent: dict = Depends(get_agent)):
    svc = get_freenet_service()
    return await svc.health()


@router.get("/rooms")
async def list_rooms(agent: dict = Depends(get_agent)):
    """List known GuildRoom contracts. Phase F3+."""
    return {"rooms": [], "phase": "F3", "note": "GuildRoom contracts — Phase F3"}


class CreateRoomRequest(BaseModel):
    guild_id: str
    room_id: str
    room_name: str


@router.post("/rooms")
async def create_room(body: CreateRoomRequest, agent: dict = Depends(get_agent)):
    """Create a GuildRoom contract on Freenet. Phase F3+."""
    svc = get_freenet_service()
    if not svc.is_connected:
        raise HTTPException(503, "Freenet node not connected — Phase F3 not yet active")
    key = await svc.create_room(body.guild_id, body.room_id, body.room_name)
    return {"contract_key": str(key) if key else None}


@router.get("/peers")
async def list_peers(agent: dict = Depends(get_agent)):
    svc = get_freenet_service()
    return {"peers": await svc.get_peers()}
