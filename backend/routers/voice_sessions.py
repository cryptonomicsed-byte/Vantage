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
import base64
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .. import voice_live, voice_session_store as store, voice_tools
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


# ── Audio relay (Phase 2) ────────────────────────────────────────────────────

@router.websocket("/me/voice/sessions/{session_id}/ws")
async def voice_session_relay(ws: WebSocket, session_id: str):
    """Browser audio in, model audio out, with Vantage in the middle.

    This is what removes the separate Vantage-Voice- deployment from the path:
    the browser talks to Vantage, Vantage holds the model connection, and every
    turn lands in the session's transcript on the way past.

    Auth is the `?key=` query param carrying the session's vvoice_ token, since
    browsers cannot set headers on a WebSocket handshake -- the same pattern
    /ws/feed and the legacy /me/voice/ws already use. The token identifies the
    session, so the path id is checked against it rather than trusted.

    Wire format matches what Vantage-Voice-'s React client already speaks, so
    the ported frontend and the standalone app can both point here.
    """
    token = ws.query_params.get("key", "")
    session = await store.resolve_ws_token(token)
    if not session or session["id"] != session_id:
        # 4401 rather than 403: the handshake is already upgraded by the time we
        # know, and the client distinguishes auth failure from a normal close.
        await ws.close(code=4401)
        return

    agent_id = session["agent_id"]
    await ws.accept()

    # Tools the model may call, resolved from this session's allowlist. No
    # allowlist means no tools -- see voice_tools for why the safe state is the
    # default here.
    selected_tools = voice_tools.select_tools(ws.app, session.get("tools_allowlist"))
    dispatcher = voice_tools.ToolDispatcher(
        ws.app,
        exec_token=store.derive_exec_token(token),
        tools=selected_tools,
        allow_destructive=bool((session.get("metadata") or {}).get("allow_destructive_tools")),
    )

    engine = None
    try:
        engine = await voice_live.create_engine(
            session.get("engine") or "gemini_live",
            api_key=voice_live.resolve_gemini_api_key(await _agent_row(agent_id)),
            voice=session.get("voice") or "",
            system_instruction=session.get("persona") or "",
            tools=voice_tools.to_gemini_declarations(selected_tools),
        )
        await engine.start()
    except Exception as exc:
        logger.warning("voice relay could not start engine for %s: %s", session_id, exc)
        await _send(ws, {"type": "error", "message": str(exc)})
        await ws.close(code=1011)
        return

    await _send(ws, {"type": "connected", "sessionId": session_id})

    # Accumulated across a turn; flushed to the transcript on turn_complete so
    # one row is one utterance rather than one row per streamed fragment.
    pending = {"user": "", "model": ""}

    async def flush_turn() -> None:
        user_text, model_text = pending["user"].strip(), pending["model"].strip()
        pending["user"] = pending["model"] = ""
        try:
            if user_text:
                await store.append_turn(session_id, agent_id, "user", content_audio_transcript=user_text)
            if model_text:
                await store.append_turn(session_id, agent_id, "assistant", content_text=model_text)
        except Exception as exc:
            # A transcript write must never kill a live call.
            logger.warning("voice relay could not persist turn for %s: %s", session_id, exc)

    async def browser_to_model() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "audio" and msg.get("audio"):
                try:
                    await engine.send_audio(base64.b64decode(msg["audio"]))
                except Exception as exc:
                    logger.debug("voice relay bad audio frame on %s: %s", session_id, exc)
            elif kind == "text" and msg.get("text"):
                await engine.send_text(str(msg["text"]))
            elif kind == "ping":
                await _send(ws, {"type": "pong"})

    async def model_to_browser() -> None:
        async for event in engine.events():
            if event.kind == voice_live.INPUT_TRANSCRIPT:
                pending["user"] += event.text
            elif event.kind == voice_live.OUTPUT_TRANSCRIPT:
                pending["model"] += event.text
            elif event.kind == voice_live.TURN_COMPLETE:
                await flush_turn()
            elif event.kind == voice_live.TOOL_CALL:
                await _handle_tool_call(ws, engine, dispatcher, session_id, agent_id, event)
                continue

            payload = event.to_client_message()
            if payload is not None:
                await _send(ws, payload)

    pump_in = asyncio.ensure_future(browser_to_model())
    pump_out = asyncio.ensure_future(model_to_browser())
    try:
        done, pending_tasks = await asyncio.wait(
            [pump_in, pump_out], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending_tasks:
            task.cancel()
        # Surface a pump that died of anything other than the client hanging up.
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.warning("voice relay pump failed for %s: %s", session_id, exc)
    except WebSocketDisconnect:
        pass
    finally:
        # Shielded because the ASGI server cancels this task the moment the
        # client disconnects. Without the shield the first await below raises
        # CancelledError and the rest never runs -- meaning a caller who hangs
        # up mid-sentence loses that turn, and the session stays "active" with
        # a live token until the idle TTL eventually reaps it.
        try:
            await asyncio.shield(_cleanup(ws, engine, flush_turn, session_id, agent_id))
        except asyncio.CancelledError:
            # Expected: the shielded work carries on to completion on its own.
            pass


async def _cleanup(ws, engine, flush_turn, session_id: str, agent_id: int) -> None:
    """Persist the open turn, drop the model connection, close the session."""
    try:
        await flush_turn()
    except Exception as exc:
        logger.warning("voice relay flush on close failed for %s: %s", session_id, exc)

    if engine is not None:
        try:
            await engine.close()
        except Exception as exc:
            logger.debug("voice relay engine close for %s: %s", session_id, exc)

    # The browser is gone, so the session is over -- close it and burn the
    # token rather than leaving it live until the idle TTL catches it.
    try:
        await store.stop_session(session_id, agent_id, "client_disconnected")
    except LookupError:
        pass
    except Exception as exc:
        logger.warning("voice relay could not stop session %s: %s", session_id, exc)

    try:
        await ws.close()
    except Exception:
        pass


async def _send(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _agent_row(agent_id: int) -> dict:
    import aiosqlite
    from ..db import get_db
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
    return dict(row) if row else {}


async def _handle_tool_call(ws, engine, dispatcher, session_id: str, agent_id: int, event) -> None:
    """Log the call, run it as the agent, and hand the result back to the model.

    Recorded before it runs so a tool that hangs or crashes the session still
    shows in the audit trail as attempted. Execution re-enters the app over
    ASGI, so the call goes through the same auth, sentencing, rate-limit and
    validation chain as any other caller -- speaking to the agent is not a way
    around anything an HTTP client would face.
    """
    source = "composio" if event.tool_name == voice_tools.COMPOSIO_TOOL else "vantage_api"
    call_id = None
    try:
        call_id = await store.record_tool_call(
            session_id, agent_id, event.tool_name, source, event.tool_args
        )
    except Exception as exc:
        logger.warning("voice relay could not log tool call on %s: %s", session_id, exc)

    await _send(ws, {"type": "tool_call", "toolName": event.tool_name, "toolArgs": event.tool_args})

    started = time.monotonic()
    result = await dispatcher.execute(event.tool_name, event.tool_args)
    duration_ms = int((time.monotonic() - started) * 1000)

    try:
        await engine.send_tool_result(event.tool_call_id, event.tool_name, result)
    except Exception as exc:
        logger.debug("voice relay could not return tool result on %s: %s", session_id, exc)

    await _send(ws, {
        "type": "tool_result",
        "toolName": event.tool_name,
        "status": result.get("status"),
        "durationMs": duration_ms,
    })

    if call_id:
        # Shielded for the same reason as the session cleanup: this pump is
        # cancelled on disconnect, and a tool call that was recorded as
        # dispatched but never resolved would leave the audit trail claiming it
        # is still in flight.
        try:
            await asyncio.shield(store.complete_tool_call(
                call_id, result,
                is_error=result.get("status") != "ok",
                duration_ms=duration_ms,
            ))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("voice relay could not complete tool call on %s: %s", session_id, exc)
