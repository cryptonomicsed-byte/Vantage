"""Memory vault API endpoints."""
import io
import json as _json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
import aiosqlite

from ..deps import get_agent, _parse_body
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

@router.get(
    "/{agent_name}/vault/galaxy",
    summary="Get agent memory galaxy",
    description="Returns all memory stars (broadcasts, knowledge, traces) as a spatial graph. Each star has 3D coordinates, content_type, tags, constellation group, and creation date. Access-controlled by vault privacy setting.",
)
async def get_galaxy_data(
    agent_name: str,
    format: Optional[str] = Query(None, description="Response format: 'turtle' or 'jsonld'. Default JSON."),
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
    if format == "turtle":
        lines = [
            f"@prefix vantage: <https://vantage.agent/knowledge/{agent_name}/> .",
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            "",
        ]
        for edge in data.get("edges", []):
            s = re.sub(r"[^\w-]", "_", str(edge.get("subject", "")).strip())
            p = re.sub(r"[^\w-]", "_", str(edge.get("predicate", "")).strip())
            o = re.sub(r"[^\w-]", "_", str(edge.get("object", "")).strip())
            if s and p and o:
                lines.append(f"vantage:{s} vantage:{p} vantage:{o} .")
        return Response("\n".join(lines), media_type="text/turtle")
    if format == "jsonld":
        stars_ld = [{"@id": f"vantage:{s.get('id')}", "@type": "vantage:Star", "vantage:label": s.get("title")} for s in data.get("stars", [])]
        return {"@context": {"vantage": f"https://vantage.agent/knowledge/{agent_name}/"}, "@graph": stars_ld}
    return data

@router.get(
    "/{agent_name}/vault/search",
    summary="Search agent memory vault",
    description="Full-text search across all vault notes using FTS5 with Porter stemming. Returns matching note paths, titles, and highlighted snippets. Use this to retrieve relevant memories by keyword.",
)
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

@router.post(
    "/{agent_name}/vault/note",
    summary="Create memory note",
    description="Manually create an Obsidian-style markdown note in the agent's memory vault. The note gets spatial galaxy coordinates and is indexed in FTS5. Categories: drafts, templates, broadcasts, knowledge.",
)
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


# ── RDF / Turtle knowledge graph export ──────────────────────────────────────

@router.get(
    "/{agent_name}/vault/graph.ttl",
    summary="Export knowledge graph as RDF/Turtle",
    description="Export all knowledge triples (subject→predicate→object) from the agent's memory vault as W3C Turtle RDF format. Compatible with SPARQL endpoints, Neo4j, and graph analysis tools.",
    response_class=Response,
)
async def export_rdf_turtle(
    agent_name: str,
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403, "Access denied to this memory vault")

    data = vault.get_galaxy_data()
    lines = [
        f"@prefix vantage: <https://vantage.agent/knowledge/{agent_name}/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    for edge in data.get("edges", []):
        subj = re.sub(r"[^\w-]", "_", str(edge.get("subject", "")).strip())
        pred = re.sub(r"[^\w-]", "_", str(edge.get("predicate", "")).strip())
        obj = re.sub(r"[^\w-]", "_", str(edge.get("object", "")).strip())
        if subj and pred and obj:
            lines.append(f"vantage:{subj} vantage:{pred} vantage:{obj} .")
    return Response(
        "\n".join(lines),
        media_type="text/turtle",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-knowledge.ttl"'},
    )


@router.get(
    "/{agent_name}/vault/semantic-search",
    summary="Semantic search agent memory vault",
    description="Enhanced full-text search with wildcard expansion and relevance scoring. Returns results ranked by FTS5 score with matched snippets.",
)
async def semantic_search_vault(
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
    results = await vault.semantic_search(q)
    return {"query": q, "results": results, "mode": "fts5-expanded"}


@router.get("/{agent_name}/vault/note-links")
async def get_note_links(
    agent_name: str,
    path: str = Query(..., description="Relative path of the note within the vault"),
    x_agent_key: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    accessor_id = await _resolve_accessor(x_agent_key)
    vault = MemoryVault(target["id"], target["name"])
    if not await vault.check_access(accessor_id, ""):
        raise HTTPException(403)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT ml.id, ml.link_type, ml.created_at,
                      a_src.name as source_agent_name, ml.source_note_path,
                      a_tgt.name as target_agent_name, ml.target_note_path
               FROM memory_links ml
               LEFT JOIN agents a_src ON a_src.id = ml.source_agent_id
               LEFT JOIN agents a_tgt ON a_tgt.id = ml.target_agent_id
               WHERE (ml.source_agent_id=? AND ml.source_note_path=?)
                  OR (ml.target_agent_id=? AND ml.target_note_path=?)
               LIMIT 20""",
            (target["id"], path, target["id"], path)
        )).fetchall()
    return {"note_path": path, "links": [dict(r) for r in rows]}


# ── Layer 2: Session (trace) search ──────────────────────────────────────────

@router.get(
    "/{agent_name}/vault/sessions/search",
    summary="Search agent thought traces",
    description="Full-text search over the agent's ghost-mode reasoning traces (session memory). Returns matching trace snippets ranked by relevance.",
)
async def search_sessions_endpoint(
    agent_name: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)
    return {"query": q, "results": await vault.search_sessions(q, limit)}


# ── Layer 7 + 1: MCP context pack (ground truth + workspace + relevant memory) ─

@router.get(
    "/{agent_name}/vault/context",
    summary="Get agent memory context pack",
    description="Returns the agent's ground-truth hierarchy (SOUL), core identity (MEMORY), and the most relevant memories for a query. Inject this before reasoning so the agent uses its own documented knowledge instead of re-deriving it.",
)
async def get_context_pack(
    agent_name: str,
    q: str = Query("", description="Optional query to surface relevant memories"),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403, "Access denied to this memory vault")
    return await vault.get_context_pack(q)


# ── Layer 3: Knowledge trust scoring ─────────────────────────────────────────

@router.post("/{agent_name}/vault/knowledge/{fact_id}/feedback")
async def fact_feedback(
    agent_name: str,
    fact_id: int,
    helpful: bool = Query(..., description="Whether the fact was helpful"),
    agent: dict = Depends(get_agent),
):
    """Record helpful/unhelpful feedback on a knowledge fact and recompute its trust score."""
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only score facts in your own vault")
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT retrieval_count, helpful_count FROM knowledge_snippets WHERE id=? AND agent_id=?",
            (fact_id, agent["id"]),
        )).fetchone()
        if not row:
            raise HTTPException(404, "Fact not found")
        retrieval = (row[0] or 0) + 1
        helpful_total = (row[1] or 0) + (1 if helpful else 0)
        # Laplace-smoothed trust: (helpful + 1) / (retrieval + 2), uniform prior 0.5
        trust = (helpful_total + 1) / (retrieval + 2)
        await db.execute(
            """UPDATE knowledge_snippets
               SET retrieval_count=?, helpful_count=?, trust_score=?, last_accessed_at=datetime('now')
               WHERE id=? AND agent_id=?""",
            (retrieval, helpful_total, round(trust, 4), fact_id, agent["id"]),
        )
        await db.commit()
    return {
        "id": fact_id,
        "trust_score": round(trust, 4),
        "retrieval_count": retrieval,
        "helpful_count": helpful_total,
    }


# ── Workspace document editing ────────────────────────────────────────────────

@router.put(
    "/{agent_name}/vault/workspace/{doc}",
    summary="Update workspace document",
    description="Edit the agent's Layer-1 workspace document (MEMORY, USER, or CREATIVE). Preserves YAML frontmatter; replaces the markdown body. Also re-indexes the content in FTS5 for vault search.",
)
async def update_workspace_doc(
    agent_name: str,
    doc: Literal["MEMORY", "USER", "CREATIVE"],
    request: Request,
    agent: dict = Depends(get_agent),
):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only edit your own workspace documents")
    body = await _parse_body(request)
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(422, "content is required")
    vault = MemoryVault(agent["id"], agent["name"])
    path = vault.vault_path / "workspace" / f"{doc}.md"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        parts = existing.split("---", 2)
        fm = f"---{parts[1]}---\n" if len(parts) >= 3 else ""
    else:
        fm = f"---\ntype: workspace\nlayer: 1\nrole: {doc.lower()}\n---\n"
    path.write_text(fm + "\n" + content, encoding="utf-8")
    relative = str(path.relative_to(vault.vault_path))
    await vault._update_fts(relative, f"{doc} Workspace — {agent_name}", content, ["workspace", doc.lower()])
    return {"updated": doc, "path": relative}


# ── Pre-action protocol preflight ─────────────────────────────────────────────

@router.get(
    "/{agent_name}/vault/preflight",
    summary="Memory vault preflight check",
    description="Check the agent's memory vault before executing external searches. Implements the mandatory pre-action protocol from SOUL.md: inventory vault first, then decide whether external search is needed. Use this as the FIRST step of any research task.",
)
async def vault_preflight(
    agent_name: str,
    q: str = Query(..., min_length=1, description="The question or topic to check vault memory for"),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)
    memories = await vault.semantic_search(q, top_k=5)
    has_memories = len(memories) > 0
    soul_path = vault.vault_path / "SOUL.md"
    soul_summary = ""
    if soul_path.exists():
        try:
            soul_summary = soul_path.read_text(encoding="utf-8")[:300]
        except Exception:
            pass
    return {
        "should_search_externally": not has_memories,
        "reason": (
            f"Found {len(memories)} relevant memories — use them before searching externally."
            if has_memories
            else "No relevant memories found in vault. External search is appropriate."
        ),
        "memories": memories,
        "vault_inventory_summary": soul_summary,
    }


# ── Galaxy content negotiation (?format=) ─────────────────────────────────────

@router.get(
    "/{agent_name}/vault/galaxy.ttl",
    summary="Galaxy as RDF/Turtle (content-negotiation shortcut)",
    description="Returns the memory galaxy's knowledge edges in W3C Turtle format. Equivalent to GET /vault/galaxy?format=turtle.",
    response_class=Response,
)
async def get_galaxy_turtle(
    agent_name: str,
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)
    data = vault.get_galaxy_data()
    lines = [
        f"@prefix vantage: <https://vantage.agent/knowledge/{agent_name}/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "",
    ]
    for star in data.get("stars", []):
        sid = re.sub(r"[^\w-]", "_", str(star.get("id", "")).strip())
        if sid:
            for tag in (star.get("tags") or [])[:5]:
                t = re.sub(r"[^\w-]", "_", str(tag).strip())
                if t:
                    lines.append(f"vantage:{sid} vantage:hasTag vantage:{t} .")
    for edge in data.get("edges", []):
        subj = re.sub(r"[^\w-]", "_", str(edge.get("subject", "")).strip())
        pred = re.sub(r"[^\w-]", "_", str(edge.get("predicate", "")).strip())
        obj = re.sub(r"[^\w-]", "_", str(edge.get("object", "")).strip())
        if subj and pred and obj:
            lines.append(f"vantage:{subj} vantage:{pred} vantage:{obj} .")
    return Response(
        "\n".join(lines),
        media_type="text/turtle",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-galaxy.ttl"'},
    )


# ── Universal export ──────────────────────────────────────────────────────────

@router.get(
    "/{agent_name}/vault/export",
    summary="Export memory vault",
    description="Export the agent's memory vault in universal JSON format (storage-agnostic, portable to Obsidian/Roam/Logseq). Use ?format=obsidian for a ZIP download, ?format=universal (default) for JSON.",
)
async def export_vault(
    agent_name: str,
    format: str = Query("universal", description="Export format: 'universal' (JSON) or 'obsidian' (ZIP)"),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403, "Access denied to this memory vault")

    if format == "obsidian":
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

    if format == "roam":
        raise HTTPException(501, "Roam export not yet implemented")

    # Universal JSON
    data = vault.get_galaxy_data()
    universal = {
        "version": "1.0",
        "format": "vantage-galaxy",
        "agent": agent_name,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "nodes": [
            {
                "id": s.get("id"),
                "name": s.get("title"),
                "type": s.get("content_type"),
                "tags": s.get("tags", []),
                "created": s.get("created"),
                "constellation": s.get("constellation"),
            }
            for s in data.get("stars", [])
        ],
        "links": [
            {
                "source": e.get("subject"),
                "target": e.get("object"),
                "label": e.get("predicate"),
                "weight": e.get("weight", 1.0),
            }
            for e in data.get("edges", [])
        ],
    }
    return Response(
        _json.dumps(universal, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-vault-universal.json"'},
    )


# ── Universal import ──────────────────────────────────────────────────────────

def _parse_obsidian_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, parts[2].strip()


async def _import_nodes_from_universal(vault: MemoryVault, nodes: list, links: list) -> tuple[int, int]:
    imported_nodes = 0
    imported_links = 0
    for node in nodes:
        name = str(node.get("name") or node.get("title") or "").strip()
        if not name:
            continue
        ntype = str(node.get("type") or "note").strip()
        tags = node.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        category_map = {
            "knowledge": "knowledge",
            "broadcast": "broadcasts",
            "text": "broadcasts",
            "trace": "traces",
            "template": "templates",
        }
        category = category_map.get(ntype, "drafts")
        coords = vault._spatial_hash(name, category)
        fm = {
            "id": f"import_{uuid4().hex[:8]}",
            "type": "star",
            "content_type": ntype,
            "galaxy_x": coords[0],
            "galaxy_y": coords[1],
            "galaxy_z": coords[2],
            "galaxy_size": 8,
            "galaxy_color": "#c7ceea",
            "constellation": tags[0] if tags else category,
            "tags": list(tags),
            "created": node.get("created") or datetime.utcnow().isoformat(),
        }
        safe = re.sub(r"[^\w-]", "_", name[:50])
        note_path = vault.vault_path / category / f"{safe}.md"
        body = str(node.get("content") or "")
        vault._write_note(note_path, fm, body)
        rel = str(note_path.relative_to(vault.vault_path))
        await vault._update_fts(rel, name, body, list(tags))
        imported_nodes += 1

    for link in links:
        subj = str(link.get("source") or "").strip()
        pred = str(link.get("label") or "relates_to").strip()
        obj = str(link.get("target") or "").strip()
        if not subj or not obj:
            continue
        weight = float(link.get("weight") or 1.0)
        coords_s = vault._spatial_hash(subj, "knowledge")
        coords_t = vault._spatial_hash(obj, "knowledge")
        fm = {
            "id": f"klink_{uuid4().hex[:8]}",
            "type": "edge",
            "subject": subj,
            "predicate": pred,
            "object": obj,
            "weight": round(weight, 3),
            "trust": 0.5,
            "source_x": coords_s[0], "source_y": coords_s[1], "source_z": coords_s[2],
            "target_x": coords_t[0], "target_y": coords_t[1], "target_z": coords_t[2],
            "created": datetime.utcnow().isoformat(),
        }
        safe = re.sub(r"[^\w-]", "_", f"{subj[:20]}_{pred[:10]}_{obj[:20]}")
        note_path = vault.vault_path / "knowledge" / f"{safe}.md"
        vault._write_note(note_path, fm, f"{subj} {pred} {obj}")
        imported_links += 1

    return imported_nodes, imported_links


async def _import_obsidian_zip(vault: MemoryVault, file_bytes: bytes) -> tuple[int, int]:
    imported_nodes = 0
    imported_links = 0
    pending_links: list[tuple[str, str]] = []

    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".md"):
                continue
            try:
                text = zf.read(name).decode("utf-8", errors="replace")
            except Exception:
                continue
            fm, body = _parse_obsidian_frontmatter(text)
            title = fm.get("title") or Path(name).stem.replace("-", " ").replace("_", " ")
            raw_tags = fm.get("tags", "")
            tags = [t.strip() for t in str(raw_tags).split(",") if t.strip()]
            wikilinks = re.findall(r"\[\[(.+?)(?:\|.+?)?\]\]", body)
            for wl in wikilinks:
                pending_links.append((title, wl.strip()))

            coords = vault._spatial_hash(title, "drafts")
            note_fm = {
                "id": f"obs_{uuid4().hex[:8]}",
                "type": "star",
                "content_type": "note",
                "galaxy_x": coords[0],
                "galaxy_y": coords[1],
                "galaxy_z": coords[2],
                "galaxy_size": 7,
                "galaxy_color": "#a8ff78",
                "constellation": tags[0] if tags else "imported",
                "tags": tags,
                "created": fm.get("date") or datetime.utcnow().isoformat(),
                "source": "obsidian",
            }
            safe = re.sub(r"[^\w-]", "_", title[:50])
            note_path = vault.vault_path / "drafts" / f"{safe}.md"
            vault._write_note(note_path, note_fm, body)
            rel = str(note_path.relative_to(vault.vault_path))
            await vault._update_fts(rel, title, body, tags)
            imported_nodes += 1

    # Create knowledge triples for wiki links
    for src_title, tgt_title in pending_links:
        coords_s = vault._spatial_hash(src_title, "knowledge")
        coords_t = vault._spatial_hash(tgt_title, "knowledge")
        fm2 = {
            "id": f"wl_{uuid4().hex[:8]}",
            "type": "edge",
            "subject": src_title,
            "predicate": "references",
            "object": tgt_title,
            "weight": 0.5,
            "trust": 0.5,
            "source_x": coords_s[0], "source_y": coords_s[1], "source_z": coords_s[2],
            "target_x": coords_t[0], "target_y": coords_t[1], "target_z": coords_t[2],
            "created": datetime.utcnow().isoformat(),
        }
        safe2 = re.sub(r"[^\w-]", "_", f"{src_title[:20]}_ref_{tgt_title[:20]}")
        kpath = vault.vault_path / "knowledge" / f"{safe2}.md"
        vault._write_note(kpath, fm2, f"{src_title} references {tgt_title}")
        imported_links += 1

    return imported_nodes, imported_links


@router.post(
    "/{agent_name}/vault/import",
    summary="Import to memory vault",
    description="Import nodes into the agent's memory vault. Accepts Universal JSON body, Universal JSON file, or Obsidian vault ZIP. Auto-detects format from Content-Type or file extension.",
)
async def import_vault(
    agent_name: str,
    request: Request,
    agent: dict = Depends(get_agent),
    file: Optional[UploadFile] = File(None),
):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only import to your own vault")

    vault = MemoryVault(agent["id"], agent["name"])
    ct = request.headers.get("content-type", "")

    if file is not None:
        # Multipart file upload
        file_bytes = await file.read()
        filename = file.filename or ""
        if filename.endswith(".zip"):
            n, l = await _import_obsidian_zip(vault, file_bytes)
            return {"imported_nodes": n, "imported_links": l, "format": "obsidian-zip"}
        else:
            try:
                universal = _json.loads(file_bytes.decode("utf-8"))
            except Exception:
                raise HTTPException(422, "Invalid JSON file")
            n, l = await _import_nodes_from_universal(
                vault,
                universal.get("nodes", []),
                universal.get("links", []),
            )
            return {"imported_nodes": n, "imported_links": l, "format": "universal-json"}

    if "application/json" in ct:
        try:
            universal = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid JSON body")
        n, l = await _import_nodes_from_universal(
            vault,
            universal.get("nodes", []),
            universal.get("links", []),
        )
        return {"imported_nodes": n, "imported_links": l, "format": "universal-json"}

    raise HTTPException(415, "Provide a file upload (.json or .zip) or application/json body")


# ── Pre-action protocol preflight ────────────────────────────────────────────

@router.get(
    "/{agent_name}/vault/preflight",
    summary="Check vault before acting",
    description=(
        "Pre-action protocol check: query the vault BEFORE external searches or tool calls. "
        "Returns whether high-trust memories already cover the query, so agents can use vault "
        "knowledge instead of re-deriving facts externally. MCP-accessible via auto-exposure."
    ),
)
async def vault_preflight(
    agent_name: str,
    q: str = Query(..., min_length=1, description="The query or task you are about to perform"),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)

    memories = await vault.semantic_search(q, top_k=5)
    high_trust = [m for m in memories if m.get("score", 0) > 0.5]

    has_memories = len(memories) > 0
    has_high_trust = len(high_trust) > 0

    if has_high_trust:
        action = "USE_MEMORY"
        should_search = False
        reason = f"Found {len(high_trust)} high-relevance memories. Use vault knowledge — do not re-derive externally."
    elif has_memories:
        action = "DECLARE_GAP"
        should_search = True
        reason = f"Found {len(memories)} low-confidence memories. External search permitted; compare against vault results."
    else:
        action = "NO_MEMORY"
        should_search = True
        reason = "No relevant memories found. External search permitted. Document findings in vault after."

    # Vault inventory summary
    stats_path = vault.vault_path
    note_count = sum(1 for _ in stats_path.rglob("*.md"))

    return {
        "action": action,
        "should_search_externally": should_search,
        "reason": reason,
        "memories": memories,
        "memory_count": len(memories),
        "vault_inventory_summary": f"{note_count} notes in vault for {agent_name}",
        "directive": (
            "GROUND TRUTH PROTOCOL: Inventory → Match → Use/Declare → Act. "
            "If USE_MEMORY: stop, use the memories above. "
            "If DECLARE_GAP: search externally, then reconcile with vault. "
            "If NO_MEMORY: search freely, then document findings."
        ),
    }


# ── Vector semantic search ─────────────────────────────────────────────────────

@router.get(
    "/{agent_name}/vault/search/vector",
    summary="Vector semantic search (OpenRouter)",
    description=(
        "High-quality semantic search using OpenRouter text-embedding-3-large embeddings. "
        "Falls back to wildcard FTS5 if OPENROUTER_KEY is not configured. "
        "Results include cosine similarity scores."
    ),
)
async def vector_search(
    agent_name: str,
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    x_agent_key: Optional[str] = Header(None),
    x_federation_peer: Optional[str] = Header(None),
):
    target = await _resolve_agent(agent_name)
    vault = MemoryVault(target["id"], target["name"])
    accessor_id = await _resolve_accessor(x_agent_key)
    if not await vault.check_access(accessor_id, x_federation_peer or ""):
        raise HTTPException(403)

    results = await vault.semantic_search_vector(q, top_k)
    mode = "vector" if results and results[0].get("source") == "vector" else "fts-fallback"
    return {"query": q, "results": results, "count": len(results), "mode": mode}


@router.post(
    "/{agent_name}/vault/index-embeddings",
    summary="Index vault notes for vector search",
    description="Pre-compute and cache OpenRouter embeddings for all vault notes. Required before vector search returns results. Requires OPENROUTER_KEY to be configured.",
)
async def index_embeddings(agent_name: str, agent: dict = Depends(get_agent)):
    if agent["name"] != agent_name:
        raise HTTPException(403, "Can only index your own vault")
    vault = MemoryVault(agent["id"], agent["name"])
    count = await vault.index_all_embeddings()
    return {"indexed": count, "agent": agent_name}
