"""Voice sessions as first-class Vantage objects.

Extends -- does not replace -- backend/voice_session.py, which still owns the
legacy HuggingFace-S2S subprocess. This router is what any voice surface
(Vantage-Voice-'s Gemini Live client, the cascade fallback, eventually the S2S
path) talks to so that a conversation becomes something Vantage can show on a
dashboard, search, audit, and keep across a restart.

Two auth tiers, deliberately:

  * Lifecycle and reads (create/list/get/stop/transcript/search) need the
    agent's real X-Agent-Key.
  * Write-through (turns, tool calls) needs only that session's vvoice_ token,
    which is scoped to one session and can do nothing else. The voice app
    holds this instead of an agent key, which is what lets the owner-PIN model
    go away.
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import voice_session_store as store
from ..deps import get_agent, get_voice_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["voice_sessions"])

_SSE_POLL_SECONDS = 1.0
_SSE_MAX_SECONDS = 900


async def _parse_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def _owned_session(session_id: str, agent: dict) -> dict:
    session = await store.get_session(session_id, agent["id"])
    if not session:
        raise HTTPException(404, "Voice session not found")
    return session


# ── Lifecycle (X-Agent-Key) ──────────────────────────────────────────────────

@router.post("/me/voice/sessions", status_code=201)
async def create_voice_session(request: Request, agent: dict = Depends(get_agent)):
    """Open a voice session and mint its scoped token.

    The token comes back exactly once; only its hash is stored. Hand it to the
    voice client and it can write that session's transcript without ever
    holding this agent's key.
    """
    body = await _parse_body(request)
    tools = body.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise HTTPException(422, "tools must be a list of tool-name patterns")
    try:
        session = await store.create_session(
            agent_id=agent["id"],
            engine=str(body.get("engine") or "gemini_live"),
            framework=str(body.get("framework") or "native"),
            persona=str(body.get("persona") or ""),
            voice=str(body.get("voice") or ""),
            tools=tools,
            ttl_seconds=body.get("ttl_seconds") or store.DEFAULT_TTL_SECONDS,
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    token = session.pop("token")
    return {
        **session,
        "session_id": session["id"],
        "token": token,
        "token_header": "Authorization: Bearer <token>",
        "ws_url": f"/api/agents/me/voice/sessions/{session['id']}/ws?key={token}",
    }


@router.get("/me/voice/sessions")
async def list_voice_sessions(
    agent: dict = Depends(get_agent),
    status: Optional[str] = Query(None, description="active | idle | stopped | failed"),
    limit: int = Query(50, ge=1, le=200),
):
    # Fold in anything that has since gone idle, so "active" means active.
    await store.expire_idle_sessions()
    return {"sessions": await store.list_sessions(agent["id"], status, limit)}


@router.get("/me/voice/sessions/search")
async def search_voice_transcripts(
    agent: dict = Depends(get_agent),
    q: str = Query(..., min_length=1, description="FTS5 query over this agent's voice transcripts"),
    limit: int = Query(25, ge=1, le=100),
):
    """Declared before /{session_id} so a search never parses as a session id."""
    return {"query": q, "results": await store.search_transcripts(agent["id"], q, limit)}


@router.get("/me/voice/sessions/{session_id}")
async def get_voice_session_status(session_id: str, agent: dict = Depends(get_agent)):
    session = await _owned_session(session_id, agent)
    return {**session, **await store.session_stats(session_id)}


@router.post("/me/voice/sessions/{session_id}/stop")
async def stop_voice_session(session_id: str, request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    try:
        return await store.stop_session(session_id, agent["id"], str(body.get("reason") or "client_stopped"))
    except LookupError:
        raise HTTPException(404, "Voice session not found")


@router.get("/me/voice/sessions/{session_id}/transcript")
async def get_voice_session_transcript(
    session_id: str,
    agent: dict = Depends(get_agent),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    await _owned_session(session_id, agent)
    return {
        "session_id": session_id,
        "turns": await store.get_transcript(session_id, limit, offset),
        "tool_calls": await store.list_tool_calls(session_id),
    }


@router.get("/me/voice/sessions/{session_id}/events")
async def stream_voice_session_events(session_id: str, agent: dict = Depends(get_agent)):
    """Live transcript feed for the dashboard, as SSE.

    Polls for turns past the last one sent rather than holding a subscription,
    which keeps it correct across the multiple writers a session can have (the
    voice client, and the tool-call logger) without any shared in-process bus.
    """
    await _owned_session(session_id, agent)

    async def gen():
        last_seq = 0
        waited = 0.0
        while waited < _SSE_MAX_SECONDS:
            turns = await store.get_transcript(session_id, limit=200, offset=last_seq)
            for turn in turns:
                if turn["sequence_num"] > last_seq:
                    last_seq = turn["sequence_num"]
                    yield f"event: turn\ndata: {json.dumps(turn)}\n\n"
            session = await store.get_session(session_id)
            if not session or session["status"] in ("stopped", "failed"):
                yield f"event: session_end\ndata: {json.dumps({'session_id': session_id})}\n\n"
                return
            await asyncio.sleep(_SSE_POLL_SECONDS)
            waited += _SSE_POLL_SECONDS
        yield f"event: stream_timeout\ndata: {json.dumps({'session_id': session_id})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Write-through (scoped vvoice_ token) ─────────────────────────────────────

@router.post("/me/voice/sessions/{session_id}/turns", status_code=201)
async def append_voice_turn(session_id: str, request: Request,
                            session: dict = Depends(get_voice_session)):
    """Append one turn to this session's transcript.

    The token identifies the session, so the path id is checked against it
    rather than trusted -- a token for one session cannot write into another.
    """
    if session["id"] != session_id:
        raise HTTPException(403, "Token does not belong to this voice session")
    body = await _parse_body(request)
    try:
        return await store.append_turn(
            session_id=session_id,
            agent_id=session["agent_id"],
            role=str(body.get("role") or ""),
            content_text=body.get("content_text") or "",
            content_audio_transcript=body.get("content_audio_transcript") or "",
            content_audio_path=body.get("content_audio_path") or "",
            tool_call_id=body.get("tool_call_id"),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/me/voice/sessions/{session_id}/tool-calls", status_code=201)
async def record_voice_tool_call(session_id: str, request: Request,
                                 session: dict = Depends(get_voice_session)):
    """Log a tool call. Post it when dispatched, then PATCH the result, so a
    tool that never returns still leaves evidence it was attempted."""
    if session["id"] != session_id:
        raise HTTPException(403, "Token does not belong to this voice session")
    body = await _parse_body(request)
    tool_name = str(body.get("tool_name") or "").strip()
    if not tool_name:
        raise HTTPException(422, "tool_name is required")
    call_id = await store.record_tool_call(
        session_id=session_id,
        agent_id=session["agent_id"],
        tool_name=tool_name,
        tool_source=str(body.get("tool_source") or "vantage_mcp"),
        arguments=body.get("arguments"),
        turn_id=body.get("turn_id"),
    )
    return {"tool_call_id": call_id, "session_id": session_id}


@router.patch("/me/voice/sessions/{session_id}/tool-calls/{call_id}")
async def complete_voice_tool_call(session_id: str, call_id: str, request: Request,
                                   session: dict = Depends(get_voice_session)):
    if session["id"] != session_id:
        raise HTTPException(403, "Token does not belong to this voice session")
    body = await _parse_body(request)
    duration = body.get("duration_ms")
    await store.complete_tool_call(
        call_id=call_id,
        result=body.get("result"),
        is_error=bool(body.get("is_error")),
        duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
    )
    return {"ok": True, "tool_call_id": call_id}


@router.post("/me/voice/sessions/{session_id}/heartbeat")
async def heartbeat_voice_session(session_id: str, session: dict = Depends(get_voice_session)):
    """Keep a session alive through a long silence. Without this a quiet call
    would hit its idle TTL and have its token burned mid-conversation."""
    if session["id"] != session_id:
        raise HTTPException(403, "Token does not belong to this voice session")
    await store.touch(session_id)
    return {"ok": True, "session_id": session_id}
