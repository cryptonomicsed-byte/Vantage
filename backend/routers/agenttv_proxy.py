"""Agent.TV -- the always-on 24/7 channel, now Live-TV-style multi-channel
(native, in-process; replaces the old Seemplify/Piper proxy entirely, see
backend/agenttv_channel.py for the real rotation/channel logic and
backend/podcast_engine.py for the generation engine).

2026-07-27: added a real channel guide -- every agent with published
podcast content is its own channel (GET /channels), not one fixed global
rotation. The flagship "Agent.TV" system agent channel is live/always-
generating; every other agent's channel deterministically loops their own
already-published episodes so every viewer watching it is in sync."""
from fastapi import APIRouter

from ..agenttv_channel import channel, list_channels, now_playing_for_channel

router = APIRouter(prefix="/api/cinema/agenttv", tags=["cinema"])


@router.get("/channels")
async def channels():
    return await list_channels()


@router.get("/now-playing")
async def now_playing(agent_id: int | None = None):
    if agent_id is None:
        return channel.now_playing()
    return await now_playing_for_channel(agent_id)
