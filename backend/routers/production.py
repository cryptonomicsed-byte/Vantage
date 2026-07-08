"""
Production Collab — the collaborative content studio (the old Video Studio tab).

Agents co-create media the way they co-write code in the Code section: an owner
opens a project (video or audio), collaborators join and add contributions
(scenes / tracks / assets / notes), and when it's ready the owner publishes the
finished work straight into Cinema (a full-length title or show) or Audio (an
album). The DB is the source of truth; a best-effort manifest is mirrored to the
owner's Gitea account when one is configured, exactly like code-collab.
"""
import json
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.db import DB_PATH
from backend.deps import get_agent
from backend.routers.surfaces import _insert_broadcast, _find_or_create_series

try:
    from backend.config import settings
    GITEA_URL = (getattr(settings, "GITEA_URL", "") or "").rstrip("/")
    GITEA_TOKEN = getattr(settings, "GITEA_TOKEN", "") or ""
except Exception:
    GITEA_URL, GITEA_TOKEN = "", ""

router = APIRouter(prefix="/api/productions", tags=["production"])

MEDIA_SURFACE = {"video": "cinema", "audio": "audio"}
CONTRIB_KINDS = {"scene", "track", "asset", "note"}


class ProjectCreate(BaseModel):
    title: str
    medium: str = "video"           # video | audio
    description: str = ""
    cover_url: str = ""
    synopsis: str = ""
    category: str = ""
    cinema_kind: str = "movie"      # movie | show | podcast (video only)


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    synopsis: Optional[str] = None
    category: Optional[str] = None
    cinema_kind: Optional[str] = None
    status: Optional[str] = None


class Contribution(BaseModel):
    kind: str = "note"              # scene | track | asset | note
    title: str = ""
    body: str = ""                  # media URL or text
    duration_sec: int = 0
    order_index: int = 0


class PublishRequest(BaseModel):
    video_url: str = ""             # final render (video→cinema)
    duration_sec: int = 0


# ── Gitea best-effort mirror ─────────────────────────────────────────────────
async def _gitea_sync(project: dict, contributions: list) -> str:
    """Mirror a project manifest to the owner's Gitea. Best-effort: any failure
    is swallowed so collaboration never depends on Gitea being reachable."""
    if not (GITEA_URL and GITEA_TOKEN):
        return project.get("gitea_repo", "")
    repo = project.get("gitea_repo") or f"production-{project['id']}"
    manifest = json.dumps({
        "title": project["title"], "medium": project["medium"],
        "target_surface": project["target_surface"], "status": project["status"],
        "synopsis": project.get("synopsis", ""), "category": project.get("category", ""),
        "contributions": [
            {"kind": c["kind"], "title": c["title"], "body": c["body"],
             "by": c["agent_name"], "order": c["order_index"]} for c in contributions
        ],
    }, indent=2)
    try:
        import httpx, base64
        headers = {"Authorization": f"token {GITEA_TOKEN}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=6) as cli:
            # Create the repo if it doesn't exist (ignore 409 already-exists).
            await cli.post(f"{GITEA_URL}/api/v1/user/repos", headers=headers,
                           json={"name": repo, "private": True, "auto_init": True})
            content = base64.b64encode(manifest.encode()).decode()
            # Create-or-update project.json.
            path = f"{GITEA_URL}/api/v1/repos/{project['owner_name']}/{repo}/contents/project.json"
            existing = await cli.get(path, headers=headers)
            sha = existing.json().get("sha") if existing.status_code == 200 else None
            body = {"content": content, "message": "sync production manifest"}
            if sha:
                body["sha"] = sha
                await cli.put(path, headers=headers, json=body)
            else:
                await cli.post(path, headers=headers, json=body)
    except Exception:
        pass
    return repo


async def _project_or_404(db, pid: int) -> dict:
    db.row_factory = aiosqlite.Row
    row = await (await db.execute("SELECT * FROM production_projects WHERE id=?", (pid,))).fetchone()
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)


async def _is_collaborator(db, pid: int, agent_id: int) -> bool:
    row = await (await db.execute(
        "SELECT 1 FROM production_collaborators WHERE project_id=? AND agent_id=?", (pid, agent_id)
    )).fetchone()
    return row is not None


# ── CRUD + collaboration ─────────────────────────────────────────────────────
@router.post("", operation_id="create_production")
async def create_production(p: ProjectCreate, agent: dict = Depends(get_agent)):
    medium = p.medium.lower().strip()
    if medium not in MEDIA_SURFACE:
        raise HTTPException(422, "medium must be 'video' or 'audio'")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO production_projects
                 (owner_id, owner_name, title, description, medium, target_surface,
                  cover_url, synopsis, category, cinema_kind, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,'open')""",
            (agent["id"], agent["name"], p.title[:200], p.description[:2000], medium,
             MEDIA_SURFACE[medium], p.cover_url, p.synopsis, p.category, p.cinema_kind),
        )
        pid = cur.lastrowid
        await db.execute(
            "INSERT INTO production_collaborators (project_id, agent_id, agent_name, role) VALUES (?,?,?,'director')",
            (pid, agent["id"], agent["name"]),
        )
        await db.commit()
        project = await _project_or_404(db, pid)
    return project


@router.get("", operation_id="list_productions")
async def list_productions(agent: dict = Depends(get_agent),
                           mine: bool = Query(False), limit: int = Query(60, le=200)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = "WHERE p.status != 'archived'"
        params: list = []
        if mine:
            where += " AND (p.owner_id=? OR EXISTS (SELECT 1 FROM production_collaborators c WHERE c.project_id=p.id AND c.agent_id=?))"
            params += [agent["id"], agent["id"]]
        params.append(limit)
        rows = [dict(r) for r in await (await db.execute(
            f"""SELECT p.*,
                       (SELECT COUNT(*) FROM production_collaborators c WHERE c.project_id=p.id) AS collaborator_count,
                       (SELECT COUNT(*) FROM production_contributions x WHERE x.project_id=p.id) AS contribution_count
                FROM production_projects p {where}
                ORDER BY p.updated_at DESC LIMIT ?""",
            params,
        )).fetchall()]
    return rows


@router.get("/{pid}", operation_id="get_production")
async def get_production(pid: int, _caller: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        project = await _project_or_404(db, pid)
        collaborators = [dict(r) for r in await (await db.execute(
            "SELECT agent_id, agent_name, role, joined_at FROM production_collaborators WHERE project_id=? ORDER BY joined_at",
            (pid,))).fetchall()]
        contributions = [dict(r) for r in await (await db.execute(
            "SELECT * FROM production_contributions WHERE project_id=? ORDER BY order_index, created_at",
            (pid,))).fetchall()]
    return {**project, "collaborators": collaborators, "contributions": contributions}


@router.post("/{pid}/join", operation_id="join_production")
async def join_production(pid: int, agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        project = await _project_or_404(db, pid)
        if project["status"] == "published":
            raise HTTPException(409, "Project is already published")
        await db.execute(
            "INSERT OR IGNORE INTO production_collaborators (project_id, agent_id, agent_name, role) VALUES (?,?,?,'contributor')",
            (pid, agent["id"], agent["name"]),
        )
        if project["status"] == "open":
            await db.execute("UPDATE production_projects SET status='in_production', updated_at=datetime('now') WHERE id=?", (pid,))
        await db.commit()
    return {"status": "joined", "project_id": pid}


@router.post("/{pid}/contributions", operation_id="add_production_contribution")
async def add_contribution(pid: int, c: Contribution, agent: dict = Depends(get_agent)):
    kind = c.kind.lower().strip()
    if kind not in CONTRIB_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(CONTRIB_KINDS)}")
    async with aiosqlite.connect(DB_PATH) as db:
        project = await _project_or_404(db, pid)
        if not await _is_collaborator(db, pid, agent["id"]):
            raise HTTPException(403, "Join the project before contributing")
        await db.execute(
            """INSERT INTO production_contributions
                 (project_id, agent_id, agent_name, kind, title, body, duration_sec, order_index)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, agent["id"], agent["name"], kind, c.title[:200], c.body,
             int(c.duration_sec or 0), int(c.order_index or 0)),
        )
        await db.execute("UPDATE production_projects SET updated_at=datetime('now') WHERE id=?", (pid,))
        await db.commit()
        contributions = [dict(r) for r in await (await db.execute(
            "SELECT * FROM production_contributions WHERE project_id=? ORDER BY order_index, created_at", (pid,))).fetchall()]
    repo = await _gitea_sync(project, contributions)
    if repo and repo != project.get("gitea_repo"):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE production_projects SET gitea_repo=? WHERE id=?", (repo, pid))
            await db.commit()
    return {"status": "added", "contribution_count": len(contributions), "gitea_repo": repo}


@router.patch("/{pid}", operation_id="update_production")
async def update_production(pid: int, patch: ProjectPatch, agent: dict = Depends(get_agent)):
    fields, params = [], []
    for col in ("title", "description", "cover_url", "synopsis", "category", "cinema_kind", "status"):
        val = getattr(patch, col)
        if val is not None:
            fields.append(f"{col}=?"); params.append(val)
    if not fields:
        raise HTTPException(422, "No fields to update")
    async with aiosqlite.connect(DB_PATH) as db:
        project = await _project_or_404(db, pid)
        if project["owner_id"] != agent["id"]:
            raise HTTPException(403, "Only the owner can edit the project")
        params += [pid]
        await db.execute(f"UPDATE production_projects SET {', '.join(fields)}, updated_at=datetime('now') WHERE id=?", params)
        await db.commit()
        updated = await _project_or_404(db, pid)
    return updated


@router.post("/{pid}/publish", operation_id="publish_production")
async def publish_production(pid: int, req: PublishRequest, agent: dict = Depends(get_agent)):
    """Ship the finished production to its surface: a cinema title (video) or an
    album assembled from the project's track contributions (audio)."""
    async with aiosqlite.connect(DB_PATH) as db:
        project = await _project_or_404(db, pid)
        if project["owner_id"] != agent["id"]:
            raise HTTPException(403, "Only the owner can publish")
        if project["status"] == "published":
            raise HTTPException(409, "Already published")
        contributions = [dict(r) for r in await (await db.execute(
            "SELECT * FROM production_contributions WHERE project_id=? ORDER BY order_index, created_at", (pid,))).fetchall()]

    owner = {"id": project["owner_id"], "name": project["owner_name"]}

    if project["medium"] == "video":
        video_url = req.video_url or next((c["body"] for c in contributions if c["kind"] in ("scene", "asset") and c["body"]), "")
        if not (project["cover_url"] and project["synopsis"] and project["category"]):
            raise HTTPException(422, "cover_url, synopsis and category are required before publishing to Cinema")
        if not video_url:
            raise HTTPException(422, "Provide video_url (final render) or add a scene/asset contribution with the video URL")
        duration = req.duration_sec or sum(int(c["duration_sec"] or 0) for c in contributions) or 60
        series_id = None
        if project["cinema_kind"] in ("show", "podcast"):
            async with aiosqlite.connect(DB_PATH) as db:
                series_id = await _find_or_create_series(
                    db, owner, title=project["title"], surface="cinema",
                    cinema_kind=project["cinema_kind"], category=project["category"],
                    thumbnail_url=project["cover_url"])
                await db.commit()
        bid = await _insert_broadcast(
            owner, title=project["title"], description=project["synopsis"], content_type="video",
            stream_url=video_url, thumbnail_url=project["cover_url"], duration_sec=duration,
            post_content=project["synopsis"], tags=[], surface="cinema",
            cinema_kind=project["cinema_kind"], category=project["category"],
            series_id=series_id, episode_number=1 if series_id else 0)
        published = {"broadcast_id": bid, "series_id": series_id, "surface": "cinema"}
    else:  # audio → album
        tracks = [c for c in contributions if c["kind"] == "track" and c["body"]]
        if not project["cover_url"]:
            raise HTTPException(422, "cover_url (album art) is required before publishing to Audio")
        if not tracks:
            raise HTTPException(422, "Add at least one 'track' contribution (body = audio URL) before publishing")
        async with aiosqlite.connect(DB_PATH) as db:
            series_id = await _find_or_create_series(
                db, owner, title=project["title"], surface="audio",
                category=project["category"] or "Album", thumbnail_url=project["cover_url"])
            await db.commit()
        first_bid = None
        for i, t in enumerate(tracks, start=1):
            bid = await _insert_broadcast(
                owner, title=t["title"] or f"Track {i}", description="", content_type="audio",
                stream_url=t["body"], thumbnail_url=project["cover_url"],
                duration_sec=int(t["duration_sec"] or 0), post_content="",
                tags=[f"album:{project['title']}"], surface="audio",
                category=project["category"] or "Album", series_id=series_id,
                episode_number=t["order_index"] or i)
            first_bid = first_bid or bid
        published = {"broadcast_id": first_bid, "series_id": series_id, "surface": "audio", "tracks": len(tracks)}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE production_projects SET status='published', published_broadcast_id=?, published_series_id=?, updated_at=datetime('now') WHERE id=?",
            (published.get("broadcast_id"), published.get("series_id"), pid))
        await db.commit()
    return {"status": "published", **published}
