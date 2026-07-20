"""Memory vault API endpoints."""
import hashlib as _hlib
import io
import json
import re
import secrets
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
import aiosqlite

from ..config import settings
from ..deps import get_agent, get_vault_connector, _parse_body
from ..memory_enrichment import MemoryIntelligence
from ..memory_vault import MemoryVault, VAULT_ROOT, OKF_VERSION, OKF_RESERVED_FILENAMES
from ..db import DB_PATH

router = APIRouter(prefix="/api/agents", tags=["memory_vault"])

# Ingest lives on its own top-level prefix rather than under
# /api/agents/{agent_name}/... — a connector token, not the URL, determines
# which agent's vault a push lands in, so there's no {agent_name} to key on.
external_router = APIRouter(prefix="/api/vault/external", tags=["memory_vault"])

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
async def get_vault_config(agent_name: str, agent: dict = Depends(get_agent)):
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
    x_federation_peer: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403, "Access denied to this memory vault")
    await vault.log_access(accessor_id, x_federation_peer or "", "galaxy", "read")
    data = vault.get_galaxy_data()
    if hasattr(settings, 'JULIA_MEMORY_URL') and settings.JULIA_MEMORY_URL:
        intel = MemoryIntelligence(settings.JULIA_MEMORY_URL)
        data["predictions"] = await intel.predict_next_activity(agent_name)
        data["patterns"] = await intel.mine_patterns(f"agent:{agent_name}")
    return data

@router.get("/{agent_name}/vault/search")
async def search_vault(
    agent_name: str,
    q: str = Query(..., min_length=1),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
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
    if getattr(settings, "JULIA_MEMORY_URL", ""):
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
    x_agent_key: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
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

_EXCLUDED_EXPORT_FILES = OKF_RESERVED_FILENAMES | {"README.md", "SOUL.md"}


@router.get("/{agent_name}/vault/export")
async def export_vault_universal(
    agent_name: str,
    format: Literal["universal"] = Query("universal"),
    x_agent_key: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
    """Portable JSON export — round-trips through POST /vault/import."""
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, ""):
        raise HTTPException(403)
    if not vault.vault_path.exists():
        raise HTTPException(404, "Vault not synced yet")

    nodes = []
    for md_file in sorted(vault.vault_path.rglob("*.md")):
        if md_file.name in _EXCLUDED_EXPORT_FILES or md_file.parent.name == "workspace":
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = vault._parse_frontmatter(content)
        if not fm.get("type"):
            continue
        parts = content.split("---", 2)
        body = parts[2].lstrip("\n") if len(parts) >= 3 else content
        nodes.append({"path": str(md_file.relative_to(vault.vault_path)), "frontmatter": fm, "body": body})

    payload = {
        "okf_version": OKF_VERSION,
        "agent_name": target["name"],
        "exported_at": datetime.utcnow().isoformat(),
        "nodes": nodes,
    }
    buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
    return StreamingResponse(
        buf, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-vault-universal.json"'},
    )


@router.post("/{agent_name}/vault/import")
async def import_vault(
    agent_name: str,
    file: UploadFile = File(...),
    agent: dict = Depends(get_agent),
):
    """Import notes from a Universal JSON export (from /vault/export) or an
    Obsidian-style ZIP (from /vault/download)."""
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only import into your own vault")

    raw = await file.read()
    filename = (file.filename or "").lower()
    vault = MemoryVault(agent["id"], agent["name"])
    imported_nodes = 0
    imported_links = 0

    def _within_vault(dest: Path) -> bool:
        try:
            dest.resolve().relative_to(vault.vault_path.resolve())
            return True
        except ValueError:
            return False

    if filename.endswith(".json"):
        try:
            payload = json.loads(raw)
        except Exception:
            raise HTTPException(422, "Invalid JSON")
        nodes = payload.get("nodes", [])
        if not isinstance(nodes, list):
            raise HTTPException(422, "'nodes' must be a list")
        for node in nodes[:2000]:
            if not isinstance(node, dict):
                continue
            rel_path = str(node.get("path", "")).strip()
            fm = node.get("frontmatter")
            body = str(node.get("body", ""))
            if not rel_path or not isinstance(fm, dict) or not fm.get("type"):
                continue
            dest = vault.vault_path / rel_path
            if dest.name in OKF_RESERVED_FILENAMES or not _within_vault(dest):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            vault._write_note(dest, fm, body)
            tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
            await vault._update_fts(rel_path, str(fm.get("title", rel_path)), body, tags)
            imported_nodes += 1

        links = payload.get("links", [])
        if isinstance(links, list):
            async with aiosqlite.connect(DB_PATH) as db:
                for link in links[:500]:
                    if not isinstance(link, dict):
                        continue
                    to_name = str(link.get("to_agent_name", "")).strip()
                    if not to_name:
                        continue
                    row = await (await db.execute("SELECT id FROM agents WHERE name=?", (to_name,))).fetchone()
                    if not row:
                        continue
                    await db.execute(
                        """INSERT INTO memory_links
                           (source_agent_id, source_note_path, target_agent_id, target_note_path, link_type)
                           VALUES (?,?,?,?,?)""",
                        (agent["id"], str(link.get("note", "")), row[0], "", str(link.get("link_type", "knows"))),
                    )
                    imported_links += 1
                await db.commit()

    elif filename.endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except Exception:
            raise HTTPException(422, "Invalid ZIP")
        for info in zf.infolist():
            if info.is_dir() or not info.filename.endswith(".md"):
                continue
            dest = vault.vault_path / info.filename
            if dest.name in OKF_RESERVED_FILENAMES or not _within_vault(dest):
                continue
            try:
                content = zf.read(info).decode("utf-8", errors="ignore")
            except Exception:
                continue
            fm = vault._parse_frontmatter(content)
            if not fm.get("type"):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            parts = content.split("---", 2)
            body = parts[2].lstrip("\n") if len(parts) >= 3 else content
            rel_path = str(dest.relative_to(vault.vault_path))
            tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
            await vault._update_fts(rel_path, str(fm.get("title", rel_path)), body, tags)
            imported_nodes += 1
    else:
        raise HTTPException(422, "Only .json or .zip files are accepted")

    return {"imported_nodes": imported_nodes, "imported_links": imported_links}


def _ttl_slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s.strip()).strip("_") or "unknown"


def _ttl_literal(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


@router.get("/{agent_name}/vault/graph.ttl")
async def export_vault_ttl(
    agent_name: str,
    x_agent_key: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
    """RDF/Turtle export of the vault's knowledge — Knowledge Triples become
    real subject/predicate/object statements; every other concept gets a
    label + type statement so the whole vault is one linked-data graph."""
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, ""):
        raise HTTPException(403)
    if not vault.vault_path.exists():
        raise HTTPException(404, "Vault not synced yet")

    agent_slug = _ttl_slug(agent_name)
    lines = [
        "@prefix vantage: <https://vantage.local/ontology#> .",
        f"@prefix agent: <https://vantage.local/agent/{agent_slug}/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "",
    ]

    knowledge_dir = vault.vault_path / "knowledge"
    if knowledge_dir.exists():
        for md_file in sorted(knowledge_dir.glob("*.md")):
            if md_file.name in OKF_RESERVED_FILENAMES:
                continue
            try:
                fm = vault._parse_frontmatter(md_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            subject, predicate, obj = fm.get("subject"), fm.get("predicate"), fm.get("object")
            if not (subject and predicate and obj):
                continue
            lines.append(
                f'agent:{_ttl_slug(str(subject))} vantage:{_ttl_slug(str(predicate))} '
                f'"{_ttl_literal(obj)}"@en .'
            )

    for md_file in sorted(vault.vault_path.rglob("*.md")):
        if (md_file.name in _EXCLUDED_EXPORT_FILES or md_file.parent.name in ("workspace", "knowledge")):
            continue
        try:
            fm = vault._parse_frontmatter(md_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        node_id, title, node_type = fm.get("id"), fm.get("title"), fm.get("type")
        if not node_id:
            continue
        if title:
            lines.append(f'agent:{_ttl_slug(str(node_id))} rdfs:label "{_ttl_literal(title)}"@en .')
        if node_type:
            lines.append(f'agent:{_ttl_slug(str(node_id))} vantage:conceptType "{_ttl_literal(node_type)}" .')

    ttl = "\n".join(lines) + "\n"
    return StreamingResponse(
        io.BytesIO(ttl.encode("utf-8")),
        media_type="text/turtle",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-knowledge.ttl"'},
    )


@router.get("/{agent_name}/vault/sessions/search")
async def search_vault_sessions(
    agent_name: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
    """Full-text search scoped to traces/ (Ghost Traces / thought sessions) —
    the general /vault/search spans every family; this is the Traces-tab
    variant the UI's Sessions search box calls."""
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            """SELECT note_path, title, tags, snippet(memory_fts, 3, '**', '**', '...', 30) as snip
               FROM memory_fts
               WHERE agent_id=? AND note_path LIKE 'traces/%' AND memory_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (target["id"], q, limit),
        )).fetchall()
    results = []
    for note_path, title, tags_json, snip in rows:
        try:
            tags = json.loads(tags_json or "[]")
        except Exception:
            tags = []
        results.append({
            "id": note_path,
            "message": title,
            "trace_type": tags[0] if tags else "thought",
            "snippet": snip,
        })
    return {"results": results}


@router.get("/{agent_name}/vault/file/{path:path}")
async def get_vault_file(
    agent_name: str,
    path: str,
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
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
    x_federation_peer: Optional[str] = Header(None), agent: dict = Depends(get_agent)):
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
        "type": f"Note · {category.title()}",
        "title": title,
        "content_type": "text",
        "timestamp": datetime.utcnow().isoformat(),
        "tags": tags,
        "node_kind": "star",
        "galaxy_x": coords[0],
        "galaxy_y": coords[1],
        "galaxy_z": coords[2],
        "galaxy_size": 8,
        "galaxy_color": "#ffe66d",
        "constellation": tags[0] if tags else category,
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
async def get_memory_links(agent_name: str, agent: dict = Depends(get_agent)):
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


@router.get("/{agent_name}/vault/note-links")
async def get_note_links(agent_name: str, path: str = Query(...), agent: dict = Depends(get_agent)):
    """Links touching one specific note (used by the star-detail panel),
    as opposed to /vault/links which returns every link for the agent."""
    target = await _resolve_agent(agent_name)
    agent_id = target["id"]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT l.id, l.link_type, l.created_at,
                      sa.name AS source_agent_name, l.source_note_path,
                      ta.name AS target_agent_name, l.target_note_path
               FROM memory_links l
               JOIN agents sa ON sa.id = l.source_agent_id
               JOIN agents ta ON ta.id = l.target_agent_id
               WHERE (l.source_agent_id=? AND l.source_note_path=?)
                  OR (l.target_agent_id=? AND l.target_note_path=?)
               ORDER BY l.created_at DESC LIMIT 50""",
            (agent_id, path, agent_id, path),
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


# ── External memory connectors ─────────────────────────────────────────────────
# A connector is a scoped, revocable, ingest-only token an agent hands to a
# third-party tool (a CLI, a hook script, a custom bot) so that tool can push
# conversation transcripts straight into the agent's vault — without ever
# seeing the agent's real X-Agent-Key.

_MAX_MESSAGES_PER_CALL = 200
_MAX_CONTENT_LEN = 20000
_MAX_STORED_MESSAGES = 1000


def _sanitize_messages(raw) -> list:
    if not isinstance(raw, list) or not raw:
        raise HTTPException(422, "messages must be a non-empty list")
    if len(raw) > _MAX_MESSAGES_PER_CALL:
        raise HTTPException(422, f"at most {_MAX_MESSAGES_PER_CALL} messages per ingest call")
    out = []
    for m in raw:
        if not isinstance(m, dict) or not str(m.get("content", "")).strip():
            continue
        out.append({
            "role": str(m.get("role", "user"))[:20] or "user",
            "content": str(m["content"])[:_MAX_CONTENT_LEN],
            "ts": str(m.get("ts", "")) or datetime.utcnow().isoformat(),
        })
    if not out:
        raise HTTPException(422, "no valid messages with content")
    return out


@router.post("/{agent_name}/vault/external/connectors")
async def create_vault_connector(agent_name: str, request: Request, agent: dict = Depends(get_agent)):
    """Register a new external connector and return its token — shown exactly
    once. The token can only push conversations into this vault; it cannot
    read the vault or act as the agent anywhere else."""
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only create connectors for your own vault")
    body = await _parse_body(request)
    name = str(body.get("name", "")).strip()[:100]
    source = re.sub(r"[^a-z0-9_-]", "-", str(body.get("source", "custom")).strip().lower())[:40] or "custom"
    if not name:
        raise HTTPException(422, "name is required")

    token = "vconn_" + secrets.token_hex(24)
    token_hash = _hlib.sha256(token.encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO vault_connectors (agent_id, name, source, token_hash) VALUES (?,?,?,?)",
            (agent["id"], name, source, token_hash),
        )
        await db.commit()
        connector_id = cur.lastrowid

    connector_row = {
        "id": connector_id, "name": name, "source": source,
        "created_at": datetime.utcnow().isoformat(), "turn_count": 0,
    }
    vault = MemoryVault(agent["id"], agent["name"])
    await vault.render_connector(connector_row)

    return {
        "connector_id": connector_id,
        "name": name,
        "source": source,
        "token": token,
        "header": "X-Vault-Connector-Key",
        "ingest_url": "/api/vault/external/ingest",
        "warning": ("Save this token now — it cannot be shown again. Anyone holding it can write "
                    "conversations into this agent's vault, and nothing else."),
    }


@router.get("/{agent_name}/vault/external/connectors")
async def list_vault_connectors(agent_name: str, agent: dict = Depends(get_agent)):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only view your own connectors")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, name, source, created_at, last_used_at, revoked, turn_count
               FROM vault_connectors WHERE agent_id=? ORDER BY created_at DESC""",
            (agent["id"],),
        )).fetchall()
    return {"connectors": [dict(r) for r in rows]}


@router.delete("/{agent_name}/vault/external/connectors/{connector_id}")
async def revoke_vault_connector(agent_name: str, connector_id: int, agent: dict = Depends(get_agent)):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only revoke your own connectors")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE vault_connectors SET revoked=1 WHERE id=? AND agent_id=?",
            (connector_id, agent["id"]),
        )
        await db.commit()
        rowcount = cur.rowcount
    if rowcount == 0:
        raise HTTPException(404, "Connector not found")
    return {"revoked": True, "connector_id": connector_id}


@external_router.post("/ingest")
async def ingest_external_conversation(request: Request, connector: dict = Depends(get_vault_connector)):
    """Push conversation turns from an external LLM/agent/tool into this
    connector's owning agent's memory vault. Omit conversation_id for a
    one-off conversation, or reuse the same conversation_id across calls to
    stream in new turns as they happen — each call re-renders the vault note
    with the full (capped) transcript so far, so it shows up immediately."""
    body = await _parse_body(request)
    messages = _sanitize_messages(body.get("messages"))
    conversation_id = str(body.get("conversation_id") or uuid4().hex[:16])
    title = str(body.get("title", ""))[:150].strip()
    resource = str(body.get("resource", ""))[:500].strip()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing = await (await db.execute(
            "SELECT * FROM external_conversations WHERE connector_id=? AND conversation_id=?",
            (connector["id"], conversation_id),
        )).fetchone()

        if existing:
            prior = json.loads(existing["messages_json"] or "[]")
            combined = (prior + messages)[-_MAX_STORED_MESSAGES:]
            await db.execute(
                """UPDATE external_conversations
                   SET messages_json=?, turn_count=?, last_at=datetime('now'),
                       title=CASE WHEN ?<>'' THEN ? ELSE title END,
                       resource=CASE WHEN ?<>'' THEN ? ELSE resource END
                   WHERE id=?""",
                (json.dumps(combined), len(combined), title, title, resource, resource, existing["id"]),
            )
            conv_id = existing["id"]
        else:
            cur = await db.execute(
                """INSERT INTO external_conversations
                   (agent_id, connector_id, conversation_id, title, resource, messages_json, turn_count)
                   VALUES (?,?,?,?,?,?,?)""",
                (connector["agent_id"], connector["id"], conversation_id, title, resource,
                 json.dumps(messages), len(messages)),
            )
            conv_id = cur.lastrowid

        await db.execute(
            "UPDATE vault_connectors SET turn_count = turn_count + ?, last_used_at=datetime('now') WHERE id=?",
            (len(messages), connector["id"]),
        )
        await db.commit()

        conv_row = dict(await (await db.execute(
            "SELECT * FROM external_conversations WHERE id=?", (conv_id,)
        )).fetchone())
        connector_row = dict(await (await db.execute(
            "SELECT * FROM vault_connectors WHERE id=?", (connector["id"],)
        )).fetchone())
        agent_row = dict(await (await db.execute(
            "SELECT id, name FROM agents WHERE id=?", (connector["agent_id"],)
        )).fetchone())

    vault = MemoryVault(agent_row["id"], agent_row["name"])
    vault_path = await vault.render_external_conversation(conv_row, connector_row)
    await vault.render_connector(connector_row)

    return {
        "conversation_id": conversation_id,
        "turn_count": conv_row["turn_count"],
        "vault_path": vault_path,
    }
