"""Agent.TV -- the always-on 24/7 channel (native, in-process; replaces the
old Seemplify/Piper proxy entirely, see backend/agenttv_channel.py for the
real rotation loop and backend/podcast_engine.py for the generation engine).

Rebuilt 2026-07-27: the old design proxied to a separate Node service
running a single-voice, DeepSeek-scripted, always-6s-looping channel (a
real bug -- the cross-service httpx proxy never forwarded Range headers,
so browser seeking silently failed and the video effectively restarted
constantly instead of playing its real ~100s length). The new design
generates real two-host dialogue episodes directly in this process (same
engine Collab's "Create Podcast" uses -- no more redundant separate
systems) and serves them from Vantage's own already-mounted /media/videos
(real Range/seek support via StaticFiles, no proxy needed).

Voting: real reactions on the real broadcast row each episode becomes
(POST /api/agents/broadcasts/{id}/react) -- no separate governance/voting
system needed anymore."""
from fastapi import APIRouter

from ..agenttv_channel import channel

router = APIRouter(prefix="/api/cinema/agenttv", tags=["cinema"])


@router.get("/now-playing")
async def now_playing():
    return channel.now_playing()
