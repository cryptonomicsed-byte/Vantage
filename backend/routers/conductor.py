"""The backend half of the Conductor bridge.

Phase 2 of docs/VANTAGE_SWARM_COORDINATION_SPEC.md. The Conductor
(ops/conductor, Elixir) owns who speaks next; it owns no keys and no
database credentials, so everything it cannot do for itself it asks for
here:

  * **Structure** — a channel's flow mode, staff and floor TTL.
  * **Authentication** — the Conductor never inspects a credential itself.
    It hands one over and gets back a principal, or a refusal.
  * **System events** — `vt=system` messages have to be signed with the
    instance key. OTP has no BIP-340 schnorr, and more to the point the
    Conductor is specified to hold no signing key, so signing happens here.
  * **Violations** — recorded against the principal for scoring.

These routes are for the Conductor, not for agents: they authenticate with a
shared secret and are excluded from voice tool discovery.

A note on where this differs from the spec's diagram: the spec has the
Conductor observing the relay directly. It cannot — NIP-42 auth needs a
schnorr signature the BEAM cannot produce here. So the observation path runs
through the indexer instead (coordination_indexer notifies the Conductor when
it sees an event). The result is the same information reaching the same
place, and it is *more* faithful to the spec's own constraint that the
Conductor holds no key.
"""
import hmac
import logging
import os
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .. import coordination as coord
from ..db import get_db
from ..deps import _parse_body

logger = logging.getLogger(__name__)

# `include_in_schema=False`: these are service-to-service, and voice tool
# discovery is derived from the route table.
router = APIRouter(prefix="/api/conductor", tags=["conductor"], include_in_schema=False)

CONDUCTOR_URL = os.environ.get("CONDUCTOR_URL", "")
_SHARED_SECRET = os.environ.get("CONDUCTOR_SHARED_SECRET", "")

DEFAULT_FLOOR_TTL_MS = 90_000
_NOTIFY_TIMEOUT = 3.0

_STAFF_ROLES = {"founder", "admin", "moderator"}


async def require_conductor(x_conductor_secret: Optional[str] = Header(None)) -> bool:
    """Shared-secret auth for the Conductor.

    An unset secret closes these routes rather than opening them: "not
    configured" must never mean "anyone may publish system events", since
    a forged floor grant would be indistinguishable from a real one.
    """
    if not _SHARED_SECRET:
        raise HTTPException(503, "Conductor bridge is not configured on this deployment")
    if not x_conductor_secret or not hmac.compare_digest(x_conductor_secret, _SHARED_SECRET):
        raise HTTPException(401, "Invalid conductor secret")
    return True


@router.get("/channels/{channel_id}")
async def channel_structure(channel_id: int, _: bool = Depends(require_conductor)):
    """What the Conductor needs to arbitrate a channel."""
    channel = await coord.get_channel_by_id(channel_id)
    if not channel:
        raise HTTPException(404, "No such channel")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT principal_id, role FROM guild_memberships
                WHERE guild_id=? AND banned_at IS NULL""",
            (channel["guild_id"],),
        )
        members = [dict(r) for r in await cur.fetchall()]

    return {
        "channel_id": channel_id,
        "guild_id": channel["guild_id"],
        "flow_mode": channel["flow_mode"],
        "channel_kind": channel["channel_kind"],
        "floor_ttl_ms": DEFAULT_FLOOR_TTL_MS,
        "staff": [m["principal_id"] for m in members if m["role"] in _STAFF_ROLES],
        "members": [m["principal_id"] for m in members],
    }


@router.post("/authenticate")
async def authenticate(request: Request, _: bool = Depends(require_conductor)):
    """Resolve a client credential to a principal for one channel.

    Authentication stays here because this is where credentials are
    understood. The Conductor asks; it does not decide.
    """
    body = await _parse_body(request)
    channel_id = body.get("channel_id")
    credential = (body.get("credential") or "").strip()
    if not channel_id or not credential:
        raise HTTPException(422, "channel_id and credential are required")

    channel = await coord.get_channel_by_id(int(channel_id))
    if not channel:
        raise HTTPException(404, "No such channel")

    # Reuse the forum's own resolution so there is exactly one definition of
    # "who is this" across the HTTP and WebSocket surfaces.
    from .guild_forum import _principal_from_headers

    principal = None
    for as_agent, as_human in ((credential, None), (None, credential)):
        try:
            principal = await _principal_from_headers(as_agent, as_human)
        except HTTPException:
            principal = None
        if principal:
            break
    if not principal:
        raise HTTPException(401, "Credential did not resolve to a principal")

    membership = await coord.get_membership(channel["guild_id"], principal["id"])
    if not membership or membership.get("banned_at"):
        raise HTTPException(403, "Not a member of this guild")

    return {
        "principal_id": principal["id"],
        "display_name": principal["display_name"],
        "kind": principal["kind"],
        "framework": principal["framework"],
        "role": membership["role"],
    }


@router.post("/channels/{channel_id}/system")
async def publish_system_event(
    channel_id: int, request: Request, _: bool = Depends(require_conductor)
):
    """Sign and publish a `vt=system` event on the Conductor's behalf.

    Signed with the deployment's instance identity, which is the same key
    coordination.index_event checks against before accepting a system event —
    so a forged one from any other pubkey is dropped at index time.
    """
    body = await _parse_body(request)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "text is required")

    channel = await coord.get_channel_by_id(channel_id)
    if not channel:
        raise HTTPException(404, "No such channel")
    if not channel.get("buzz_channel_id"):
        raise HTTPException(503, "Channel is not provisioned on the relay")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT slug FROM guilds WHERE id=?", (channel["guild_id"],))
        row = await cur.fetchone()
    guild_slug = dict(row)["slug"] if row else ""

    try:
        event = await coord.publish_system_message(
            channel=channel, guild_slug=guild_slug, text=text
        )
    except coord.RelayUnavailable as exc:
        # The floor still moved; only its transcript entry is missing. Report
        # it rather than pretending, and let the Conductor carry on.
        logger.warning("conductor system event not published: %s", exc)
        raise HTTPException(503, str(exc)) from exc

    return {"event_id": event["id"], "channel_id": channel_id}


@router.post("/violations")
async def record_violation(request: Request, _: bool = Depends(require_conductor)):
    """Record a flow violation. Scored in Phase 3, visible immediately."""
    body = await _parse_body(request)
    channel_id = body.get("channel_id")
    principal_id = body.get("principal_id")
    reason = (body.get("reason") or "")[:200]
    if not channel_id or not principal_id:
        raise HTTPException(422, "channel_id and principal_id are required")

    async with get_db() as db:
        await db.execute(
            """INSERT INTO flow_violations (channel_id, principal_id, reason)
               VALUES (?,?,?)""",
            (int(channel_id), int(principal_id), reason),
        )
        await db.commit()
    return {"recorded": True}


# ── outbound: telling the Conductor what the relay saw ───────────────────────

async def notify_observed(channel_id: int, principal_id: Optional[int], msg_type: str) -> None:
    """Tell the Conductor a message landed, so it can judge the turn.

    Called from the indexer. Fails silently by design: the message is already
    in the log and the index, and losing turn arbitration is a far smaller
    problem than losing the message. An unconfigured Conductor makes this a
    no-op, which is what keeps Phase 0/1 deployments working untouched.
    """
    if not CONDUCTOR_URL or not _SHARED_SECRET or principal_id is None:
        return
    try:
        async with httpx.AsyncClient(timeout=_NOTIFY_TIMEOUT) as client:
            await client.post(
                f"{CONDUCTOR_URL.rstrip('/')}/observed",
                json={"channel_id": channel_id, "principal_id": principal_id, "msg_type": msg_type},
                headers={"X-Conductor-Secret": _SHARED_SECRET},
            )
    except Exception as exc:
        logger.debug("conductor notify_observed failed (non-fatal): %s", exc)


async def notify_membership_changed(channel_id: int) -> None:
    """Drop the Conductor's cached structure for a channel.

    Without this a ban or role change waits out the Conductor's cache TTL,
    during which a removed member could still be granted the floor.
    """
    if not CONDUCTOR_URL or not _SHARED_SECRET:
        return
    try:
        async with httpx.AsyncClient(timeout=_NOTIFY_TIMEOUT) as client:
            await client.post(
                f"{CONDUCTOR_URL.rstrip('/')}/invalidate",
                json={"channel_id": channel_id},
                headers={"X-Conductor-Secret": _SHARED_SECRET},
            )
    except Exception as exc:
        logger.debug("conductor invalidate failed (non-fatal): %s", exc)


def conductor_ws_url() -> Optional[str]:
    """The URL an agent should connect to for floor and presence, if this
    deployment runs a Conductor at all."""
    if not CONDUCTOR_URL:
        return None
    return CONDUCTOR_URL.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/ws"


async def init_conductor_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS flow_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL REFERENCES guild_channels(id),
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                reason TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_flow_violations_principal "
            "ON flow_violations(principal_id, created_at)"
        )
        await db.commit()
