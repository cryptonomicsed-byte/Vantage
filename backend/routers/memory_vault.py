"""Memory vault API endpoints."""
import io
import zipfile
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
import aiosqlite

from ..deps import get_agent
from ..memory_vault import MemoryVault, VAULT_ROOT
from ..db import DB_PATH

router = APIRouter(prefix="/api/agents", tags=["memory_vault"])

async def _resolve_agent(agent_name: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, name FROM agents WHERE name=?", (agent_name,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Agent not found")
        return dict(row)

async def _resolve_accessor(x_agent_key: Optional[str]) -> Optional[int]:
    if not x_agent_key:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE api_key=?", (x_agent_key,)
        )).fetchone()
        return row[0] if row else None

@router.post("/{agent_name}/vault/sync")
async def sync_vault(agent_name: str, agent: dict = Depends(get_agent)):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only sync your own vault")
    vault = MemoryVault(agent["id"], agent["name"])
    await vault.full_sync()
    return {"status": "synced"}

@router.get("/{agent_name}/vault/config")
async def get_vault_config(agent_name: str):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    config = await vault.get_config()
    return {
        "access": config.access,
        "federation_peers": config.federation_peers,
        "auto_export": config.auto_export,
        "last_synced": config.last_synced,
    }

@router.put("/{agent_name}/vault/config")
async def update_vault_config(
    agent_name: str,
    access: Literal["private", "followers", "federated", "public"],
    federation_peers: Optional[str] = None,
    agent: dict = Depends(get_agent),
):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only configure your own vault")
    peers = [p.strip() for p in (federation_peers or "").split("\n") if p.strip()]
    vault = MemoryVault(agent["id"], agent["name"])
    await vault.set_access(access, peers)
    return {"status": "updated", "access": access}

@router.get("/{agent_name}/vault/galaxy")
async def get_galaxy_data(
    agent_name: str,
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403, "Access denied to this memory vault")
    await vault.log_access(accessor_id, x_federation_peer or "", "galaxy", "read")
    return vault.get_galaxy_data()

@router.get("/{agent_name}/vault/search")
async def search_vault(
    agent_name: str,
    q: str = Query(..., min_length=1),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)
    async with aiosqlite.connect(DB_PATH) as db:
        results = await (await db.execute(
            """SELECT note_path, title, snippet(memory_fts, 3, '**', '**', '...', 30) as snip
               FROM memory_fts WHERE agent_id=? AND memory_fts MATCH ? ORDER BY rank LIMIT 20""",
            (target["id"], q)
        )).fetchall()
    return {"query": q, "results": [{"path": r[0], "title": r[1], "snippet": r[2]} for r in results]}

@router.get("/{agent_name}/vault/access-log")
async def get_access_log(
    agent_name: str,
    agent: dict = Depends(get_agent),
    limit: int = 50,
):
    if agent["name"] != agent_name:
        raise HTTPException(403)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM memory_access_log WHERE vault_agent_id=? ORDER BY accessed_at DESC LIMIT ?",
            (agent["id"], limit)
        )).fetchall()
    return {"access_log": [dict(r) for r in rows]}

@router.get("/{agent_name}/vault/download")
async def download_vault(
    agent_name: str,
    x_agent_key: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, ""):
        raise HTTPException(403)
    if not vault.vault_path.exists():
        raise HTTPException(404, "Vault not synced yet")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in vault.vault_path.rglob("*"):
            if fpath.is_file() and not str(fpath).endswith(".sqlite"):
                zf.write(fpath, fpath.relative_to(vault.vault_path))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-vault.zip"'},
    )

@router.get("/{agent_name}/vault/file/{path:path}")
async def get_vault_file(
    agent_name: str,
    path: str,
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)
    file_path = vault.vault_path / path
    try:
        file_path.resolve().relative_to(vault.vault_path.resolve())
    except ValueError:
        raise HTTPException(403, "Invalid path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "File not found")
    await vault.log_access(accessor_id, x_federation_peer or "", path, "read")
    return FileResponse(file_path, media_type="text/markdown")
