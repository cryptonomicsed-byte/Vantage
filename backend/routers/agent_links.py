"""The bridge between a human account and an agent: scoped, revocable grants.
This is deliberately the ONLY way a human's session can act through an agent
-- an agent's own X-Agent-Key remains fully sovereign and untouched by any of
this. A human never gets implicit access; every grant is explicit and scoped."""
import hashlib as _hlib
import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import _parse_body, get_human

router = APIRouter(prefix="/api/humans/me/agents", tags=["humans"])

STARTER_SCOPES = ["copilot_chat", "view_state"]
VALID_SCOPES = {"view_state", "copilot_chat", "trading_execute", "wallet_manage", "admin_full"}


@router.get("")
async def list_my_agents(human: dict = Depends(get_human)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT a.id AS agent_id, a.name, a.avatar_url, g.scopes, g.granted_by, g.created_at
               FROM agent_grants g JOIN agents a ON a.id = g.agent_id
               WHERE g.human_id = ? AND g.revoked_at IS NULL
               ORDER BY g.created_at DESC""",
            (human["id"],),
        )).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["scopes"] = json.loads(d["scopes"])
        except Exception:
            d["scopes"] = []
        out.append(d)
    return out


@router.post("/link")
async def link_agent(request: Request, human: dict = Depends(get_human)):
    """Link an existing agent by presenting its raw X-Agent-Key once, proving
    ownership -- the raw key is never stored, only hashed-and-compared exactly
    like get_agent() does. Creates the same narrow starter grant as birth."""
    body = await _parse_body(request)
    agent_key = str(body.get("agent_key", "")).strip()
    if not agent_key:
        raise HTTPException(422, "agent_key is required")

    key_hash = _hlib.sha256(agent_key.encode()).hexdigest()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        agent_row = await (await db.execute(
            "SELECT id, name FROM agents WHERE api_key = ?", (key_hash,)
        )).fetchone()
    if not agent_row:
        raise HTTPException(401, "Invalid agent key")

    try:
        async with get_db() as db:
            await db.execute(
                """INSERT INTO agent_grants (human_id, agent_id, scopes, granted_by)
                   VALUES (?, ?, ?, 'link')""",
                (human["id"], agent_row["id"], json.dumps(STARTER_SCOPES)),
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(409, "This agent is already linked to your account")

    return {"agent_id": agent_row["id"], "name": agent_row["name"], "scopes": STARTER_SCOPES}


@router.post("/{agent_id}/grants")
async def update_grant(agent_id: int, request: Request, human: dict = Depends(get_human)):
    """Re-scope a grant. Caller must already hold SOME grant on this agent
    (self-service, can only ever narrow/rearrange what they already had) OR
    present the agent's own X-Agent-Key directly (the agent explicitly
    expanding its human's access) -- that second path is the only way to
    reach 'trading_execute'/'wallet_manage'/'admin_full'."""
    body = await _parse_body(request)
    scopes = body.get("scopes", [])
    if not isinstance(scopes, list) or not all(s in VALID_SCOPES for s in scopes):
        raise HTTPException(422, f"scopes must be a subset of {sorted(VALID_SCOPES)}")

    x_agent_key = request.headers.get("x-agent-key", "")
    is_agent_itself = False
    if x_agent_key:
        key_hash = _hlib.sha256(x_agent_key.encode()).hexdigest()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            agent_row = await (await db.execute(
                "SELECT id FROM agents WHERE api_key = ? AND id = ?", (key_hash, agent_id)
            )).fetchone()
        is_agent_itself = agent_row is not None

    if not is_agent_itself:
        # Self-service path: human may only narrow/rearrange scopes they
        # already hold -- never escalate to something they didn't have.
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            existing = await (await db.execute(
                "SELECT scopes FROM agent_grants WHERE human_id=? AND agent_id=? AND revoked_at IS NULL",
                (human["id"], agent_id),
            )).fetchone()
        if not existing:
            raise HTTPException(403, "No existing grant to modify -- link the agent first")
        existing_scopes = set(json.loads(existing["scopes"]))
        if not set(scopes).issubset(existing_scopes):
            raise HTTPException(
                403,
                "Cannot self-escalate scopes -- only the agent itself (X-Agent-Key) can grant new scopes",
            )

    granted_by = "agent_explicit" if is_agent_itself else "human_self_narrow"
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE agent_grants SET scopes=?, granted_by=? WHERE human_id=? AND agent_id=? AND revoked_at IS NULL",
            (json.dumps(scopes), granted_by, human["id"], agent_id),
        )
        if cur.rowcount == 0:
            await db.execute(
                """INSERT INTO agent_grants (human_id, agent_id, scopes, granted_by)
                   VALUES (?, ?, ?, ?)""",
                (human["id"], agent_id, json.dumps(scopes), granted_by),
            )
        await db.commit()

    return {"agent_id": agent_id, "scopes": scopes, "granted_by": granted_by}


@router.delete("/{agent_id}/grants")
async def revoke_grant(agent_id: int, human: dict = Depends(get_human)):
    async with get_db() as db:
        await db.execute(
            "UPDATE agent_grants SET revoked_at=datetime('now') WHERE human_id=? AND agent_id=? AND revoked_at IS NULL",
            (human["id"], agent_id),
        )
        await db.commit()
    return {"status": "revoked", "agent_id": agent_id}
