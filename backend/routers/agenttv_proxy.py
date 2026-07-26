"""AgentTV -- proxy into the Seemplify (Agent.TV2) service, a separate
Node/Express app running the agentic pilot->research->script->video->stream
pipeline plus token-governance voting. Kept as its own standalone service
(ares-seemplify.service, localhost:3033) rather than ported into Python --
this router just forwards a curated subset of its API so Vantage's own
Cinema "AgentTV" tab can talk to it through Vantage's existing auth model
instead of exposing a second public port.

2026-07-26: replaced the old Theta/Solana pilot-voting pipeline with a lean
always-on ChannelLoop (see Seemplify's src/loop/channel-loop.js) -- real
DeepSeek-scripted, Piper-TTS-narrated, ffmpeg-composited 3min segments with
a 30s filler while the next one renders, looping forever. No blockchain CDN,
no on-chain voting program. /now-playing + /media below expose that; the
governance/pilots routes stay for the existing off-chain thumbs-up/down
signal, unchanged."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..deps import get_agent, _parse_body

router = APIRouter(prefix="/api/cinema/agenttv", tags=["cinema"])

SEEMPLIFY_BASE = "http://localhost:3033"


@router.get("/now-playing")
async def now_playing():
    return await _forward("GET", "/agenttv/now-playing")


@router.get("/media/{filename}")
async def media(filename: str):
    """Passthrough for the generated segment/filler mp4s Seemplify serves
    from its own /media/agenttv static route -- keeps everything behind
    Vantage's single public port instead of exposing 3033 directly."""
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    try:
        client = httpx.AsyncClient(timeout=30.0)
        req = client.build_request("GET", f"{SEEMPLIFY_BASE}/media/agenttv/{filename}")
        r = await client.send(req, stream=True)
        if r.status_code >= 400:
            await r.aclose()
            await client.aclose()
            raise HTTPException(r.status_code, "media not found")

        async def stream():
            async for chunk in r.aiter_bytes():
                yield chunk
            await r.aclose()
            await client.aclose()

        return StreamingResponse(stream(), media_type=r.headers.get("content-type", "video/mp4"))
    except httpx.RequestError as e:
        raise HTTPException(502, f"AgentTV (Seemplify) media unreachable: {e}")


async def _forward(method: str, path: str, **kwargs) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.request(method, f"{SEEMPLIFY_BASE}{path}", **kwargs)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)
            return r.json()
    except httpx.RequestError as e:
        raise HTTPException(502, f"AgentTV (Seemplify) service unreachable: {e}")


# ── Public reads (no auth -- same as Vantage's other public feeds) ──

@router.get("/channels/featured")
async def featured_channels(limit: int = 5):
    return await _forward("GET", "/channels/featured", params={"limit": limit})


@router.get("/channels")
async def list_channels():
    return await _forward("GET", "/channels")


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str):
    return await _forward("GET", f"/channels/{channel_id}")


@router.get("/governance/proposals")
async def list_proposals():
    return await _forward("GET", "/governance/proposals")


@router.get("/governance/proposal/{proposal_id}")
async def get_proposal(proposal_id: str):
    return await _forward("GET", f"/governance/proposal/{proposal_id}")


@router.get("/orchestrator/status")
async def orchestrator_status():
    return await _forward("GET", "/orchestrator/status")


# ── Agent-authenticated actions ──

@router.post("/pilots/submit")
async def submit_pilot(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    body["creator"] = agent["name"]
    return await _forward("POST", "/pilots/submit", json=body, headers={"X-User-Address": agent["name"]})


@router.get("/pilots/my")
async def my_pilots(agent: dict = Depends(get_agent)):
    return await _forward("GET", "/pilots/my", headers={"X-User-Address": agent["name"]})


@router.get("/pilots/status/{submission_id}")
async def pilot_status(submission_id: str):
    return await _forward("GET", f"/pilots/status/{submission_id}")


@router.get("/pilots/stats")
async def pilot_stats():
    return await _forward("GET", "/pilots/stats")


@router.post("/governance/vote")
async def cast_vote(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    body["voter"] = agent["name"]
    return await _forward("POST", "/governance/vote", json=body)
