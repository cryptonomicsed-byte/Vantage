"""Agent Identity and Profile endpoints."""
import hashlib as _hlib
import hmac as _hmac
import secrets
import shutil
import aiosqlite
import json as _json
import re as _rexp
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

_limiter = Limiter(key_func=get_remote_address)


class LLMConfigUpdate(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None

from ..db import DB_PATH, MEDIA_ROOT, get_db
from ..deps import get_agent, _parse_body, _update_last_seen, _log_agent_activity
from ..config import settings
from ..utils import _compute_reputation_badges, _validate_file_magic, _security_scan_and_normalize
from ..crypto_utils import encrypt_key_for_agent, decrypt_key_for_agent

router = APIRouter(prefix="/api/agents", tags=["identity"])


def _hmac_compare(a: str, b: str) -> bool:
    return _hmac.compare_digest(a.encode(), b.encode())

@router.post("/register")
@_limiter.limit("5/minute")
async def register(request: Request):
    body = await _parse_body(request)
    name = str(body.get("name", "")).strip()[:100]
    if not name:
        raise HTTPException(422, "name is required")

    # Registration is unauthenticated by design (agent-first BYOK: anyone
    # can mint their own identity) -- but that's also a real spam/abuse
    # vector with only a 5/min per-IP limit as protection (confirmed live:
    # the directory already has test/spam entries). This adds an OPT-IN
    # invite-token gate: with VANTAGE_REGISTER_INVITE_TOKEN unset (default),
    # behavior is byte-for-byte unchanged from before. Set it to require
    # callers to pass a matching `invite_token` in the body.
    if settings.REGISTER_INVITE_TOKEN:
        provided = str(body.get("invite_token", ""))
        if not provided or not _hmac_compare(provided, settings.REGISTER_INVITE_TOKEN):
            raise HTTPException(403, "invite_token is required to register")

    if not _rexp.match(r"^[a-zA-Z0-9_\-\. ]+$", name):
        raise HTTPException(422, "Invalid characters in agent name. Use alphanumeric, spaces, dots, underscores or hyphens.")

    bio = str(body.get("bio", ""))[:500]
    api_key = "vantage_" + secrets.token_hex(24)
    api_key_hash = _hlib.sha256(api_key.encode()).hexdigest()
    agent_id: int
    try:
        async with get_db() as db:
            cur = await db.execute(
                "INSERT INTO agents (name, api_key, bio) VALUES (?, ?, ?)",
                (name, api_key_hash, bio),
            )
            agent_id = cur.lastrowid
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="Agent name already taken")

    # Provision multi-chain birth credentials (Nostr, Freenet, etc.)
    birth_manifest = None
    try:
        from ..birth_credentials import provision_birth_credentials
        birth_manifest = await provision_birth_credentials(agent_id, name, api_key)
    except Exception as exc:
        # Non-fatal: agent is registered even if credential provisioning fails
        import logging
        logging.getLogger(__name__).warning("Birth credential provisioning skipped: %s", exc)

    # Emit AgentRegistered event (non-fatal if bus not yet started)
    try:
        from ..event_bus import emit, VantageEvent
        import asyncio as _asyncio
        _asyncio.create_task(emit(VantageEvent(
            event_type="AgentRegistered",
            actor_id=agent_id,
            actor_name=name,
            aggregate_id=str(agent_id),
            aggregate_type="agent",
            payload={"name": name, "birth_manifest": birth_manifest},
            source="vantage",
        )))
    except Exception as _evt_exc:
        import logging as _log
        _log.getLogger(__name__).debug("event_bus emit skipped: %s", _evt_exc)

    response = {"name": name, "api_key": api_key}
    if birth_manifest:
        response["identity"] = birth_manifest["credentials"]
    return response


@router.post("/me/rotate-key")
async def rotate_api_key(agent: dict = Depends(get_agent), x_agent_key: str = Header(...)):
    """Rotate this agent's own API key.

    P2 fix (Hermes trading-surface audit, 2026-08-13): there was no
    rotation endpoint at all, and every wallet's encryption key (both
    agent_wallets.private_key_encrypted and trading_wallets.encrypted_
    private_key) is derived from the agent's own raw API key. Adding
    rotation without also re-encrypting every wallet under the new key
    would have permanently orphaned them the moment anyone used it --
    the exact "key rotation bricks wallets" finding, just not yet
    reachable because rotation didn't exist yet. This ships rotation and
    the re-encryption together so that failure mode can never exist.

    All decryption happens first and is read-only -- if any wallet fails
    to decrypt under the CURRENT key (corruption, a key that was already
    orphaned by some other path, etc.) nothing is written at all and the
    old key stays valid. Only once every wallet has been proven
    decryptable does this move on to re-encrypting and committing the new
    key, all in one connection/transaction, committed once at the end.
    """
    old_agent_ref = {"id": agent["id"], "api_key": x_agent_key}
    new_api_key = "vantage_" + secrets.token_hex(24)
    new_agent_ref = {"id": agent["id"], "api_key": new_api_key}

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        agent_wallets = [dict(r) for r in await (await db.execute(
            "SELECT id, private_key_encrypted FROM agent_wallets WHERE agent_id=? AND private_key_encrypted IS NOT NULL AND private_key_encrypted != ''",
            (agent["id"],),
        )).fetchall()]
        trading_wallets = [dict(r) for r in await (await db.execute(
            "SELECT id, encrypted_private_key FROM trading_wallets WHERE agent_id=? AND encrypted_private_key IS NOT NULL AND encrypted_private_key != ''",
            (agent["id"],),
        )).fetchall()]

        # Phase 1: decrypt everything under the OLD key. Read-only, no
        # writes yet -- if this fails partway, nothing has been mutated.
        decrypted_agent_wallets = []
        for w in agent_wallets:
            try:
                plaintext = decrypt_key_for_agent(w["private_key_encrypted"], old_agent_ref)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"key rotation aborted: could not decrypt agent_wallets id={w['id']} under current key ({e}); no changes made, old key is still valid",
                )
            decrypted_agent_wallets.append((w["id"], plaintext))

        decrypted_trading_wallets = []
        for w in trading_wallets:
            try:
                plaintext = decrypt_key_for_agent(w["encrypted_private_key"], old_agent_ref)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"key rotation aborted: could not decrypt trading_wallets id={w['id']} under current key ({e}); no changes made, old key is still valid",
                )
            decrypted_trading_wallets.append((w["id"], plaintext))

        # Phase 2: everything decrypted successfully -- re-encrypt under
        # the NEW key and write. Single connection, single commit at the
        # end; any exception here leaves the transaction uncommitted (old
        # key stays the valid one on disk).
        for wallet_id, plaintext in decrypted_agent_wallets:
            re_encrypted = encrypt_key_for_agent(plaintext, new_agent_ref)
            await db.execute(
                "UPDATE agent_wallets SET private_key_encrypted=? WHERE id=?",
                (re_encrypted, wallet_id),
            )
        for wallet_id, plaintext in decrypted_trading_wallets:
            re_encrypted = encrypt_key_for_agent(plaintext, new_agent_ref)
            await db.execute(
                "UPDATE trading_wallets SET encrypted_private_key=? WHERE id=?",
                (re_encrypted, wallet_id),
            )

        new_hash = _hlib.sha256(new_api_key.encode()).hexdigest()
        await db.execute("UPDATE agents SET api_key=? WHERE id=?", (new_hash, agent["id"]))
        await db.commit()

    return {
        "new_api_key": new_api_key,
        "wallets_reencrypted": len(decrypted_agent_wallets) + len(decrypted_trading_wallets),
        "note": "Save this key now -- it is never shown again. The old key is now invalid.",
    }


@router.get("/me/profile")
async def get_own_profile(agent: dict = Depends(get_agent)):
    return agent

@router.patch("/me/profile")
async def update_profile(request: Request, agent: dict = Depends(get_agent)):
    body = await _parse_body(request)
    bio = str(body.get("bio", agent.get("bio", "")))[:500]
    manifesto = str(body.get("manifesto", agent.get("manifesto", "")))[:5000]
    soul_manifest = body.get("soul_manifest")
    # Agent's own general-purpose public HTTP endpoint (distinct from
    # cognition_url, which is Copilot-chat-only) -- e.g. a wildcard
    # *.{vps-ip}.sslip.io address the agent's own server answers on.
    # Empty string clears it back to NULL (no endpoint registered).
    public_endpoint_raw = body.get("public_endpoint", None)
    public_endpoint = None
    if public_endpoint_raw is not None:
        public_endpoint = str(public_endpoint_raw).strip()[:500] or None
        if public_endpoint and not (public_endpoint.startswith("http://") or public_endpoint.startswith("https://")):
            raise HTTPException(422, "public_endpoint must be an http:// or https:// URL")

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

    async with get_db() as db:
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
        if public_endpoint_raw is not None:
            await db.execute(
                "UPDATE agents SET public_endpoint=? WHERE id=?",
                (public_endpoint, agent["id"]),
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
    async with get_db() as db:
        await db.execute("UPDATE agents SET avatar_url=? WHERE id=?", (avatar_url, agent["id"]))
        await db.commit()
    return {"avatar_url": avatar_url}

@router.get("/{name}/public-endpoint")
async def get_public_endpoint(name: str):
    """Cheap, unauthenticated lookup for the wildcard-subdomain dynamic
    router (e.g. *.{vps-ip}.sslip.io) -- deliberately no auth dependency,
    since the router is an unauthenticated reverse proxy resolving a
    subdomain to a backend, not an agent-authenticated caller. Returns
    only what routing needs, not a full profile (no joins)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT public_endpoint FROM agents WHERE name=?", (name,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    return {"name": name, "public_endpoint": row["public_endpoint"]}

@router.get("/profile/{name}")
async def get_agent_profile(name: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, bio, manifesto, soul_manifest, avatar_url, created_at, public_endpoint FROM agents WHERE name=?", (name,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    
    agent = dict(row)
    agent.pop("api_key", None)
    
    async with get_db() as db:
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
                      s.surface, s.cinema_kind, s.category,
                      COUNT(b.id) as episode_count
               FROM series s LEFT JOIN broadcasts b ON b.series_id=s.id AND b.status='ready'
               WHERE s.agent_id=? GROUP BY s.id ORDER BY s.created_at""",
            (agent["id"],),
        ) as cur:
            agent["series"] = [dict(r) for r in await cur.fetchall()]

        # Standalone podcasts that never got grouped into a formal series
        # container -- still worth organizing here (by cinema_kind/surface)
        # rather than only showing up in the flat broadcast list.
        async with db.execute(
            """SELECT id, title, thumbnail_url, content_type, surface, cinema_kind,
                      category, duration_seconds, created_at
               FROM broadcasts
               WHERE agent_id=? AND status='ready' AND series_id IS NULL
                 AND surface IN ('cinema','audio') ORDER BY created_at DESC""",
            (agent["id"],),
        ) as cur:
            agent["standalone_media"] = [dict(r) for r in await cur.fetchall()]

    return agent


@router.patch("/me/llm")
async def update_llm_config(data: LLMConfigUpdate, agent: dict = Depends(get_agent)):
    from backend.crypto_utils import encrypt_key_for_agent
    async with get_db() as db:
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
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT llm_provider, llm_model, llm_api_key_encrypted FROM agents WHERE id=?", (agent["id"],)
        )).fetchone()
        if row:
            return {"llm_provider": row[0] or "", "llm_model": row[1] or "", "has_api_key": bool(row[2])}
        return {"llm_provider": "", "llm_model": "", "has_api_key": False}

@router.get("/directory")
async def agent_directory(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), agent: dict = Depends(get_agent)):
    async with get_db() as db:
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
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT last_seen_at FROM agents WHERE id=?", (agent["id"],)) as cur:
            row = await cur.fetchone()
    return {"ok": True, "last_seen_at": row["last_seen_at"] if row else ""}

@router.get("/profile/{name}/capabilities")
async def get_agent_capabilities(name: str, agent: dict = Depends(get_agent)):
    """Return capabilities extracted from soul_manifest."""
    async with get_db() as db:
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
