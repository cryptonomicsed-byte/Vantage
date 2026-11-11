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

from ..deps import get_admin
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
