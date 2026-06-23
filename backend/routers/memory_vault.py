"""Memory vault API endpoints."""
import hashlib as _hlib
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
import aiosqlite

from ..config import settings
from ..deps import get_agent, _parse_body
from ..memory_enrichment import MemoryIntelligence
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
    hashed_key = _hlib.sha256(x_agent_key.encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE api_key=?", (hashed_key,)
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
    data = vault.get_galaxy_data()
    if settings.JULIA_MEMORY_URL:
        intel = MemoryIntelligence(settings.JULIA_MEMORY_URL)
        data["predictions"] = await intel.predict_next_activity(agent_name)
        data["patterns"] = await intel.mine_patterns(f"agent:{agent_name}")
    return data

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
    fts_results = [{"path": r[0], "title": r[1], "snippet": r[2]} for r in results]
    if settings.JULIA_MEMORY_URL:
        intel = MemoryIntelligence(settings.JULIA_MEMORY_URL)
        semantic = await intel.find_similar(q, top_k=20)
        fts_paths = {r["path"] for r in fts_results}
        for s in semantic:
            node_id = s.get("node_id", "")
            if node_id and node_id not in fts_paths:
                fts_results.append({
                    "path": node_id,
                    "title": node_id,
                    "snippet": f"semantic similarity: {s.get('similarity', 0):.3f}",
                })
    return {"query": q, "results": fts_results}

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


# ── Vault stats ───────────────────────────────────────────────────────────────

@router.get("/{agent_name}/vault/stats")
async def get_vault_stats(
    agent_name: str,
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403, "Access denied to this memory vault")
    return await vault.get_stats()


# ── Manual note creation ──────────────────────────────────────────────────────

_VALID_CATEGORIES = {"drafts", "templates", "broadcasts", "knowledge"}

@router.post("/{agent_name}/vault/note")
async def create_vault_note(
    agent_name: str,
    request: Request,
    agent: dict = Depends(get_agent),
):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only create notes in your own vault")

    body = await _parse_body(request)
    title: str = str(body.get("title", "")).strip()
    note_body: str = str(body.get("body", ""))
    category: str = str(body.get("category", "drafts")).strip()
    raw_tags = body.get("tags", [])

    if not title:
        raise HTTPException(422, "title is required")
    if category not in _VALID_CATEGORIES:
        raise HTTPException(422, f"category must be one of: {', '.join(sorted(_VALID_CATEGORIES))}")

    tags: list = list(raw_tags) if isinstance(raw_tags, list) else [t.strip() for t in str(raw_tags).split(",") if t.strip()]

    vault = MemoryVault(agent["id"], agent["name"])
    note_id = f"note_{uuid4().hex[:8]}"
    coords = vault._spatial_hash(title, category)

    frontmatter = {
        "id": note_id,
        "type": "star",
        "content_type": "text",
        "galaxy_x": coords[0],
        "galaxy_y": coords[1],
        "galaxy_z": coords[2],
        "galaxy_size": 8,
        "galaxy_color": "#ffe66d",
        "constellation": tags[0] if tags else category,
        "tags": tags,
        "created": datetime.utcnow().isoformat(),
    }

    safe_title = re.sub(r"[^\w-]", "_", title[:50])
    filename = f"{safe_title}.md"
    note_path = vault.vault_path / category / filename
    vault._write_note(note_path, frontmatter, note_body)
    relative_path = str(note_path.relative_to(vault.vault_path))
    await vault._update_fts(relative_path, title, note_body, tags)

    return {"path": relative_path, "id": note_id}


# ── Cross-agent memory links ──────────────────────────────────────────────────

@router.get("/{agent_name}/vault/links")
async def get_memory_links(agent_name: str):
    target = await _resolve_agent(agent_name)
    agent_id = target["id"]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT * FROM memory_links
               WHERE source_agent_id=? OR target_agent_id=?
               LIMIT 50""",
            (agent_id, agent_id),
        )).fetchall()
    return {"links": [dict(r) for r in rows]}


@router.post("/{agent_name}/vault/link")
async def create_memory_link(
    agent_name: str,
    request: Request,
    agent: dict = Depends(get_agent),
):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only create links from your own vault")

    body = await _parse_body(request)
    to_agent_name: str = str(body.get("to_agent_name", "")).strip()
    link_type: str = str(body.get("link_type", "knows")).strip() or "knows"
    source_note: str = str(body.get("note", "")).strip()

    if not to_agent_name:
        raise HTTPException(422, "to_agent_name is required")

    # Resolve target agent
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE name=?", (to_agent_name,)
        )).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{to_agent_name}' not found")
        to_agent_id: int = row[0]

        await db.execute(
            """INSERT INTO memory_links (source_agent_id, source_note_path, target_agent_id, target_note_path, link_type)
               VALUES (?, ?, ?, ?, ?)""",
            (agent["id"], source_note, to_agent_id, "", link_type),
        )
        await db.commit()

    return {"linked": True}
