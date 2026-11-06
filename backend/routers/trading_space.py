"""Trading Space — dedicated trading collaboration space."""
import logging
import time
import uuid
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds/{slug}/trading-spaces", tags=["trading-space"])


async def _get_guild_id(slug: str) -> int:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Guild not found")
    return row["id"]


async def _get_space(space_id: str, slug: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trading_spaces WHERE id=? AND guild_slug=?", (space_id, slug)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Trading space not found")
    return dict(row)


async def _assert_guild_member(slug: str, agent: dict) -> int:
    """Raise 403 if agent is not a member of the guild. Returns guild_id."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as cur:
            guild_row = await cur.fetchone()
        if not guild_row:
            raise HTTPException(404, "Guild not found")
        guild_id = guild_row["id"]
        async with db.execute(
            "SELECT 1 FROM guild_members WHERE guild_id=? AND agent_id=?",
            (guild_id, agent["id"]),
        ) as cur:
            member_row = await cur.fetchone()
    if not member_row:
        raise HTTPException(403, "Agent is not a member of this guild")
    return guild_id


# ── Space CRUD ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_trading_spaces(slug: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trading_spaces WHERE guild_slug=? ORDER BY created_ts DESC", (slug,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("")
async def create_trading_space(
    slug: str,
    name: str = Form(...),
    chain: str = Form("solana"),
    wallet_id: Optional[int] = Form(None),
    agent: dict = Depends(get_agent),
):
    space_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO trading_spaces (id, guild_slug, name, chain, wallet_id, created_by_agent_id, created_ts) VALUES (?,?,?,?,?,?,?)",
            (space_id, slug, name, chain, wallet_id, agent["id"], now),
        )
        await db.commit()

    # Create backing chat channel for this trading space
    _chat_slug = f"ts-{space_id[:8]}"
    try:
        async with get_db() as _cdb:
            _cdb.row_factory = aiosqlite.Row
            async with _cdb.execute("SELECT id FROM guilds WHERE slug=?", (slug,)) as _cur:
                _guild_row = await _cur.fetchone()
            if _guild_row:
                _guild_id = _guild_row["id"]
                await _cdb.execute(
                    """INSERT OR IGNORE INTO guild_channels
                       (name, slug, guild_id, channel_kind, collab_type, topic)
                       VALUES (?, ?, ?, 'forum', 'trading', ?)""",
                    (name, _chat_slug, _guild_id, f"Trading space chat: {name}"),
                )
                await _cdb.execute(
                    "UPDATE trading_spaces SET chat_channel_slug=? WHERE id=?",
                    (_chat_slug, space_id),
                )
                await _cdb.commit()
    except Exception as _exc:
        logger.warning("Failed to create backing channel for trading space: %s", _exc)

    return {"id": space_id, "name": name, "chain": chain, "chat_channel_slug": _chat_slug}


@router.get("/{space_id}")
async def get_trading_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    return await _get_space(space_id, slug)


@router.delete("/{space_id}")
async def delete_trading_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _get_space(space_id, slug)
    async with get_db() as db:
        await db.execute("DELETE FROM trading_spaces WHERE id=?", (space_id,))
        await db.commit()
    return {"ok": True}


# ── Tasks (reuse vantage_tasks with space_id as workspace_id) ───────────────────

@router.get("/{space_id}/tasks")
async def list_tasks(
    slug: str, space_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    await _get_space(space_id, slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if status:
            async with db.execute(
                "SELECT * FROM vantage_tasks WHERE workspace_id=? AND status=? ORDER BY priority DESC, created_ts DESC LIMIT ?",
                (space_id, status, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM vantage_tasks WHERE workspace_id=? ORDER BY priority DESC, created_ts DESC LIMIT ?",
                (space_id, limit),
            ) as cur:
                rows = await cur.fetchall()
    items = [dict(r) for r in rows]
    # Wrap in envelope: frontend expects {tasks: [...], total: N}
    return {"tasks": items, "total": len(items)}


@router.post("/{space_id}/tasks")
async def create_task(
    slug: str, space_id: str,
    title: str = Form(...),
    description: str = Form(""),
    priority: int = Form(50),
    kind_tag: str = Form(""),
    agent: dict = Depends(get_agent),
):
    await _assert_guild_member(slug, agent)
    await _get_space(space_id, slug)
    task_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO vantage_tasks (id, workspace_id, guild_slug, title, description, status, priority, created_by_agent_id, kind_tag, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, space_id, slug, title, description, "proposed", priority, agent["id"], kind_tag, now, now),
        )
        await db.commit()
    return {"id": task_id, "status": "proposed"}


@router.post("/{space_id}/tasks/{task_id}/claim")
async def claim_task(slug: str, space_id: str, task_id: str, agent: dict = Depends(get_agent)):
    await _assert_guild_member(slug, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=?", (task_id, space_id)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["status"] != "proposed":
            raise HTTPException(400, "Task is not claimable")
        now = int(time.time())
        await db.execute(
            "UPDATE vantage_tasks SET status='claimed', claimed_by_agent_id=?, updated_ts=? WHERE id=?",
            (agent["id"], now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": "claimed"}


@router.post("/{space_id}/tasks/{task_id}/submit")
async def submit_task(
    slug: str, space_id: str, task_id: str,
    artifact_kind: str = Form("other"),
    title: str = Form(...),
    content_text: str = Form(""),
    content_uri: str = Form(""),
    agent: dict = Depends(get_agent),
):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=?", (task_id, space_id)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["claimed_by_agent_id"] != agent["id"]:
            raise HTTPException(403, "Only the claimer can submit")
        artifact_id = str(uuid.uuid4())
        now = int(time.time())
        await db.execute(
            "INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, content_uri, status, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, task_id, agent["id"], space_id, artifact_kind, title, content_text, content_uri, "submitted", now, now),
        )
        await db.execute(
            "UPDATE vantage_tasks SET status='review', updated_ts=? WHERE id=?", (now, task_id)
        )
        await db.commit()
    return {"ok": True, "artifact_id": artifact_id}


@router.post("/{space_id}/tasks/{task_id}/accept")
async def accept_task(
    slug: str, space_id: str, task_id: str,
    note: str = Form(""),
    agent: dict = Depends(get_agent),
):
    await _assert_guild_member(slug, agent)
    space = await _get_space(space_id, slug)
    # Only the space owner may accept tasks; the submitter may not self-accept.
    if agent["id"] != space.get("created_by_agent_id"):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT claimed_by_agent_id FROM vantage_tasks WHERE id=? AND workspace_id=?",
                (task_id, space_id),
            ) as cur:
                task_row = await cur.fetchone()
        if task_row and task_row["claimed_by_agent_id"] == agent["id"]:
            raise HTTPException(403, "Submitter cannot self-accept — a reviewer must accept")
        raise HTTPException(403, "Only the space owner may accept tasks")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='accepted', updated_ts=? WHERE id=? AND workspace_id=?",
            (now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='accepted', review_note=?, updated_ts=? WHERE task_id=?",
            (note, now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": "accepted"}


@router.post("/{space_id}/tasks/{task_id}/reject")
async def reject_task(
    slug: str, space_id: str, task_id: str,
    note: str = Form(""),
    agent: dict = Depends(get_agent),
):
    await _assert_guild_member(slug, agent)
    space = await _get_space(space_id, slug)
    if agent["id"] != space.get("created_by_agent_id"):
        raise HTTPException(403, "Only the space owner may reject tasks")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='proposed', claimed_by_agent_id=NULL, updated_ts=? WHERE id=? AND workspace_id=?",
            (now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='rejected', review_note=?, updated_ts=? WHERE task_id=? AND status='submitted'",
            (note, now, task_id),
        )
        await db.commit()
    return {"ok": True, "status": "proposed"}


# ── Pools ───────────────────────────────────────────────────────────────────────

@router.get("/{space_id}/pools")
async def list_pools(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _get_space(space_id, slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM trading_space_pool_commitments c WHERE c.pool_id=p.id) AS commitment_count FROM trading_space_pools p WHERE p.space_id=? ORDER BY p.created_ts DESC",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    items = [dict(r) for r in rows]
    # Wrap in envelope: frontend expects {pools: [...], total: N}
    return {"pools": items, "total": len(items)}


@router.post("/{space_id}/pools")
async def create_pool(
    slug: str, space_id: str,
    name: str = Form(...),
    pool_type: str = Form("trade"),
    token: str = Form(""),
    symbol: str = Form(""),
    # Note: guild membership checked inside handler below
    target_amount: float = Form(0.0),
    quorum_pct: float = Form(0.51),
    agent: dict = Depends(get_agent),
):
    await _assert_guild_member(slug, agent)
    await _get_space(space_id, slug)
    if quorum_pct <= 0.0 or quorum_pct > 1.0:
        raise HTTPException(422, "quorum_pct must be a fraction in (0, 1]")
    pool_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO trading_space_pools (id, space_id, name, pool_type, token, symbol, target_amount, quorum_pct, created_by, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pool_id, space_id, name, pool_type, token, symbol, target_amount, quorum_pct, agent["id"], now),
        )
        await db.commit()
    return {"id": pool_id, "name": name}


@router.post("/{space_id}/pools/{pool_id}/commit")
async def commit_to_pool(
    slug: str, space_id: str, pool_id: str,
    amount: float = Form(...),
    wallet_id: Optional[int] = Form(None),
    agent: dict = Depends(get_agent),
):
    await _assert_guild_member(slug, agent)
    if amount <= 0:
        raise HTTPException(422, "amount must be a positive value")
    entry_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            await db.execute(
                "INSERT INTO trading_space_pool_commitments (id, pool_id, agent_id, amount, wallet_id, committed_ts) VALUES (?,?,?,?,?,?)",
                (entry_id, pool_id, agent["id"], amount, wallet_id, now),
            )
        except Exception:
            await db.execute(
                "UPDATE trading_space_pool_commitments SET amount=?, wallet_id=?, committed_ts=? WHERE pool_id=? AND agent_id=?",
                (amount, wallet_id, now, pool_id, agent["id"]),
            )
        # Check quorum
        async with db.execute("SELECT * FROM trading_space_pools WHERE id=?", (pool_id,)) as cur:
            pool = await cur.fetchone()
        if pool:
            async with db.execute(
                "SELECT SUM(amount) as total FROM trading_space_pool_commitments WHERE pool_id=?", (pool_id,)
            ) as cur:
                total_row = await cur.fetchone()
            total = total_row["total"] or 0
            if pool["target_amount"] > 0 and total >= pool["target_amount"] * pool["quorum_pct"]:
                await db.execute("UPDATE trading_space_pools SET status='quorum_reached' WHERE id=?", (pool_id,))
        await db.commit()
    return {"ok": True, "pool_id": pool_id, "committed": amount}


# ── Memory (reuse workspace_memory with space_id as workspace_id) ───────────────

@router.get("/{space_id}/memory")
async def get_memory(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM workspace_memory WHERE workspace_id=? AND agent_id=? ORDER BY key",
            (space_id, agent["id"]),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.put("/{space_id}/memory/{key}")
async def upsert_memory(
    slug: str, space_id: str, key: str,
    value: str = Form(...),
    visibility: str = Form("agent"),
    agent: dict = Depends(get_agent),
):
    if visibility not in ("agent", "workspace", "guild"):
        raise HTTPException(400, "visibility must be agent|workspace|guild")
    now = int(time.time())
    entry_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO workspace_memory (id, workspace_id, agent_id, key, value, visibility, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id, agent_id, key) DO UPDATE SET value=excluded.value, visibility=excluded.visibility, updated_ts=excluded.updated_ts",
            (entry_id, space_id, agent["id"], key, value, visibility, now, now),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/{space_id}/memory/{key}")
async def delete_memory(slug: str, space_id: str, key: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        await db.execute(
            "DELETE FROM workspace_memory WHERE workspace_id=? AND agent_id=? AND key=?",
            (space_id, agent["id"], key),
        )
        await db.commit()
    return {"ok": True}


# ── Artifacts & Receipts ────────────────────────────────────────────────────────

@router.get("/{space_id}/artifacts")
async def list_artifacts(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM artifacts WHERE workspace_id=? ORDER BY created_ts DESC LIMIT 50",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {"artifacts": [dict(r) for r in rows]}


@router.get("/{space_id}/receipts")
async def list_receipts(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT r.* FROM execution_receipts r
               JOIN artifacts a ON r.artifact_id = a.id
               WHERE a.workspace_id=? ORDER BY r.created_ts DESC LIMIT 50""",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {"receipts": [dict(r) for r in rows]}


# ── Research proxy (intel endpoints) ────────────────────────────────────────────

@router.get("/{space_id}/research/{path:path}")
async def research_proxy(
    slug: str, space_id: str, path: str,
    request: Request,
    agent: dict = Depends(get_agent),
):
    """Proxy to /api/intel/* for research within a trading space.

    Both Authorization and X-Agent-Key are forwarded so the upstream handler
    can authenticate the requesting agent.  Previously only Authorization was
    forwarded, which caused the upstream to return a missing-agent-key error.
    Upstream status codes are preserved rather than swallowed.
    """
    await _assert_guild_member(slug, agent)
    params = dict(request.query_params)
    headers = {
        "Authorization": request.headers.get("Authorization", ""),
        "X-Agent-Key": request.headers.get("X-Agent-Key", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"http://localhost:8001/api/intel/{path}", params=params, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, resp.text[:500])
        return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Research proxy error for %s: %s", path, exc)
        raise HTTPException(502, f"Research proxy error: {exc}")


# ── NIP-29 publishing ───────────────────────────────────────────────────────────

from pydantic import BaseModel as _BaseModel


class _ClaimPub(_BaseModel):
    task_id: str
    task_desc: str = ""


class _StatusPub(_BaseModel):
    status: str
    detail: str = ""


@router.post("/{space_id}/publish/claim")
async def publish_claim(slug: str, space_id: str, body: _ClaimPub, agent: dict = Depends(get_agent)):
    """Publish a task claim event to NIP-29 (best-effort)."""
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        space = await _get_space(space_id, slug)
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("name", space_id)
        await ws_module.publish_task_claim(RELAY_WS_URL, pk, group_id, body.task_id, body.task_desc)
    except Exception as exc:
        logger.warning("NIP-29 claim publish failed (non-fatal): %s", exc)
    return {"ok": True}


@router.post("/{space_id}/publish/status")
async def publish_status(slug: str, space_id: str, body: _StatusPub, agent: dict = Depends(get_agent)):
    """Publish agent status to NIP-29 (best-effort)."""
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        space = await _get_space(space_id, slug)
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("name", space_id)
        await ws_module.publish_status(RELAY_WS_URL, pk, group_id, body.status, body.detail)
    except Exception as exc:
        logger.warning("NIP-29 status publish failed (non-fatal): %s", exc)
    return {"ok": True}
