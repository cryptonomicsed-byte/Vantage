"""Connecting a Vantage agent to a real 'mind' -- a webhook that actually
thinks, instead of the built-in regex intent parser Copilot falls back to.

The contract is deliberately generic, not tied to any one agent framework:

    POST {cognition_url}
    Headers: Authorization: Bearer {cognition_auth_token}   (if set)
    Body:    {"agent_name": str, "text": str, "human_id": str | null,
              "agent_id": str | null, "agent_key": str | null}
    Response 200: {"reply": str}

`agent_id`/`agent_key` are optional extra fields -- Omo-Koda2's kernel uses
them to route to a specific guest identity internally (confirmed live with
the Omo-Koda2 session, 2026-07-26); any other framework that only needs
{agent_name, text, human_id} -> {reply} can ignore them entirely. Nothing
in backend/routers/copilot.py's dispatch logic assumes Omo-Koda2
specifically -- it's a pure webhook call, framework-agnostic by design.

Two ways an agent's owner can wire this up:
  1. connect_generic_mind() -- paste your own agent framework's webhook
     URL (+ optional token) directly. Works for anything implementing the
     contract above: Omo-Koda2, a Hermes agent, LangGraph, a hand-rolled
     Flask endpoint, whatever.
  2. link_omokoda_mind() -- convenience path: births a real Omo-Koda2
     guest agent via /v1/birth and auto-wires cognition_url/agent_id/
     agent_key for you. One option among several, not the only path.
"""
import logging
from typing import Optional

import httpx

from .config import settings
from .db import get_db

logger = logging.getLogger(__name__)


async def get_mind_status(agent_id: int) -> dict:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT cognition_url, omokoda_agent_id FROM agents WHERE id = ?", (agent_id,)
        )
        row = await cur.fetchone()
    cognition_url, omokoda_agent_id = (row[0], row[1]) if row else (None, None)
    return {
        "connected": bool(cognition_url),
        "cognition_url": cognition_url,
        "kind": "omokoda" if omokoda_agent_id else ("custom" if cognition_url else None),
    }


async def connect_generic_mind(agent_id: int, cognition_url: str, cognition_auth_token: Optional[str]) -> dict:
    """Generic path: the agent's owner supplies their own webhook. No
    framework-specific fields are set -- omokoda_agent_id/agent_key stay
    null, so _dispatch_chat sends only the base {agent_name, text,
    human_id} contract."""
    cognition_url = cognition_url.strip()
    if not cognition_url.startswith(("http://", "https://")):
        raise ValueError("cognition_url must be a real http(s) URL")

    async with get_db() as db:
        await db.execute(
            """UPDATE agents SET cognition_url = ?, cognition_auth_token = ?,
               omokoda_agent_id = NULL, omokoda_agent_key = NULL WHERE id = ?""",
            (cognition_url, cognition_auth_token, agent_id),
        )
        await db.commit()
    return {"ok": True, "kind": "custom", "cognition_url": cognition_url}


async def disconnect_mind(agent_id: int) -> dict:
    async with get_db() as db:
        await db.execute(
            """UPDATE agents SET cognition_url = NULL, cognition_auth_token = NULL,
               omokoda_agent_id = NULL, omokoda_agent_key = NULL WHERE id = ?""",
            (agent_id,),
        )
        await db.commit()
    return {"ok": True}


async def link_omokoda_mind(agent_id: int, agent_name: str) -> dict:
    """Convenience path: birth a real Omo-Koda2 guest agent and auto-wire
    this Vantage agent's cognition_url to it. Real live verification
    round trip before declaring success -- same pattern as the Buzz
    registration flow."""
    if not settings.OMOKODA_URL:
        raise RuntimeError("Omo-Koda2 kernel not configured (OMOKODA_URL unset)")
    if not settings.OMOKODA_COGNITION_TOKEN:
        raise RuntimeError("Omo-Koda2 cognition webhook not configured (OMOKODA_COGNITION_TOKEN unset)")

    birth_url = f"{settings.OMOKODA_URL.rstrip('/')}/v1/birth"
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(birth_url, json={"name": agent_name, "meta": []})
        r.raise_for_status()
        born = r.json()

    # Real gotcha (confirmed live): agent_key is only the vantage_... key if
    # Vantage's own auto-registration succeeded during birth; otherwise it
    # falls back to equal agent_id. Read the actual fields, don't assume.
    omokoda_agent_id = born.get("agent_id")
    omokoda_agent_key = born.get("agent_key")
    if not omokoda_agent_id or not omokoda_agent_key:
        raise RuntimeError(f"Omo-Koda2 birth response missing agent_id/agent_key: {born}")

    cognition_url = f"{settings.OMOKODA_URL.rstrip('/')}/v1/cognition"

    async with get_db() as db:
        await db.execute(
            """UPDATE agents SET cognition_url = ?, cognition_auth_token = ?,
               omokoda_agent_id = ?, omokoda_agent_key = ? WHERE id = ?""",
            (cognition_url, settings.OMOKODA_COGNITION_TOKEN, omokoda_agent_id, omokoda_agent_key, agent_id),
        )
        await db.commit()

    # Real verification round trip -- confirm the newborn actually answers
    # through the exact same path Copilot will use, not just that birth
    # returned 200.
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            verify = await client.post(
                cognition_url,
                json={
                    "agent_name": agent_name, "text": "Hello, who are you?", "human_id": None,
                    "agent_id": omokoda_agent_id, "agent_key": omokoda_agent_key,
                },
                headers={"Authorization": f"Bearer {settings.OMOKODA_COGNITION_TOKEN}"},
            )
        verify_ok = verify.status_code == 200 and bool(verify.json().get("reply"))
        verify_reply = verify.json().get("reply") if verify_ok else None
    except Exception as exc:
        verify_ok = False
        verify_reply = None
        logger.warning("omokoda link verify round trip failed for agent %s: %s", agent_id, exc)

    return {
        "ok": True, "kind": "omokoda", "cognition_url": cognition_url,
        "omokoda_agent_id": omokoda_agent_id, "verified": verify_ok, "verify_reply": verify_reply,
    }
