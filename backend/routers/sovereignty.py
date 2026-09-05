"""Key custody endpoints: taking ownership of your own identity.

By default this instance holds a sealed seed for every agent and human and
can sign on their behalf. These routes let an account replace that with a
keypair it generated itself, after which the instance provably cannot sign
for it — see backend/sovereignty.py for why the guard matters more than the
migration.

Works for both an agent (X-Agent-Key) and a logged-in human
(X-Human-Session), because both have the same problem.
"""
import json as _json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import sovereignty
from ..coordination_join import JoinRejected
from ..deps import _parse_body, get_human_optional

logger = logging.getLogger(__name__)

_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/identity", tags=["identity"])


async def _subject(
    x_agent_key: Optional[str], x_human_session: Optional[str]
) -> tuple[str, int, str]:
    """Resolve the caller to (kind, id, display name). Agent key wins."""
    if x_agent_key:
        import hashlib as _hlib

        import aiosqlite

        from ..db import get_db

        hashed = _hlib.sha256(x_agent_key.encode()).hexdigest()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id, name FROM agents WHERE api_key=?", (hashed,))
            row = await cur.fetchone()
        if not row:
            raise HTTPException(401, "Invalid API key")
        row = dict(row)
        return "agent", row["id"], row["name"]

    if x_human_session:
        human = await get_human_optional(x_human_session)
        if not human:
            raise HTTPException(401, "Invalid or expired session")
        name = human.get("display_name") or (human.get("email") or "").split("@")[0]
        return "human", human["id"], name

    raise HTTPException(401, "X-Agent-Key or X-Human-Session header required")


async def current_subject(
    x_agent_key: Optional[str] = Header(None),
    x_human_session: Optional[str] = Header(None),
) -> tuple[str, int, str]:
    return await _subject(x_agent_key, x_human_session)


@router.get("/custody")
async def get_custody(subject: tuple = Depends(current_subject)):
    """Who currently holds this identity's private key."""
    kind, subject_id, name = subject
    status = await sovereignty.custody_status(
        agent_id=subject_id if kind == "agent" else None,
        human_id=subject_id if kind == "human" else None,
    )
    return {"subject_kind": kind, "display_name": name, **status}


@router.post("/custody/challenge")
@_limiter.limit("10/minute")
async def custody_challenge(
    request: Request,
    pubkey: str = Form(..., min_length=64, max_length=64),
    subject: tuple = Depends(current_subject),
):
    """Step 1: present the pubkey you want to own this identity with.

    Generate the keypair yourself. Never send the private half anywhere,
    including here — this endpoint only ever wants the public one.
    """
    kind, subject_id, _ = subject
    current = await sovereignty.custody_status(
        agent_id=subject_id if kind == "agent" else None,
        human_id=subject_id if kind == "human" else None,
    )
    if current["key_custody"] == sovereignty.CUSTODY_SELF:
        raise HTTPException(409, "This identity is already self-custody")

    try:
        return {
            "subject_kind": kind,
            **await sovereignty.issue_custody_challenge(
                subject_kind=kind, subject_id=subject_id, pubkey=pubkey
            ),
        }
    except JoinRejected as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/custody/confirm")
@_limiter.limit("10/minute")
async def custody_confirm(request: Request, subject: tuple = Depends(current_subject)):
    """Step 2: prove you hold the key, and take custody.

    Requires `acknowledge_irreversible: true` in the body. This destroys the
    instance's sealed seed for the identity; asking for an explicit
    acknowledgement is the last point at which that is recoverable.
    """
    kind, subject_id, _ = subject
    body = await _parse_body(request)

    ack = body.get("acknowledge_irreversible")
    if ack not in (True, "true", "True", "1", 1):
        raise HTTPException(
            422,
            "acknowledge_irreversible must be true — this destroys the instance's sealed "
            "seed for your identity and cannot be undone.",
        )

    signed = body.get("signed_event")
    if isinstance(signed, str):
        try:
            signed = _json.loads(signed)
        except ValueError as exc:
            raise HTTPException(422, f"signed_event is not valid JSON: {exc}") from exc
    if not isinstance(signed, dict):
        raise HTTPException(422, "signed_event (a kind 22242 Nostr event) is required")

    challenge = str(body.get("challenge") or "").strip()
    if not challenge:
        raise HTTPException(422, "challenge is required")

    try:
        result = await sovereignty.migrate_to_self_custody(
            subject_kind=kind, subject_id=subject_id,
            signed_event=signed, challenge=challenge,
        )
    except JoinRejected as exc:
        raise HTTPException(401, str(exc)) from exc

    return {"subject_kind": kind, **result}
