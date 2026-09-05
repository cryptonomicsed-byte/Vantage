"""Artifact management endpoints."""
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException

from ..db import get_db
from ..deps import get_agent

router = APIRouter(
    prefix="/api/guilds/{slug}/workspaces/{ws_id}/artifacts",
    tags=["artifacts"],
)


@router.post("")
async def create_artifact(
    slug: str,
    ws_id: str,
    task_id: str = Form(...),
    kind: str = Form("other"),
    title: str = Form(...),
    content_text: str = Form(""),
    content_uri: str = Form(""),
    content_hash: str = Form(""),
    agent: dict = Depends(get_agent),
):
    """Create an artifact for a task."""
    artifact_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            """INSERT INTO artifacts
               (id, task_id, agent_id, workspace_id, kind, title, content_text,
                content_uri, content_hash, created_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (artifact_id, task_id, agent["id"], ws_id, kind, title,
             content_text, content_uri, content_hash, now, now),
        )
        await db.commit()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)) as cur:
            row = await cur.fetchone()
    return dict(row)


@router.get("/{artifact_id}")
async def get_artifact(
    slug: str,
    ws_id: str,
    artifact_id: str,
    agent: dict = Depends(get_agent),
):
    """Get a single artifact by ID."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM artifacts WHERE id=? AND workspace_id=?",
            (artifact_id, ws_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Artifact not found")
    return dict(row)


@router.post("/{artifact_id}/receipt")
async def attach_receipt(
    slug: str,
    ws_id: str,
    artifact_id: str,
    receipt_body: str = Form(...),
    omokoda_receipt_id: Optional[str] = Form(None),
    kernel_pubkey: str = Form(""),
    agent: dict = Depends(get_agent),
):
    """Attach an execution receipt to an artifact. Insert-only."""
    # Verify artifact exists in this workspace
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT task_id FROM artifacts WHERE id=? AND workspace_id=?",
            (artifact_id, ws_id),
        ) as cur:
            art_row = await cur.fetchone()
    if not art_row:
        raise HTTPException(404, "Artifact not found")
    task_id = art_row["task_id"]

    receipt_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO execution_receipts
                   (id, artifact_id, task_id, agent_id, omokoda_receipt_id,
                    kernel_pubkey, receipt_body, created_ts)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (receipt_id, artifact_id, task_id, agent["id"],
                 omokoda_receipt_id, kernel_pubkey, receipt_body, now),
            )
            await db.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, "Receipt with this omokoda_receipt_id already exists")
            raise

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM execution_receipts WHERE id=?", (receipt_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row)
