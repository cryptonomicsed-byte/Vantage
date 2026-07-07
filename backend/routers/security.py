"""Security scan lookup — read-only visibility into the parrot-security gate
that runs on every upload (backend/utils.py::_security_scan_and_normalize).
"""

import json as _json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

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
