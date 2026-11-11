"""Sentinel: named operators and the sign-off inbox (P0).

  /api/admin/sentinel/operators   bootstrap/manage operators with the instance
                                  admin key (until passkey + Nostr sign-in lands)
  /api/sentinel/me                who am I as an operator
  /api/sentinel/signoffs          the Human Input inbox (operators only)
  /api/sentinel/signoffs/{id}/approve | /deny
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_admin_bootstrap as get_admin
from ..sentinel import operators, signoffs

# Tagged "admin" so no agent tool surface (MCP, voice) ever exposes them:
# these are human-operator actions only.
router = APIRouter(tags=["admin", "sentinel"])


class OperatorUpsert(BaseModel):
    human_id: int
    role: str


class Decision(BaseModel):
    note: str = ""


@router.get("/api/admin/sentinel/operators")
async def list_operators(_: str = Depends(get_admin)):
    return {"operators": await operators.list_all()}


@router.post("/api/admin/sentinel/operators")
async def upsert_operator(data: OperatorUpsert, admin_key: str = Depends(get_admin)):
    import hashlib
    return await operators.upsert(data.human_id, data.role,
                                  added_by=f"admin_key:{hashlib.sha256(admin_key.encode()).hexdigest()[:12]}")


@router.delete("/api/admin/sentinel/operators/{human_id}")
async def revoke_operator(human_id: int, _: str = Depends(get_admin)):
    if not await operators.revoke(human_id):
        raise HTTPException(404, "No active operator with that human_id")
    return {"human_id": human_id, "revoked": True}


@router.get("/api/sentinel/me")
async def whoami(op: dict = Depends(operators.require_operator)):
    return {"human_id": op["id"], "display_name": op.get("display_name"), "role": op["operator_role"],
            "may_approve_trading": op["operator_role"] in operators.APPROVER_ROLES}


@router.get("/api/sentinel/signoffs")
async def list_signoffs(status: Optional[str] = "pending", op: dict = Depends(operators.require_operator)):
    if status and status not in signoffs.STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(signoffs.STATUSES)}")
    return {"cards": await signoffs.list_cards(status or None)}


@router.post("/api/sentinel/signoffs/{sid}/approve")
async def approve(sid: str, data: Decision = Decision(), op: dict = Depends(operators.require_operator)):
    return await signoffs.decide(sid=sid, approver=op, approve=True, note=data.note)


@router.post("/api/sentinel/signoffs/{sid}/deny")
async def deny(sid: str, data: Decision = Decision(), op: dict = Depends(operators.require_operator)):
    return await signoffs.decide(sid=sid, approver=op, approve=False, note=data.note)


# ── federation admission ─────────────────────────────────────────────────────

@router.get("/api/sentinel/federation/peers")
async def list_peers(admitted: Optional[bool] = None, op: dict = Depends(operators.require_operator)):
    import aiosqlite
    from ..db import get_db
    q = ("SELECT id, url, name, status, nostr_pubkey, discovered_via, COALESCE(admitted,0) AS admitted, "
         "admitted_by, admitted_at, COALESCE(flagged,0) AS flagged FROM federation_peers")
    args: tuple = ()
    if admitted is not None:
        q += " WHERE COALESCE(admitted,0)=?"
        args = (1 if admitted else 0,)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(q + " ORDER BY id", args)).fetchall()
    return {"peers": [dict(r) for r in rows]}


async def _set_admission(peer_id: int, admit: bool, op: dict) -> dict:
    from ..db import get_db
    if admit and op["operator_role"] not in operators.APPROVER_ROLES:
        raise HTTPException(403, "Admitting a peer needs an owner or security operator")
    async with get_db() as db:
        row = await (await db.execute("SELECT nostr_pubkey FROM federation_peers WHERE id=?", (peer_id,))).fetchone()
        if not row:
            raise HTTPException(404, "No such peer")
        if admit and not row[0]:
            raise HTTPException(422, "Peer has no pinned Nostr key yet; it cannot be authenticated")
        await db.execute(
            "UPDATE federation_peers SET admitted=?, admitted_by=?, admitted_at=datetime('now') WHERE id=?",
            (1 if admit else 0, f"operator:{op['id']}:{op['operator_role']}", peer_id))
        await db.commit()
    return {"peer_id": peer_id, "admitted": admit}


@router.post("/api/sentinel/federation/peers/{peer_id}/admit")
async def admit_peer(peer_id: int, op: dict = Depends(operators.require_operator)):
    return await _set_admission(peer_id, True, op)


@router.post("/api/sentinel/federation/peers/{peer_id}/revoke")
async def revoke_peer(peer_id: int, op: dict = Depends(operators.require_operator)):
    """Revoking is immediate for any operator: a brake never waits."""
    return await _set_admission(peer_id, False, op)
