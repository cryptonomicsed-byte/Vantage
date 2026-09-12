"""Vantage VCP Device Registry — manages physical devices available for agent inhabitation.

Mirrors the vcp-broker's DeviceRegistry but persisted in SQLite so Vantage can
cross-reference devices with agents, guilds, and the twin pipeline.

Routes:
  POST /api/vcp/devices/register   — device submits DeviceManifest
  POST /api/vcp/devices/:id/heartbeat — device refreshes presence
  GET  /api/vcp/devices            — list fresh devices
  GET  /api/vcp/devices/:id        — get device manifest
  DELETE /api/vcp/devices/:id      — device owner deregisters

  POST /api/vcp/sessions           — agent requests session (proxied to vcp-broker)
  GET  /api/vcp/sessions/:id/receipt — retrieve completed session receipt
"""
from __future__ import annotations

import json as _json
import os
import time
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..deps import get_agent

router = APIRouter(prefix="/api/vcp", tags=["vcp"])

STALE_SECS = 300   # 5 min
VCP_BROKER_URL = os.getenv("VCP_BROKER_URL", "")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vcp_devices (
    device_id     TEXT PRIMARY KEY,
    owner_did     TEXT NOT NULL,
    label         TEXT,
    device_class  TEXT,
    safety_class  TEXT,
    manifest_json TEXT NOT NULL,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    vantage_name  TEXT
);
CREATE TABLE IF NOT EXISTS vcp_sessions (
    session_id    TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL,
    agent_did     TEXT NOT NULL,
    grant_id      TEXT,
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at      TEXT,
    outcome       TEXT,
    receipt_hash  TEXT,
    receipt_json  TEXT
);
"""

_table_ready = False

async def _ensure_tables():
    global _table_ready
    if _table_ready:
        return
    async with get_db() as db:
        for stmt in _CREATE_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await db.execute(s)
        await db.commit()
    _table_ready = True


# ── device registry ────────────────────────────────────────────────────────────

@router.post("/devices/register")
async def register_device(request: Request, agent: dict = Depends(get_agent)):
    await _ensure_tables()
    body = await request.json()

    device_id    = str(body.get("device_id", "")).strip()
    owner_did    = str(body.get("owner_did", agent["name"])).strip()
    label        = str(body.get("label", "")).strip()
    device_class = str(body.get("class", "other")).strip()
    safety_class = str(body.get("safety_class", "Observer")).strip()

    if not device_id:
        raise HTTPException(422, "device_id required")

    # Ownership: device owner must match authenticated agent OR be delegated.
    if owner_did != agent["name"]:
        raise HTTPException(403, "owner_did must match authenticated agent")

    async with get_db() as db:
        await db.execute(
            """INSERT INTO vcp_devices
                 (device_id, owner_did, label, device_class, safety_class,
                  manifest_json, vantage_name, last_seen_at)
               VALUES (?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(device_id) DO UPDATE SET
                 manifest_json = excluded.manifest_json,
                 last_seen_at  = datetime('now'),
                 label         = excluded.label""",
            (device_id, owner_did, label, device_class, safety_class,
             _json.dumps(body), agent["name"]),
        )
        await db.commit()

    return {"ok": True, "device_id": device_id}


@router.post("/devices/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str, request: Request, agent: dict = Depends(get_agent)
):
    await _ensure_tables()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT owner_did FROM vcp_devices WHERE device_id=?", (device_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "device not found")
        if row[0] != agent["name"]:
            raise HTTPException(403, "only the device owner may heartbeat")
        await db.execute(
            "UPDATE vcp_devices SET last_seen_at=datetime('now') WHERE device_id=?",
            (device_id,),
        )
        await db.commit()
    return {"ok": True}


@router.get("/devices")
async def list_devices(agent: dict = Depends(get_agent)):
    await _ensure_tables()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT device_id, owner_did, label, device_class, safety_class,
                      last_seen_at
               FROM vcp_devices
               WHERE datetime(last_seen_at) > datetime('now', ?)
               ORDER BY last_seen_at DESC""",
            (f"-{STALE_SECS} seconds",),
        )).fetchall()
    return {"devices": [dict(r) for r in rows]}


@router.get("/devices/{device_id}")
async def get_device(device_id: str, agent: dict = Depends(get_agent)):
    await _ensure_tables()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM vcp_devices WHERE device_id=?", (device_id,)
        )).fetchone()
    if not row:
        raise HTTPException(404, "device not found")
    result = dict(row)
    result["manifest"] = _json.loads(result.pop("manifest_json", "{}"))
    return result


@router.delete("/devices/{device_id}")
async def deregister_device(device_id: str, agent: dict = Depends(get_agent)):
    await _ensure_tables()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT owner_did FROM vcp_devices WHERE device_id=?", (device_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "device not found")
        if row[0] != agent["name"]:
            raise HTTPException(403, "only the device owner may deregister")
        await db.execute("DELETE FROM vcp_devices WHERE device_id=?", (device_id,))
        await db.commit()
    return {"ok": True, "deleted": device_id}


# ── session receipts ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/receipt")
async def store_session_receipt(
    session_id: str, request: Request, agent: dict = Depends(get_agent)
):
    """vcp-broker POSTs the completed VcpReceipt here for durable storage."""
    await _ensure_tables()
    body = await request.json()
    receipt_hash = str(body.get("receipt_hash", ""))
    receipt_json = _json.dumps(body.get("receipt", body))

    async with get_db() as db:
        await db.execute(
            """INSERT INTO vcp_sessions (session_id, device_id, agent_did, receipt_hash, receipt_json, ended_at, outcome)
               VALUES (?,?,?,?,?,datetime('now'),?)
               ON CONFLICT(session_id) DO UPDATE SET
                 receipt_hash = excluded.receipt_hash,
                 receipt_json = excluded.receipt_json,
                 ended_at     = excluded.ended_at,
                 outcome      = excluded.outcome""",
            (session_id,
             str(body.get("receipt", {}).get("device_id", "")),
             agent["name"],
             receipt_hash, receipt_json,
             str(body.get("receipt", {}).get("outcome", "unknown"))),
        )
        await db.commit()
    return {"ok": True, "session_id": session_id}


@router.get("/sessions/{session_id}/receipt")
async def get_session_receipt(session_id: str, agent: dict = Depends(get_agent)):
    await _ensure_tables()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM vcp_sessions WHERE session_id=?", (session_id,)
        )).fetchone()
    if not row:
        raise HTTPException(404, "session not found")
    result = dict(row)
    if result.get("receipt_json"):
        result["receipt"] = _json.loads(result.pop("receipt_json"))
    return result
