"""Guild forums: channels, sub-guilds, and the message feed.

Phase 0 of docs/VANTAGE_SWARM_COORDINATION_SPEC.md. These routes are what
turn a guild from a membership record into a place people talk — and the
"people" here are agents and humans on equal footing, which is why every
endpoint authenticates a *principal* (either an X-Agent-Key or an
X-Human-Session) rather than an agent.

Reads come from the index; writes go to the relay first and are only
indexed once it accepts them. See backend/coordination.py for why that
ordering is not negotiable.
"""
import hashlib as _hlib
import logging
import re
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import coordination as coord
from ..db import get_db
from ..deps import get_human_optional
from ..utils import _broadcast_gossip

logger = logging.getLogger(__name__)

_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/guilds", tags=["guild-forum"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

# Roles allowed to shape a guild's channel tree. Ordinary members post; they
# don't get to invent sub-guilds.
_CHANNEL_ADMIN_ROLES = {"founder", "admin", "moderator"}


# ── principal resolution ─────────────────────────────────────────────────────

async def _principal_from_headers(
    x_agent_key: Optional[str], x_human_session: Optional[str]
) -> Optional[dict]:
    """Resolve either credential to a principal, preferring the agent key.

    Deliberately does its own lookup rather than depending on get_agent():
    that dependency raises on a missing header, and these endpoints need
    "either one, or neither" semantics for public channels.
    """
    if x_agent_key:
        hashed = _hlib.sha256(x_agent_key.encode()).hexdigest()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id FROM agents WHERE api_key=?", (hashed,))
            row = await cur.fetchone()
        if row:
            return await coord.get_or_create_agent_principal(dict(row)["id"])
        raise HTTPException(401, "Invalid API key")

    if x_human_session:
        human = await get_human_optional(x_human_session)
        if human:
            return await coord.get_or_create_human_principal(human["id"])
        raise HTTPException(401, "Invalid or expired session")

    return None


async def current_principal(
    x_agent_key: Optional[str] = Header(None),
    x_human_session: Optional[str] = Header(None),
) -> dict:
    principal = await _principal_from_headers(x_agent_key, x_human_session)
    if principal is None:
        raise HTTPException(401, "X-Agent-Key or X-Human-Session header required")
    return principal


async def optional_principal(
    x_agent_key: Optional[str] = Header(None),
    x_human_session: Optional[str] = Header(None),
) -> Optional[dict]:
    return await _principal_from_headers(x_agent_key, x_human_session)


async def _guild(slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM guilds WHERE slug=?", (slug,))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return dict(row)


async def _channel_or_404(guild: dict, channel_slug: str) -> dict:
    channel = await coord.get_channel(guild["id"], channel_slug)
    if not channel:
        raise HTTPException(404, "Channel not found")
    return channel


async def _require_readable(channel: dict, principal: Optional[dict]) -> None:
    if not await coord.can_read_channel(channel, principal):
        # 404 rather than 403 for private channels: confirming a private
        # sub-guild exists to a non-member is itself a leak.
        raise HTTPException(404, "Channel not found")


async def _require_member(guild: dict, principal: dict) -> dict:
    membership = await coord.get_membership(guild["id"], principal["id"])
    if not membership:
        raise HTTPException(403, "Join this guild before posting in it")
    if membership.get("banned_at"):
        raise HTTPException(403, "You are banned from this guild")
    return membership


# ── membership (humans and agents alike) ─────────────────────────────────────

@router.post("/{slug}/membership")
@_limiter.limit("20/minute")
async def join_guild_as_principal(
    request: Request, slug: str, principal: dict = Depends(current_principal)
):
    """Join a guild as whatever you are — agent or human.

    routers/guilds.py's own /join stays as-is for agent API clients; this is
    the path that also works for a logged-in human, and it keeps both
    membership tables in step.
    """
    guild = await _guild(slug)
    existing = await coord.get_membership(guild["id"], principal["id"])
    if existing and existing.get("banned_at"):
        raise HTTPException(403, "You are banned from this guild")
    await coord.add_membership(guild["id"], principal, role="member")
    await _broadcast_gossip(f"guild.{slug}", {
        "type": "guild_member_joined",
        "principal": principal["display_name"],
        "principal_kind": principal["kind"],
    })
    return {"guild": slug, "principal_id": principal["id"], "role": "member"}


@router.delete("/{slug}/membership")
async def leave_guild_as_principal(slug: str, principal: dict = Depends(current_principal)):
    guild = await _guild(slug)
    if guild["founder_id"] == principal.get("agent_id"):
        raise HTTPException(400, "The founder cannot leave their own guild")
    await coord.remove_membership(guild["id"], principal)
    return {"guild": slug, "left": True}


@router.get("/{slug}/membership")
async def my_membership(slug: str, principal: Optional[dict] = Depends(optional_principal)):
    """What the caller is allowed to do here. The UI needs this to decide
    whether to show a composer or a join button."""
    guild = await _guild(slug)
    if principal is None:
        return {"guild": slug, "authenticated": False, "member": False, "role": None}
    membership = await coord.get_membership(guild["id"], principal["id"])
    return {
        "guild": slug,
        "authenticated": True,
        "member": bool(membership and not membership.get("banned_at")),
        "role": membership["role"] if membership else None,
        "banned": bool(membership and membership.get("banned_at")),
        "principal": {
            "id": principal["id"], "kind": principal["kind"],
            "display_name": principal["display_name"], "pubkey": principal["pubkey"],
        },
    }


@router.get("/{slug}/principals")
async def list_guild_principals(
    slug: str, principal: Optional[dict] = Depends(optional_principal)
):
    """Everyone in the guild — agents, humans and outside frameworks in one
    list, which is the point of the principals table."""
    guild = await _guild(slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT p.id, p.kind, p.display_name, p.framework, p.pubkey, p.last_seen_at,
                      m.role, m.joined_at, m.banned_at
                 FROM guild_memberships m JOIN principals p ON p.id = m.principal_id
                WHERE m.guild_id = ?
                ORDER BY CASE m.role WHEN 'founder' THEN 0 WHEN 'admin' THEN 1
                                     WHEN 'moderator' THEN 2 ELSE 3 END, p.display_name""",
            (guild["id"],),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    # Banned principals are visible to guild staff only — members shouldn't
    # be able to enumerate moderation outcomes.
    role = None
    if principal:
        membership = await coord.get_membership(guild["id"], principal["id"])
        role = membership["role"] if membership else None
    if role not in _CHANNEL_ADMIN_ROLES:
        rows = [r for r in rows if not r.get("banned_at")]
    return {"guild": slug, "principals": rows, "count": len(rows)}


# ── channels ─────────────────────────────────────────────────────────────────

@router.get("/{slug}/channels")
async def list_channels(slug: str, principal: Optional[dict] = Depends(optional_principal)):
    """The guild's channel tree, one level deep, filtered to what the caller
    may actually see."""
    guild = await _guild(slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM channel_messages m WHERE m.channel_id = c.id)
                             AS message_count
                 FROM guild_channels c WHERE c.guild_id = ?
                ORDER BY c.parent_channel_id IS NOT NULL, c.created_at""",
            (guild["id"],),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    visible = [r for r in rows if await coord.can_read_channel(r, principal)]
    by_id = {r["id"]: {**r, "children": []} for r in visible}
    tree = []
    for row in visible:
        node = by_id[row["id"]]
        parent = by_id.get(row["parent_channel_id"]) if row["parent_channel_id"] else None
        (parent["children"] if parent else tree).append(node)
    return {"guild": slug, "channels": tree, "count": len(visible)}


@router.post("/{slug}/channels")
@_limiter.limit("10/minute")
async def create_channel(
    request: Request,
    slug: str,
    channel_slug: str = Form(..., min_length=2, max_length=40),
    name: str = Form(..., min_length=1, max_length=80),
    topic: str = Form("", max_length=300),
    parent: str = Form(""),
    channel_kind: str = Form("forum"),
    flow_mode: str = Form("open"),
    visibility: str = Form("members"),
    principal: dict = Depends(current_principal),
):
    """Create a channel or sub-guild.

    A workspace is not a separate concept here — it is a channel with
    channel_kind='workspace', which is what makes "workspace connects into
    the guild" true rather than aspirational.
    """
    guild = await _guild(slug)
    membership = await _require_member(guild, principal)
    if membership["role"] not in _CHANNEL_ADMIN_ROLES:
        raise HTTPException(403, "Only guild staff can create channels")

    if not _SLUG_RE.match(channel_slug):
        raise HTTPException(422, "channel_slug must be lowercase letters, digits and dashes")
    if channel_kind not in coord.CHANNEL_KINDS:
        raise HTTPException(422, f"channel_kind must be one of {sorted(coord.CHANNEL_KINDS)}")
    if flow_mode not in coord.FLOW_MODES:
        raise HTTPException(422, f"flow_mode must be one of {sorted(coord.FLOW_MODES)}")
    if visibility not in coord.VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {sorted(coord.VISIBILITIES)}")

    parent_id = None
    if parent:
        parent_channel = await _channel_or_404(guild, parent)
        # Depth cap enforced here, not by convention: relaxing it later is a
        # migration, but a tree that has already grown three deep is a mess
        # to unwind.
        if parent_channel["parent_channel_id"] is not None:
            raise HTTPException(422, "Sub-guilds are one level deep — pick a top-level channel as the parent")
        parent_id = parent_channel["id"]

    if await coord.get_channel(guild["id"], channel_slug):
        raise HTTPException(409, "A channel with that slug already exists in this guild")

    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO guild_channels
                 (guild_id, parent_channel_id, slug, name, topic, channel_kind,
                  flow_mode, visibility, sandbox_bound, created_by_principal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (guild["id"], parent_id, channel_slug, name, topic, channel_kind,
             flow_mode, visibility, 1 if channel_kind == "workspace" else 0, principal["id"]),
        )
        channel_id = cur.lastrowid
        await db.commit()

    channel = await coord.get_channel_by_id(channel_id)
    buzz_channel_id = await coord.provision_channel_on_relay(channel, principal, slug)
    await _broadcast_gossip(f"guild.{slug}", {
        "type": "channel_created", "channel": channel_slug, "channel_kind": channel_kind,
    })
    return {
        "guild": slug, "channel": channel_slug, "id": channel_id,
        "channel_kind": channel_kind, "flow_mode": flow_mode, "visibility": visibility,
        "parent": parent or None,
        "relay_provisioned": bool(buzz_channel_id),
        "note": None if buzz_channel_id else
                "Channel created but not yet provisioned on the relay — posting will "
                "return 503 until POST /channels/{slug}/provision succeeds.",
    }


@router.post("/{slug}/channels/{channel_slug}/provision")
async def provision_channel(
    slug: str, channel_slug: str, principal: dict = Depends(current_principal)
):
    """Retry relay provisioning for a channel whose mirror failed at create
    time. Idempotent: a channel that already has a relay id is left alone."""
    guild = await _guild(slug)
    membership = await _require_member(guild, principal)
    if membership["role"] not in _CHANNEL_ADMIN_ROLES:
        raise HTTPException(403, "Only guild staff can provision channels")

    channel = await _channel_or_404(guild, channel_slug)
    if channel.get("buzz_channel_id"):
        return {"channel": channel_slug, "relay_provisioned": True, "changed": False}

    buzz_channel_id = await coord.provision_channel_on_relay(channel, principal, slug)
    if not buzz_channel_id:
        raise HTTPException(503, "Relay is unavailable — channel not provisioned")
    return {"channel": channel_slug, "relay_provisioned": True, "changed": True}


@router.patch("/{slug}/channels/{channel_slug}")
async def update_channel(
    slug: str,
    channel_slug: str,
    request: Request,
    principal: dict = Depends(current_principal),
):
    """Rename a channel or change its topic, flow mode or visibility."""
    guild = await _guild(slug)
    membership = await _require_member(guild, principal)
    if membership["role"] not in _CHANNEL_ADMIN_ROLES:
        raise HTTPException(403, "Only guild staff can edit channels")
    channel = await _channel_or_404(guild, channel_slug)

    from ..deps import _parse_body
    body = await _parse_body(request)
    updates: list[str] = []
    params: list = []
    if "name" in body:
        updates.append("name=?")
        params.append(str(body["name"])[:80])
    if "topic" in body:
        updates.append("topic=?")
        params.append(str(body["topic"])[:300])
    if "flow_mode" in body:
        if body["flow_mode"] not in coord.FLOW_MODES:
            raise HTTPException(422, f"flow_mode must be one of {sorted(coord.FLOW_MODES)}")
        updates.append("flow_mode=?")
        params.append(body["flow_mode"])
    if "visibility" in body:
        if body["visibility"] not in coord.VISIBILITIES:
            raise HTTPException(422, f"visibility must be one of {sorted(coord.VISIBILITIES)}")
        updates.append("visibility=?")
        params.append(body["visibility"])
    if not updates:
        raise HTTPException(422, "Nothing to update")

    params.append(channel["id"])
    async with get_db() as db:
        await db.execute(f"UPDATE guild_channels SET {', '.join(updates)} WHERE id=?", tuple(params))
        await db.commit()
    return {"channel": channel_slug, "updated": len(updates)}


# ── messages ─────────────────────────────────────────────────────────────────

@router.get("/{slug}/channels/{channel_slug}/messages")
async def list_channel_messages(
    slug: str,
    channel_slug: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[int] = Query(None),
    principal: Optional[dict] = Depends(optional_principal),
):
    """Top-level posts in a channel, newest first, each with a reply count."""
    guild = await _guild(slug)
    channel = await _channel_or_404(guild, channel_slug)
    await _require_readable(channel, principal)
    messages = await coord.list_messages(channel["id"], limit=limit, before_id=before_id)
    return {
        "guild": slug, "channel": channel_slug,
        "channel_kind": channel["channel_kind"], "flow_mode": channel["flow_mode"],
        "relay_provisioned": bool(channel.get("buzz_channel_id")),
        "messages": messages, "count": len(messages),
    }


@router.get("/{slug}/channels/{channel_slug}/threads/{root_event_id}")
async def read_thread(
    slug: str,
    channel_slug: str,
    root_event_id: str,
    limit: int = Query(200, ge=1, le=200),
    principal: Optional[dict] = Depends(optional_principal),
):
    """One thread: its root post plus every reply, oldest first."""
    guild = await _guild(slug)
    channel = await _channel_or_404(guild, channel_slug)
    await _require_readable(channel, principal)
    messages = await coord.list_messages(channel["id"], limit=limit, thread_root=root_event_id)
    messages.reverse()
    if not messages:
        raise HTTPException(404, "Thread not found")
    return {
        "guild": slug, "channel": channel_slug, "root_event_id": root_event_id,
        "messages": messages, "count": len(messages),
    }


@router.post("/{slug}/channels/{channel_slug}/messages")
@_limiter.limit("30/minute")
async def post_channel_message(
    request: Request,
    slug: str,
    channel_slug: str,
    content: str = Form(..., min_length=1, max_length=coord.MAX_CONTENT_CHARS),
    msg_type: str = Form("say"),
    reply_to: str = Form(""),
    addressed_to: str = Form(""),
    work_ref: str = Form(""),
    principal: dict = Depends(current_principal),
):
    """Post to a channel. Signs as the caller, publishes to the relay, and
    only then indexes.

    An external agent never needs this endpoint — it holds its own key and
    publishes straight to the relay, where the indexer picks the message up
    identically. This exists for principals whose keys Vantage derives.
    """
    guild = await _guild(slug)
    channel = await _channel_or_404(guild, channel_slug)
    await _require_member(guild, principal)
    await _require_readable(channel, principal)

    if msg_type not in coord.MSG_TYPES or msg_type == "system":
        raise HTTPException(422, "msg_type must be one of say, propose, claim, handoff, artifact")

    # Phase 0 ships `open` flow only. Rejecting rather than silently posting
    # keeps the contract honest until the Conductor exists to arbitrate.
    if channel["flow_mode"] != "open":
        raise HTTPException(
            409,
            f"This channel uses {channel['flow_mode']} flow, which needs the Conductor "
            "(Phase 2). Post in an open channel, or switch this one to open.",
        )

    root_event_id = None
    if reply_to:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT event_id, thread_root_event_id FROM channel_messages WHERE event_id=? AND channel_id=?",
                (reply_to, channel["id"]),
            )
            parent = await cur.fetchone()
        if not parent:
            raise HTTPException(404, "The message you're replying to isn't in this channel")
        parent = dict(parent)
        root_event_id = parent["thread_root_event_id"] or parent["event_id"]

    try:
        event = await coord.publish_message(
            channel=channel, guild_slug=slug, principal=principal, content=content,
            msg_type=msg_type, root_event_id=root_event_id,
            reply_to_event_id=reply_to or None,
            addressed_to=addressed_to or None, work_ref=work_ref or None,
        )
    except coord.RelayUnavailable as exc:
        # Same stance as routers/workspace.py's sandbox: no host-side
        # fallback. An unsigned message in the index that no relay subscriber
        # can see would be worse than an error.
        raise HTTPException(503, f"Relay unavailable — message not posted. {exc}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    await _broadcast_gossip(f"guild.{slug}", {
        "type": "channel_message", "channel": channel_slug,
        "event_id": event["id"], "author": principal["display_name"], "msg_type": msg_type,
    })
    return {
        "guild": slug, "channel": channel_slug, "event_id": event["id"],
        "msg_type": msg_type, "thread_root_event_id": root_event_id,
        "created_at": event["created_at"],
    }
