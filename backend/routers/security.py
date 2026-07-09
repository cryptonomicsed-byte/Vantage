"""Security scan lookup + ingest — the parrot-security upload gate
(backend/utils.py::_security_scan_and_normalize) plus external scanner results
(SSTImap/XSStrike web scans, atomic-red-team emulation) posted by the VPS
security bridges. All land in the security_scans table, which the ARES SENTINEL
"Security Scans" tab reads via /api/admin/security-scans.
"""

import json as _json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import DB_PATH
from ..deps import get_agent

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM security_scans WHERE id=? AND agent_id=?",
            (scan_id, agent["id"]),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Scan not found")
    result = dict(row)
    result["findings"] = _json.loads(result.pop("findings_json"))
    return result


@router.post("/scan-result")
async def ingest_scan_result(request: Request, agent: dict = Depends(get_agent)):
    """Record an external scanner result (SSTImap, XSStrike, atomic-red-team, …)
    so it surfaces in the SENTINEL Security Scans tab as a structured record
    instead of a plain feed post.

    Body: {tool, target, status?, vulnerable?, findings?[]}
      - tool   → artifact_type (e.g. "sstimap", "xsstrike", "atomic")
      - target → artifact_ref (URL / repo / technique id)
      - status → 'clean' | 'vulnerable' | 'flagged' (or derived from `vulnerable`)
      - findings → list of strings or objects
    """
    body = await request.json()
    tool = str(body.get("tool", "scan")).strip().lower() or "scan"
    target = str(body.get("target", "")).strip()
    findings = body.get("findings") or []
    if not isinstance(findings, list):
        findings = [findings]
    status = body.get("status")
    if not status:
        status = "vulnerable" if body.get("vulnerable") else "clean"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO security_scans
               (agent_id, artifact_type, artifact_ref, status, normalized, findings_json, completed_at)
               VALUES (?, ?, ?, ?, 0, ?, datetime('now'))""",
            (agent["id"], tool, target, str(status), _json.dumps(findings)),
        )
        await db.commit()
        scan_id = cur.lastrowid
    return {"scan_id": scan_id, "tool": tool, "target": target, "status": status, "findings": len(findings)}
