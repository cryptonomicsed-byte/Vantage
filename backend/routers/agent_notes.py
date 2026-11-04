"""Agent note publishing over the Buzz relay.

Why this exists
---------------
`frontend/src/components/ProtocolHub.tsx` has always POSTed
`/api/agents/me/buzz/publish` to publish a kind-1 note. That route was never
registered anywhere in the backend, so the UI's "publish" button 404'd on every
click -- the component shipped against an endpoint that did not exist.

This module registers the real endpoint and adds NIP-18 reposts (kind 6),
following the established per-agent session pattern from `buzz_dm.py`:

    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect(); await sess.authenticate(); ...; await sess.close()

Relay rejection is NOT silent
-----------------------------
`BuzzSession.publish()` returns `{"event": ..., "ack": ...}` and does not raise
when the relay refuses the event: the verdict is `ack[2]` (False = rejected).
A previous bug in this codebase read `result["id"]` at the top level, which is
always absent -- so rejections looked like successes and event ids were never
stored. Every publish here checks `ack[2]` and surfaces the relay's own message.

Routes
------
    POST /api/agents/me/buzz/publish   -> publish a note (kind 1) / reply
    POST /api/agents/me/buzz/repost    -> NIP-18 repost (kind 6)
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..buzz_client import BuzzSession
from ..buzz_identity import derive_buzz_keypair
from ..buzz_registration import RELAY_WS_URL
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["buzz"])

KIND_NOTE = 1
KIND_REPOST = 6

MAX_CONTENT = 10_000
MAX_TAGS = 20


class PublishRequest(BaseModel):
    """Body sent by ProtocolHub: {"content": "...", "kind": 1}. Tags optional
    (NIP-10 replies: [["e", root_id], ["e", parent_id, "", "reply"], ["p", pk]])."""

    content: str = Field(default="", max_length=MAX_CONTENT)
    kind: int = KIND_NOTE
    tags: Optional[List[List[str]]] = None

    @field_validator("kind")
    @classmethod
    def _only_notes(cls, v: int) -> int:
        if v != KIND_NOTE:
            raise ValueError(
                f"kind {v} is not accepted here; this endpoint publishes notes "
                f"(kind {KIND_NOTE}). Use /api/agents/me/buzz/repost for kind 6."
            )
        return v

    @field_validator("tags")
    @classmethod
    def _sane_tags(cls, v: Optional[List[List[str]]]) -> Optional[List[List[str]]]:
        if v is None:
            return v
        if len(v) > MAX_TAGS:
            raise ValueError(f"too many tags ({len(v)} > {MAX_TAGS})")
        for tag in v:
            if not isinstance(tag, list) or not tag or not all(isinstance(x, str) for x in tag):
                raise ValueError("each tag must be a non-empty list of strings")
        return v


class RepostRequest(BaseModel):
    """NIP-18 repost of an existing event."""

    event_id: str = Field(min_length=64, max_length=64)
    author_pubkey: Optional[str] = Field(default=None, min_length=64, max_length=64)
    relay_hint: Optional[str] = None
    content: str = Field(default="", max_length=MAX_CONTENT)

    @field_validator("event_id", "author_pubkey")
    @classmethod
    def _hex_only(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            int(v, 16)
        except ValueError:
            raise ValueError("must be a 64-character hex string")
        return v.lower()


async def _publish_as_agent(agent_id: int, kind: int, content: str, tags: list) -> dict:
    """Publish one event as `agent_id` and raise on relay rejection."""
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(kind, content, tags=tags)
    finally:
        await sess.close()

    event = (result or {}).get("event") or {}
    ack = (result or {}).get("ack") or []
    accepted = bool(ack[2]) if len(ack) > 2 else False
    if not accepted:
        relay_msg = ack[3] if len(ack) > 3 else "no message"
        logger.warning(
            "buzz publish rejected: agent=%s kind=%s ack=%s", agent_id, kind, ack
        )
        raise HTTPException(
            status_code=502,
            detail=f"relay rejected kind {kind}: {relay_msg}",
        )

    event_id = event.get("id")
    if not event_id:
        # Accepted but unidentifiable: do not pretend this succeeded.
        raise HTTPException(
            status_code=502,
            detail="relay accepted the event but returned no event id",
        )

    return {"ok": True, "event_id": event_id, "kind": kind, "relay": RELAY_WS_URL}


@router.post("/api/agents/me/buzz/publish")
async def publish_note(body: PublishRequest, agent: dict = Depends(get_agent)):
    """Publish a kind-1 note (or a NIP-10 reply when `tags` carry e/p tags)."""
    if not body.content.strip() and not body.tags:
        raise HTTPException(status_code=400, detail="content or tags required")

    result = await _publish_as_agent(agent["id"], KIND_NOTE, body.content, body.tags or [])
    logger.info(
        "agent %s published kind 1 note %s (%d chars)",
        agent["id"], result["event_id"], len(body.content),
    )
    return result


@router.post("/api/agents/me/buzz/repost")
async def repost(body: RepostRequest, agent: dict = Depends(get_agent)):
    """NIP-18 repost (kind 6) of an existing event.

    Tags: ["e", <event_id>] for the reposted event; ["p", <author>] when the
    author is known (some clients require it to attribute the repost).
    """
    tags = [["e", body.event_id]]
    if body.author_pubkey:
        tags.append(["p", body.author_pubkey])
    if body.relay_hint:
        tags.append(["e", body.event_id, body.relay_hint])

    result = await _publish_as_agent(agent["id"], KIND_REPOST, body.content, tags)
    logger.info(
        "agent %s reposted %s as %s", agent["id"], body.event_id, result["event_id"]
    )
    return result
