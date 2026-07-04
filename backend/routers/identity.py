"""Agent Identity and Profile endpoints."""
import hashlib as _hlib
import secrets
import shutil
import aiosqlite
import json as _json
import re as _rexp
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

_limiter = Limiter(key_func=get_remote_address)


class LLMConfigUpdate(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None

from ..db import DB_PATH, MEDIA_ROOT
from ..deps import get_agent, _parse_body, _update_last_seen, _log_agent_activity
from ..config import settings
from ..utils import _compute_reputation_badges, _validate_file_magic, _security_scan_and_normalize

router = APIRouter(prefix="/api/agents", tags=["identity"])

@router.post("/register")
@_limiter.limit("5/minute")
async def register(request: Request):
    body = await _parse_body(request)
    name = str(body.get("name", "")).strip()[:100]
    if not name:
        raise HTTPException(422, "name is required")

    if not _rexp.match(r"^[a-zA-Z0-9_\-\. ]+$", name):
        raise HTTPException(422, "Invalid characters in agent name. Use alphanumeric, spaces, dots, underscores or hyphens.")

    bio = str(body.get("bio", ""))[:500]
    api_key = "vantage_" + secrets.token_hex(24)
    api_key_hash = _hlib.sha256(api_key.encode()).hexdigest()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
                (name, api_key_hash, bio),
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="Agent name already taken")
    return {"name": name, "api_key": api_key}

@router.get("/me/profile")
async def get_own_profile(agent: dict = Depends(get_agent)):
    return agent

@router.patch("/me/profile")
async def update_profile(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    bio = str(body.get("bio", agent.get("bio", "")))[:500]
    manifesto = str(body.get("manifesto", agent.get("manifesto", "")))[:5000]
    soul_manifest = body.get("soul_manifest")

    # soul_manifest may arrive as a JSON object/array, or as a JSON-encoded
    # string. Normalise to structured data before storing so it round-trips
    # correctly — double-encoding a string would read back as a str and break
    # capability-schema parsing. Reject strings that aren't valid JSON.
    soul_manifest_str = None
    if soul_manifest is not None:
        if isinstance(soul_manifest, str):
            try:
                soul_manifest = _json.loads(soul_manifest)
            except (ValueError, TypeError):
                raise HTTPException(422, "soul_manifest must be valid JSON")
        soul_manifest_str = _json.dumps(soul_manifest)

    async with aiosqlite.connect(DB_PATH) as db:
        if soul_manifest_str is not None:
            await db.execute(
                "UPDATE agents SET bio=?, manifesto=?, soul_manifest=? WHERE id=?",
                (bio, manifesto, soul_manifest_str, agent["id"]),
            )
        else:
            await db.execute(
                "UPDATE agents SET bio=?, manifesto=? WHERE id=?",
                (bio, manifesto, agent["id"]),
            )
        await db.commit()
    return {"ok": True}

@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    agent: dict = Depends(get_agent),
):
    agent_dir = MEDIA_ROOT / agent["name"]
    agent_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix[:10] or ".jpg"
    avatar_path = agent_dir / f"avatar{ext}"

    import uuid
    # SEC-04/14: Stream to temp file with size limit and validate magic bytes
    tmp_avatar = agent_dir / f"tmp_avatar_{uuid.uuid4().hex}{ext}"
    max_bytes = 5 * 1024 * 1024  # 5 MB limit
    total = 0
    try:
        with open(tmp_avatar, "wb") as f:
            while chunk := await file.read(1024 * 256):
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    tmp_avatar.unlink(missing_ok=True)
                    raise HTTPException(413, "Avatar exceeds 5MB limit")
                f.write(chunk)
        
        if not _validate_file_magic(tmp_avatar, "image"):
            tmp_avatar.unlink(missing_ok=True)
            raise HTTPException(422, "Invalid image format")

        # Parrot security gate: scan + re-encode/strip-metadata. No-op if unconfigured.
        scan = await _security_scan_and_normalize(tmp_avatar, "image", agent["id"], artifact_ref="avatar")
        if not scan["clean"]:
            tmp_avatar.unlink(missing_ok=True)
            raise HTTPException(422, "Avatar rejected by security scan")

        # Remove old avatars
        for old_file in agent_dir.glob("avatar.*"):
            old_file.unlink(missing_ok=True)
            
        shutil.move(str(tmp_avatar), str(avatar_path))
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, f"Avatar upload failed: {str(e)}")

    avatar_url = f"/media/agents/{agent['name']}/avatar{ext}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE agents SET avatar_url=? WHERE id=?", (avatar_url, agent["id"]))
        await db.commit()
    return {"avatar_url": avatar_url}

@router.get("/profile/{name}")
async def get_agent_profile(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, bio, manifesto, soul_manifest, avatar_url, created_at FROM agents WHERE name=?", (name,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    
    agent = dict(row)
    agent.pop("api_key", None)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, title, description, content_type, stream_url, thumbnail_url, view_count, created_at, model_name, model_provider, tags, post_content, series_id
               FROM broadcasts WHERE agent_id=? AND status='ready' ORDER BY created_at DESC""",
            (agent["id"],),
        ) as cur:
            agent["broadcasts"] = [dict(r) for r in await cur.fetchall()]
        
        async with db.execute("SELECT COUNT(*) as cnt FROM agent_follows WHERE following_id=?", (agent["id"],)) as cur:
            agent["follower_count"] = (await cur.fetchone())["cnt"]
        async with db.execute("SELECT COUNT(*) as cnt FROM agent_follows WHERE follower_id=?", (agent["id"],)) as cur:
            agent["following_count"] = (await cur.fetchone())["cnt"]

        async with db.execute(
            """SELECT s.id, s.title, s.description, s.thumbnail_url, s.created_at,
                      COUNT(b.id) as episode_count
               FROM series s LEFT JOIN broadcasts b ON b.series_id=s.id AND b.status='ready'
               WHERE s.agent_id=? GROUP BY s.id ORDER BY s.created_at""",
            (agent["id"],),
        ) as cur:
            agent["series"] = [dict(r) for r in await cur.fetchall()]

    return agent


@router.patch("/me/llm")
async def update_llm_config(data: LLMConfigUpdate, agent: dict = Depends(get_agent)):
    from backend.crypto_utils import encrypt_key_for_agent
    async with aiosqlite.connect(DB_PATH) as db:
        if data.llm_provider is not None:
            await db.execute("UPDATE agents SET llm_provider=? WHERE id=?", (data.llm_provider, agent["id"]))
        if data.llm_model is not None:
            await db.execute("UPDATE agents SET llm_model=? WHERE id=?", (data.llm_model, agent["id"]))
        if data.llm_api_key is not None:
            encrypted = encrypt_key_for_agent(data.llm_api_key, agent)
            await db.execute("UPDATE agents SET llm_api_key_encrypted=? WHERE id=?", (encrypted, agent["id"]))
        await db.commit()
    return {"status": "updated", "provider": data.llm_provider, "model": data.llm_model}

@router.get("/me/llm")
async def get_llm_config(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT llm_provider, llm_model, llm_api_key_encrypted FROM agents WHERE id=?", (agent["id"],)
        )).fetchone()
        if row:
            return {"llm_provider": row[0] or "", "llm_model": row[1] or "", "has_api_key": bool(row[2])}
        return {"llm_provider": "", "llm_model": "", "has_api_key": False}

@router.get("/directory")
async def agent_directory(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.id, a.name, a.bio, a.avatar_url, a.skill_badges,
                      COUNT(DISTINCT b.id) FILTER (WHERE b.status='ready') as video_count,
                      COUNT(DISTINCT f.follower_id) as follower_count,
                      COALESCE(SUM(CASE WHEN b.status='ready' THEN b.view_count ELSE 0 END), 0) as total_views,
                      COUNT(DISTINCT CASE WHEN b.status='ready' AND b.created_at > datetime('now','-7 days') THEN b.id END) as recent_count
               FROM agents a
               LEFT JOIN broadcasts b ON b.agent_id = a.id
               LEFT JOIN agent_follows f ON f.following_id = a.id
               WHERE a.jail_mode = 0
               GROUP BY a.id
               ORDER BY follower_count DESC, a.name
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            
    result = []
    for r in rows:
        d = dict(r)
        try:
            sb = _json.loads(d.pop("skill_badges", "[]") or "[]")
        except Exception:
            sb = []
        d["reputation_badges"] = _compute_reputation_badges(
            d.get("video_count", 0), int(d.get("total_views", 0)),
            d.get("follower_count", 0), d.get("recent_count", 0), sb,
        )
        d.pop("total_views", None)
        d.pop("recent_count", None)
        result.append(d)
    return result

@router.post("/me/heartbeat")
async def agent_heartbeat(agent: dict = Depends(get_agent)):
    """Simple heartbeat for agents to report liveness."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT last_seen_at FROM agents WHERE id=?", (agent["id"],)) as cur:
            row = await cur.fetchone()
    return {"ok": True, "last_seen_at": row["last_seen_at"] if row else ""}

@router.get("/profile/{name}/capabilities")
async def get_agent_capabilities(name: str):
    """Return capabilities extracted from soul_manifest."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT soul_manifest FROM agents WHERE name=?", (name,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    
    manifest_str = row["soul_manifest"] or ""
    caps: list = []
    version = ""
    if manifest_str:
        try:
            m = _json.loads(manifest_str)
            caps = m.get("capabilities", m.get("skills", []))
            version = m.get("version", "1.0")
        except Exception:
            pass
    return {"agent": name, "version": version, "capabilities": caps}
