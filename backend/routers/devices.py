"""Agent device embodiment router.

Tier 3+ agents can register IoT/smart home/robotics device endpoints and
manage delegation of device control to other agents.

Endpoints:
  POST   /api/devices                                         — register a device
  GET    /api/devices                                         — list my devices
  GET    /api/devices/delegated                               — list devices I control
  GET    /api/devices/{device_id}                             — get device details
  DELETE /api/devices/{device_id}                             — deactivate device
  POST   /api/devices/{device_id}/delegate                    — request delegation
  POST   /api/devices/{device_id}/delegations/{id}/approve    — approve delegation
  POST   /api/devices/{device_id}/delegations/{id}/reject     — reject delegation
  DELETE /api/devices/{device_id}/delegations/{id}            — revoke delegation
"""
import json
import logging
import time
from typing import List, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import get_db
from ..deps import get_agent
from ..device_registry import DEVICE_TYPES, _decrypt_endpoint, _encrypt_endpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["devices"])


# ── Request bodies ────────────────────────────────────────────────────────────

class RegisterDeviceBody(BaseModel):
    name: str
    device_type: str = "generic"
    description: str = ""
    endpoint_url: Optional[str] = None
    capabilities: List[str] = []


class DelegateDeviceBody(BaseModel):
    delegate_agent_id: int
    permissions: List[str] = []
    expires_in_hours: Optional[int] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_agent_tier(agent_id: int) -> int:
    """Return the tier for agent_id from agent_tiers table. Defaults to 0 if no row."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tier FROM agent_tiers WHERE agent_id=?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
    return int(row["tier"]) if row else 0


async def _require_tier3(agent: dict) -> None:
    """Raise HTTP 403 if the calling agent is below tier 3."""
    tier = await _get_agent_tier(agent["id"])
    if tier < 3:
        raise HTTPException(
            status_code=403,
            detail=f"Device registration requires tier 3 or above (your tier: {tier})",
        )


async def _get_device(device_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_devices WHERE id=?", (device_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Device not found")
    return dict(row)


async def _get_delegation_row(delegation_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_delegations WHERE id=?", (delegation_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Delegation not found")
    return dict(row)


def _sanitize_device(device: dict, *, is_owner: bool) -> dict:
    """Strip endpoint_enc from non-owners."""
    out = {k: v for k, v in device.items() if k != "endpoint_enc"}
    if is_owner and device.get("endpoint_enc"):
        out["has_endpoint"] = True
    else:
        out["has_endpoint"] = bool(device.get("endpoint_enc"))
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/devices",
    summary="Register a device (requires tier 3+)",
    description=(
        "Tier 3+ agents can register a physical or IoT device endpoint. "
        "The endpoint URL is stored encrypted and never returned in plaintext."
    ),
)
async def register_device(
    body: RegisterDeviceBody,
    agent: dict = Depends(get_agent),
):
    await _require_tier3(agent)

    device_type = body.device_type if body.device_type in DEVICE_TYPES else "generic"
    caps_json = json.dumps(body.capabilities)
    endpoint_enc: Optional[str] = None
    if body.endpoint_url:
        endpoint_enc = _encrypt_endpoint(body.endpoint_url)

    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO agent_devices
               (agent_id, name, device_type, description, endpoint_enc, capabilities)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                agent["id"],
                body.name,
                device_type,
                body.description,
                endpoint_enc,
                caps_json,
            ),
        )
        await db.commit()
        device_id = cur.lastrowid

    device = await _get_device(device_id)
    return _sanitize_device(device, is_owner=True)


@router.get(
    "/devices",
    summary="List my registered devices",
)
async def list_my_devices(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_devices WHERE agent_id=? AND is_active=1 ORDER BY registered_at DESC",
            (agent["id"],),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"devices": [_sanitize_device(r, is_owner=True) for r in rows], "count": len(rows)}


@router.get(
    "/devices/delegated",
    summary="List devices I have approved delegation on",
    description="Returns devices where the calling agent has an 'approved' delegation status.",
)
async def list_delegated_devices(agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT d.*, dd.id as delegation_id, dd.permissions, dd.expires_at as delegation_expires_at
               FROM agent_devices d
               JOIN device_delegations dd ON dd.device_id = d.id
               WHERE dd.delegate_agent_id=? AND dd.status='approved' AND d.is_active=1
               ORDER BY dd.resolved_at DESC""",
            (agent["id"],),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    # Non-owners — strip endpoint
    return {"devices": [_sanitize_device(r, is_owner=False) for r in rows], "count": len(rows)}


@router.get(
    "/devices/{device_id}",
    summary="Get device details",
)
async def get_device(
    device_id: int,
    agent: dict = Depends(get_agent),
):
    device = await _get_device(device_id)
    if not device["is_active"]:
        raise HTTPException(404, "Device not found")
    is_owner = device["agent_id"] == agent["id"]
    return _sanitize_device(device, is_owner=is_owner)


@router.delete(
    "/devices/{device_id}",
    summary="Deactivate a device (soft delete)",
)
async def deactivate_device(
    device_id: int,
    agent: dict = Depends(get_agent),
):
    device = await _get_device(device_id)
    if device["agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the device owner can deactivate it")
    if not device["is_active"]:
        raise HTTPException(404, "Device not found")

    async with get_db() as db:
        await db.execute(
            "UPDATE agent_devices SET is_active=0, updated_at=unixepoch() WHERE id=?",
            (device_id,),
        )
        await db.commit()
    return {"status": "deactivated", "device_id": device_id}


@router.post(
    "/devices/{device_id}/delegate",
    summary="Request delegation of a device to another agent",
    description="Only the device owner can initiate a delegation request.",
)
async def request_delegation(
    device_id: int,
    body: DelegateDeviceBody,
    agent: dict = Depends(get_agent),
):
    device = await _get_device(device_id)
    if not device["is_active"]:
        raise HTTPException(404, "Device not found")
    if device["agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the device owner can initiate delegation")

    # Verify target agent exists
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM agents WHERE id=?", (body.delegate_agent_id,)) as cur:
            target = await cur.fetchone()
    if not target:
        raise HTTPException(404, f"Agent {body.delegate_agent_id} not found")

    expires_at: Optional[int] = None
    if body.expires_in_hours is not None:
        expires_at = int(time.time()) + body.expires_in_hours * 3600

    permissions_json = json.dumps(body.permissions)

    async with get_db() as db:
        try:
            cur = await db.execute(
                """INSERT INTO device_delegations
                   (device_id, delegator_agent_id, delegate_agent_id, permissions, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (device_id, agent["id"], body.delegate_agent_id, permissions_json, expires_at),
            )
            await db.commit()
            delegation_id = cur.lastrowid
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(
                    409,
                    "A delegation to this agent already exists for this device. "
                    "Revoke it first before creating a new one.",
                )
            raise

    return await _get_delegation_row(delegation_id)


@router.post(
    "/devices/{device_id}/delegations/{delegation_id}/approve",
    summary="Approve a pending delegation request",
)
async def approve_delegation(
    device_id: int,
    delegation_id: int,
    agent: dict = Depends(get_agent),
):
    device = await _get_device(device_id)
    if device["agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the device owner can approve delegations")

    delegation = await _get_delegation_row(delegation_id)
    if delegation["device_id"] != device_id:
        raise HTTPException(404, "Delegation not found for this device")
    if delegation["status"] != "pending":
        raise HTTPException(400, f"Delegation is not pending (current: {delegation['status']})")

    async with get_db() as db:
        await db.execute(
            """UPDATE device_delegations
               SET status='approved', resolved_at=unixepoch()
               WHERE id=?""",
            (delegation_id,),
        )
        await db.commit()

    return await _get_delegation_row(delegation_id)


@router.post(
    "/devices/{device_id}/delegations/{delegation_id}/reject",
    summary="Reject a pending delegation request",
)
async def reject_delegation(
    device_id: int,
    delegation_id: int,
    agent: dict = Depends(get_agent),
):
    device = await _get_device(device_id)
    if device["agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the device owner can reject delegations")

    delegation = await _get_delegation_row(delegation_id)
    if delegation["device_id"] != device_id:
        raise HTTPException(404, "Delegation not found for this device")
    if delegation["status"] != "pending":
        raise HTTPException(400, f"Delegation is not pending (current: {delegation['status']})")

    async with get_db() as db:
        await db.execute(
            """UPDATE device_delegations
               SET status='rejected', resolved_at=unixepoch()
               WHERE id=?""",
            (delegation_id,),
        )
        await db.commit()

    return await _get_delegation_row(delegation_id)


@router.delete(
    "/devices/{device_id}/delegations/{delegation_id}",
    summary="Revoke an approved delegation",
)
async def revoke_delegation(
    device_id: int,
    delegation_id: int,
    agent: dict = Depends(get_agent),
):
    device = await _get_device(device_id)
    if device["agent_id"] != agent["id"]:
        raise HTTPException(403, "Only the device owner can revoke delegations")

    delegation = await _get_delegation_row(delegation_id)
    if delegation["device_id"] != device_id:
        raise HTTPException(404, "Delegation not found for this device")
    if delegation["status"] not in ("approved", "pending"):
        raise HTTPException(400, f"Cannot revoke delegation in status '{delegation['status']}'")

    async with get_db() as db:
        await db.execute(
            """UPDATE device_delegations
               SET status='revoked', resolved_at=unixepoch()
               WHERE id=?""",
            (delegation_id,),
        )
        await db.commit()

    return {"status": "revoked", "delegation_id": delegation_id}
