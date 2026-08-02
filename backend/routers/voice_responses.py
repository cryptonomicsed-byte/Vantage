"""Minimal OpenAI /v1/responses-compatible shim for Vantage's own
on-demand speech-to-speech pipeline (voice_session.py). The pipeline
process itself only ever calls this over 127.0.0.1 (its
--responses_api_base_url is hardcoded to Vantage's own loopback address),
but this route rides the same public FastAPI app as everything else, so
it's reachable at the same domain as any other /api/* route -- the
bearer-token check below (not network location) is what actually gates
access; an unknown/expired token is rejected outright.

The huggingface/speech-to-speech package's `--llm_backend responses-api`
mode is a real OpenAI SDK client calling `client.responses.create(...)` --
it expects genuine Responses-API request/response (and SSE streaming
event) shapes, not a generic chat-completions body. This implements just
enough of that surface for the pipeline's single non-tool-calling text
turn to round-trip through Vantage's existing Copilot dispatch
(_dispatch_chat in routers/copilot.py) -- so a voice reply comes from
whichever agent/provider is actually active in that agent's Copilot, not
a hardcoded model.

Auth: the pipeline is launched with --responses_api_api_key set to a
random per-session token (voice_session.py); this shim looks that token
up via voice_session.resolve_token() to find which agent_id/agent_name it
belongs to. An unknown/expired token is rejected outright -- there is no
fallback identity.

Streaming only (the installed package's client always sets stream=True
for its live turn requests; there is no CLI flag to force non-streaming).
Vantage's own dispatch returns a complete reply in one shot rather than
token-by-token, so this sends it as a single delta chunk followed by the
completion events -- a valid (if degenerate, chunk-of-one) SSE stream per
the OpenAI Responses API's own event contract, not a protocol violation.
"""
import json
import logging
import time
import uuid

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db import get_db
from ..voice_session import resolve_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/voice", tags=["voice-internal"])


def _extract_text(body: dict) -> str:
    """Pull the latest user message's text out of a Responses API `input`
    array. Only text content is spoken by this pipeline -- no images/audio
    blocks are expected here, so anything else is skipped."""
    for item in reversed(body.get("input") or []):
        if item.get("type") == "message" and item.get("role") == "user":
            for c in item.get("content") or []:
                if c.get("type") in ("input_text", "text") and c.get("text"):
                    return c["text"]
    return ""


def _sse(event_type: str, payload: dict) -> bytes:
    payload = {"type": event_type, **payload}
    return f"data: {json.dumps(payload)}\n\n".encode()


@router.post("/responses")
async def create_response(request: Request):
    auth = request.headers.get("authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    session = resolve_token(token)
    if not session:
        raise HTTPException(401, "unknown or expired voice session token")

    body = await request.json()
    text = _extract_text(body)
    if not text:
        raise HTTPException(422, "no user text found in Responses API `input`")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents WHERE id=?", (session["agent_id"],))
        agent_row = await cur.fetchone()
    if not agent_row:
        raise HTTPException(404, "voice session's agent no longer exists")

    from .copilot import _dispatch_chat
    result = await _dispatch_chat(dict(agent_row), text)
    reply_text = ((result.get("data") or {}).get("reply")) or "Sorry, I didn't catch that."

    response_id = f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"
    created_at = time.time()

    async def stream():
        seq = 0

        def next_seq():
            nonlocal seq
            seq += 1
            return seq

        yield _sse("response.output_text.delta", {
            "content_index": 0, "item_id": item_id, "output_index": 0,
            "logprobs": [], "delta": reply_text, "sequence_number": next_seq(),
        })

        output_message = {
            "id": item_id, "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": reply_text, "annotations": [], "logprobs": []}],
        }
        yield _sse("response.output_item.done", {
            "item": output_message, "output_index": 0, "sequence_number": next_seq(),
        })

        response_obj = {
            "id": response_id, "object": "response", "created_at": created_at,
            "model": body.get("model", session["agent_name"]), "status": "completed",
            "output": [output_message], "parallel_tool_calls": False,
            "tool_choice": "auto", "tools": [],
            "usage": {
                "input_tokens": max(1, len(text) // 4), "output_tokens": max(1, len(reply_text) // 4),
                "total_tokens": max(2, (len(text) + len(reply_text)) // 4),
                "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        yield _sse("response.completed", {"response": response_obj, "sequence_number": next_seq()})

    return StreamingResponse(stream(), media_type="text/event-stream")
