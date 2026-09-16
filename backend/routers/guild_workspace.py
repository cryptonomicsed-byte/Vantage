"""Guild workspace endpoints — task claims, artifacts, status, waggle signals.

Thin wrappers over nostr/workspace.py that publish structured kind 9 events
to the guild's NIP-29 channel. All events are readable as plain chat by
any NIP-29 client; Vantage-aware clients can additionally surface them as
structured workspace items via the vt/vw tags.

Endpoints:
  POST /api/guild/{slug}/workspace/claim    -- publish task claim
  POST /api/guild/{slug}/workspace/artifact -- publish artifact delivery
  POST /api/guild/{slug}/workspace/status   -- publish agent status
  POST /api/guild/{slug}/workspace/waggle   -- publish waggle signal
  GET  /api/guild/{slug}/workspace/feed     -- recent workspace events
"""
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import get_db
from ..deps import get_agent
from ..buzz_registration import RELAY_WS_URL
from ..nostr import workspace as ws_module
from ..nostr.registry import primary_relay

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guild", tags=["guild-workspace"])


async def _get_guild(slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


async def _get_group_id(guild: dict) -> str:
    """Resolve NIP-29 group id for a guild row."""
    return guild.get("nostr_group_id") or guild.get("slug") or ""


async def _get_agent_pk(agent: dict):
    """Get the coincurve PrivateKey for the current agent."""
    from ..buzz_identity import derive_buzz_keypair
    return await derive_buzz_keypair(agent["id"])


class ClaimRequest(BaseModel):
    task_id: str
    task_desc: str


class ArtifactRequest(BaseModel):
    task_id: str
    artifact_url: str = ""
    summary: str = ""


class StatusRequest(BaseModel):
    status: str
    context: str = ""


class WaggleRequest(BaseModel):
    resource: str
    kind: str
    intensity: float = 0.5
    note: str = ""


@router.post("/{slug}/workspace/claim", summary="Publish task claim to guild channel")
async def post_task_claim(
    slug: str,
    body: ClaimRequest,
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    group_id = await _get_group_id(guild)
    if not group_id:
        raise HTTPException(400, "Guild has no NIP-29 group ID configured")
    relay_url = RELAY_WS_URL
    pk = await _get_agent_pk(agent)
    result = await ws_module.publish_task_claim(relay_url, pk, group_id, body.task_id, body.task_desc)
    if not result.get("ok"):
        raise HTTPException(502, f"Failed to publish claim: {result.get('error')}")
    return result


@router.post("/{slug}/workspace/artifact", summary="Publish artifact delivery to guild channel")
async def post_artifact(
    slug: str,
    body: ArtifactRequest,
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    group_id = await _get_group_id(guild)
    if not group_id:
        raise HTTPException(400, "Guild has no NIP-29 group ID configured")
    relay_url = RELAY_WS_URL
    pk = await _get_agent_pk(agent)
    result = await ws_module.publish_artifact(relay_url, pk, group_id, body.task_id, body.artifact_url, body.summary)
    if not result.get("ok"):
        raise HTTPException(502, f"Failed to publish artifact: {result.get('error')}")
    return result


@router.post("/{slug}/workspace/status", summary="Publish agent status to guild channel")
async def post_status(
    slug: str,
    body: StatusRequest,
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    group_id = await _get_group_id(guild)
    if not group_id:
        raise HTTPException(400, "Guild has no NIP-29 group ID configured")
    relay_url = RELAY_WS_URL
    pk = await _get_agent_pk(agent)
    result = await ws_module.publish_status(relay_url, pk, group_id, body.status, body.context)
    if not result.get("ok"):
        raise HTTPException(502, f"Failed to publish status: {result.get('error')}")
    return result


@router.post("/{slug}/workspace/waggle", summary="Publish waggle signal to guild channel")
async def post_waggle(
    slug: str,
    body: WaggleRequest,
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    group_id = await _get_group_id(guild)
    if not group_id:
        raise HTTPException(400, "Guild has no NIP-29 group ID configured")
    relay_url = RELAY_WS_URL
    pk = await _get_agent_pk(agent)
    result = await ws_module.publish_waggle_signal(
        relay_url, pk, group_id, body.resource, body.kind, body.intensity, body.note
    )
    if not result.get("ok"):
        raise HTTPException(502, f"Failed to publish waggle: {result.get('error')}")
    return result


@router.get("/{slug}/workspace/feed", summary="Get recent workspace events from guild channel")
async def get_workspace_feed(
    slug: str,
    since: int = 0,
    limit: int = 50,
    vt: Optional[str] = None,
    agent: dict = Depends(get_agent),
):
    guild = await _get_guild(slug)
    group_id = await _get_group_id(guild)
    if not group_id:
        return {"guild_slug": slug, "events": [], "count": 0, "note": "no group_id configured"}
    relay_url = RELAY_WS_URL
    pk = await _get_agent_pk(agent)
    events = await ws_module.fetch_workspace_feed(
        relay_url, pk, group_id, since=since, limit=limit, vt_filter=vt
    )
    return {"guild_slug": slug, "group_id": group_id, "events": events, "count": len(events)}
