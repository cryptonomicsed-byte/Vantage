"""Trading Space — dedicated trading collaboration space.

Every route, reads included, requires membership of the guild that owns the
space (G0). Deleting a space takes its owner or guild staff. Accepting or
rejecting delivered work takes a reviewer, never the claimant.
"""
import logging
import time
import uuid
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..db import get_db
from ..deps import get_agent
from ..space_provisioning import provision_space_channel
from ..work_states import envelope
from ..task_events import emit_for_space
from ..guild_access import (
    require_member, require_not_claimant, require_owner_or_staff, require_space_capability,
)
from ..space_tasks import (
    DEFAULT_LEASE_MINUTES, MAX_LEASE_MINUTES, claim_sql, clamped_lease, lease_expired_sql, submit_sql,
)

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
    return (await require_member(slug, agent))["guild_id"]


async def _member_space(slug: str, space_id: str, agent: dict) -> dict:
    """Membership first, then the space: a non-member learns nothing about
    which space ids exist."""
    await require_member(slug, agent)
    return await _get_space(space_id, slug)


# ── Space CRUD ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_trading_spaces(slug: str, agent: dict = Depends(get_agent)):
    await require_member(slug, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM trading_spaces WHERE guild_slug=? ORDER BY created_ts DESC", (slug,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class CreateTradingSpaceBody(BaseModel):
    name: str
    chain: str = Field("solana")
    wallet_id: Optional[int] = Field(None)


@router.post("")
async def create_trading_space(
    body: CreateTradingSpaceBody,
    slug: str,
    agent: dict = Depends(get_agent)
):
    name = body.name
    chain = body.chain
    wallet_id = body.wallet_id
    await require_member(slug, agent)
    space_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO trading_spaces (id, guild_slug, name, chain, wallet_id, created_by_agent_id, created_ts) VALUES (?,?,?,?,?,?,?)",
            (space_id, slug, name, chain, wallet_id, agent["id"], now),
        )
        await db.commit()

    # G4: the backing chat channel goes through the coordination layer so it
    # exists on the relay too, and carries the space it belongs to.
    prov = await provision_space_channel(slug=slug, kind="trading", space_id=space_id, name=name, agent=agent)
    _chat_slug = prov["chat_channel_slug"]

    return {"id": space_id, "name": name, "chain": chain, "chat_channel_slug": _chat_slug,
            "relay_provisioned": prov["relay_provisioned"], "note": prov["note"]}


@router.get("/{space_id}")
async def get_trading_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    return await _member_space(slug, space_id, agent)


@router.delete("/{space_id}")
async def delete_trading_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    space = await _member_space(slug, space_id, agent)
    await require_owner_or_staff(slug, agent, space.get("created_by_agent_id"), "this trading space")
    async with get_db() as db:
        await db.execute("DELETE FROM trading_spaces WHERE id=?", (space_id,))
        await db.commit()
    return {"ok": True}


@router.post("/{space_id}/provision")
async def provision_trading_space(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    """Retry channel and relay provisioning for this space. Idempotent;
    owner or guild staff."""
    space = await _member_space(slug, space_id, agent)
    await require_owner_or_staff(slug, agent, space.get("created_by_agent_id"), "this trading space")
    return await provision_space_channel(slug=slug, kind="trading", space_id=space_id,
                                         name=space["name"], agent=agent)


# ── Tasks (reuse vantage_tasks with space_id as workspace_id) ───────────────────

@router.get("/{space_id}/tasks")
async def list_tasks(
    slug: str, space_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(get_agent),
):
    await _member_space(slug, space_id, agent)
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
    # One envelope (G3): {items, total}; "tasks" stays for the current UI.
    return envelope("vtask", [dict(r) for r in rows], legacy_key="tasks")


class CreateTaskBody(BaseModel):
    title: str
    description: str = Field("")
    priority: int = Field(50)
    kind_tag: str = Field("")


@router.post("/{space_id}/tasks")
async def create_task(
    body: CreateTaskBody,
    slug: str, space_id: str,
    agent: dict = Depends(get_agent)
):
    title = body.title
    description = body.description
    priority = body.priority
    kind_tag = body.kind_tag
    space = await _member_space(slug, space_id, agent)
    await require_space_capability(slug, space, agent, "post")
    task_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO vantage_tasks (id, workspace_id, guild_slug, title, description, status, priority, created_by_agent_id, kind_tag, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, space_id, slug, title, description, "proposed", priority, agent["id"], kind_tag, now, now),
        )
        await db.commit()
    await emit_for_space(guild_slug=slug, table="trading_spaces", space_id=space_id, event="created",
                         kind="vtask", ref_id=task_id, actor=agent, title=title)
    return {"id": task_id, "status": "proposed"}


# P1-12: a claim is a lease. The rule lives in backend/space_tasks.py and is
# shared with the content space and the MCP tool router.
def _clamped_lease(minutes: int) -> int:
    return clamped_lease(minutes)


def _lease_expired_sql() -> str:
    return lease_expired_sql()


@router.post("/{space_id}/tasks/{task_id}/claim")
async def claim_task(slug: str, space_id: str, task_id: str,
                     lease_minutes: int = Query(DEFAULT_LEASE_MINUTES),
                     agent: dict = Depends(get_agent)):
    # G2: an observer on this space can read the board but not take work.
    space = await _member_space(slug, space_id, agent)
    await require_space_capability(slug, space, agent, "claim")
    lease = _clamped_lease(lease_minutes)
    now = int(time.time())
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            claim_sql(open_status="proposed", claimed_status="claimed", lease_minutes=lease),
            ("claimed", agent["id"], now, task_id, space_id, slug, agent["id"]),
        )
        await db.commit()
        async with db.execute(
            "SELECT status, claimed_by_agent_id, claim_expires_at FROM vantage_tasks "
            "WHERE id=? AND workspace_id=? AND guild_slug=?", (task_id, space_id, slug)) as c:
            row = await c.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    if cur.rowcount == 0:
        if row["status"] == "claimed":
            raise HTTPException(409, f"Task is claimed by another agent until {row['claim_expires_at']}")
        raise HTTPException(400, "Task is not claimable")
    await emit_for_space(guild_slug=slug, table="trading_spaces", space_id=space_id, event="claimed",
                         kind="vtask", ref_id=task_id, actor=agent, note=f"lease until {row['claim_expires_at']}")
    return {"ok": True, "status": "claimed", "claim_expires_at": row["claim_expires_at"]}


@router.post("/{space_id}/tasks/{task_id}/heartbeat")
async def heartbeat_task(slug: str, space_id: str, task_id: str,
                         lease_minutes: int = Query(DEFAULT_LEASE_MINUTES),
                         agent: dict = Depends(get_agent)):
    """Renew the lease on a claim you hold. 403 if you do not hold it."""
    await _assert_guild_member(slug, agent)
    lease = _clamped_lease(lease_minutes)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""UPDATE vantage_tasks
                SET claim_expires_at=datetime('now', '+{lease} minutes'), updated_ts=?
                WHERE id=? AND workspace_id=? AND guild_slug=?
                  AND status='claimed' AND claimed_by_agent_id=?""",
            (int(time.time()), task_id, space_id, slug, agent["id"]),
        )
        await db.commit()
        async with db.execute(
            "SELECT claim_expires_at FROM vantage_tasks WHERE id=? AND workspace_id=? AND guild_slug=?",
            (task_id, space_id, slug)) as c:
            row = await c.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    if cur.rowcount == 0:
        raise HTTPException(403, "You do not hold a claim on this task")
    return {"ok": True, "status": "claimed", "claim_expires_at": row["claim_expires_at"]}


class SubmitTaskBody(BaseModel):
    artifact_kind: str = Field("other")
    title: str
    content_text: str = Field("")
    content_uri: str = Field("")


@router.post("/{space_id}/tasks/{task_id}/submit")
async def submit_task(
    body: SubmitTaskBody,
    slug: str, space_id: str, task_id: str,
    agent: dict = Depends(get_agent)
):
    # P1-13: membership first (a removed member's old claim no longer lets
    # them submit); the task must belong to this space AND this guild; it must
    # still be claimed, by the caller, under an unexpired lease. The status
    # move is one conditional UPDATE, so a lease that expires (and is taken by
    # someone else) between check and write cannot slip a submission through.
    artifact_kind = body.artifact_kind
    title = body.title
    content_text = body.content_text
    content_uri = body.content_uri
    await _assert_guild_member(slug, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=? AND guild_slug=?",
                              (task_id, space_id, slug)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["claimed_by_agent_id"] != agent["id"]:
            raise HTTPException(403, "Only the claimer can submit")
        if row["status"] != "claimed":
            raise HTTPException(409, f"Task is {row['status']!r}, not claimed; nothing to submit against")
        artifact_id = str(uuid.uuid4())
        now = int(time.time())
        cur = await db.execute(
            submit_sql(claimed_status="claimed", review_status="review"),
            (now, task_id, space_id, slug, agent["id"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            raise HTTPException(409, "Your claim's lease has expired; claim the task again before submitting")
        await db.execute(
            "INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, content_uri, status, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, task_id, agent["id"], space_id, artifact_kind, title, content_text, content_uri, "submitted", now, now),
        )
        await db.commit()
    await emit_for_space(guild_slug=slug, table="trading_spaces", space_id=space_id, event="delivered",
                         kind="vtask", ref_id=task_id, actor=agent, title=title, note=f"artifact {artifact_id}")
    return {"ok": True, "artifact_id": artifact_id}


async def _task_for_review(slug: str, space_id: str, task_id: str, agent: dict) -> dict:
    """The task, once the caller is confirmed as a reviewer: a guild member
    who is the space owner or guild staff, and not the claimant.

    The claimant check runs first so the space owner cannot accept work they
    claimed themselves -- the self-accept hole the audit found."""
    space = await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vantage_tasks WHERE id=? AND workspace_id=? AND guild_slug=?",
            (task_id, space_id, slug),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    task = dict(row)
    require_not_claimant(task, agent)
    # G2: a reviewer holds `review` on this space -- the owner (lead), guild
    # staff (maintainer) or a member given operator or above on the space's
    # channel roster. A plain contributor cannot accept.
    await require_space_capability(slug, space, agent, "review")
    return task


class AcceptTaskBody(BaseModel):
    note: str = Field("")


@router.post("/{space_id}/tasks/{task_id}/accept")
async def accept_task(
    slug: str, space_id: str, task_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[AcceptTaskBody] = None,
):
    body = body or AcceptTaskBody()
    note = body.note
    task = await _task_for_review(slug, space_id, task_id, agent)
    if task["status"] != "review":
        raise HTTPException(409, f"Task is {task['status']!r}, not in review; nothing to accept")
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
    await emit_for_space(guild_slug=slug, table="trading_spaces", space_id=space_id, event="accepted",
                         kind="vtask", ref_id=task_id, actor=agent, title=task["title"], note=note)
    return {"ok": True, "status": "accepted"}


class RejectTaskBody(BaseModel):
    note: str = Field("")


@router.post("/{space_id}/tasks/{task_id}/reject")
async def reject_task(
    slug: str, space_id: str, task_id: str,
    agent: dict = Depends(get_agent),
    body: Optional[RejectTaskBody] = None,
):
    body = body or RejectTaskBody()
    note = body.note
    task = await _task_for_review(slug, space_id, task_id, agent)
    if task["status"] not in ("claimed", "review"):
        raise HTTPException(409, f"Task is {task['status']!r}; only claimed or in-review work can be rejected")
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "UPDATE vantage_tasks SET status='proposed', claimed_by_agent_id=NULL, claim_expires_at=NULL, updated_ts=? WHERE id=? AND workspace_id=?",
            (now, task_id, space_id),
        )
        await db.execute(
            "UPDATE artifacts SET status='rejected', review_note=?, updated_ts=? WHERE task_id=? AND status='submitted'",
            (note, now, task_id),
        )
        await db.commit()
    await emit_for_space(guild_slug=slug, table="trading_spaces", space_id=space_id, event="rejected",
                         kind="vtask", ref_id=task_id, actor=agent, title=task["title"], note=note)
    return {"ok": True, "status": "proposed"}


# ── Pools ───────────────────────────────────────────────────────────────────────

@router.get("/{space_id}/pools")
async def list_pools(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM trading_space_pool_commitments c WHERE c.pool_id=p.id) AS commitment_count FROM trading_space_pools p WHERE p.space_id=? ORDER BY p.created_ts DESC",
            (space_id,),
        ) as cur:
            rows = await cur.fetchall()
    items = [dict(r) for r in rows]
    return {"items": items, "pools": items, "total": len(items)}


class CreatePoolBody(BaseModel):
    name: str
    pool_type: str = Field("trade")
    token: str = Field("")
    symbol: str = Field("")
    target_amount: float = Field(0.0)
    quorum_pct: float = Field(0.51)


@router.post("/{space_id}/pools")
async def create_pool(
    body: CreatePoolBody,
    slug: str, space_id: str,
    agent: dict = Depends(get_agent)
):
    name = body.name
    pool_type = body.pool_type
    token = body.token
    symbol = body.symbol
    target_amount = body.target_amount
    quorum_pct = body.quorum_pct
    await _member_space(slug, space_id, agent)
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


class CommitToPoolBody(BaseModel):
    amount: float
    wallet_id: Optional[int] = Field(None)


@router.post("/{space_id}/pools/{pool_id}/commit")
async def commit_to_pool(
    body: CommitToPoolBody,
    slug: str, space_id: str, pool_id: str,
    agent: dict = Depends(get_agent)
):
    amount = body.amount
    wallet_id = body.wallet_id
    await _member_space(slug, space_id, agent)
    if amount <= 0:
        raise HTTPException(422, "amount must be a positive value")
    entry_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trading_space_pools WHERE id=? AND space_id=?", (pool_id, space_id)) as cur:
            pool = await cur.fetchone()
        if not pool:
            raise HTTPException(404, "Pool not found")
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
        async with db.execute(
            "SELECT SUM(amount) as total FROM trading_space_pool_commitments WHERE pool_id=?", (pool_id,)
        ) as cur:
            total_row = await cur.fetchone()
        total = total_row["total"] or 0
        if pool["target_amount"] > 0 and total >= pool["target_amount"] * pool["quorum_pct"]:
            await db.execute("UPDATE trading_space_pools SET status='quorum_reached' WHERE id=?", (pool_id,))
        await db.commit()
    return {"ok": True, "pool_id": pool_id, "committed": amount}


# ── Orders and strategies, scoped to the space's guild (G9) ─────────────────────
#
# The Orders and Strategies tabs used to read the caller's personal
# /api/trading/orders and /strategies, so a guild tab showed one agent's
# private book. These list what the guild's members run, paper only, and
# nothing here can place, arm or enable anything: the live path stays
# outside the guild by design (R-123).

@router.get("/{space_id}/orders")
async def space_orders(slug: str, space_id: str, limit: int = Query(50, ge=1, le=200),
                       agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    guild_id = await _get_guild_id(slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT o.*, a.name AS agent_name FROM trading_orders o
                 JOIN guild_members gm ON gm.agent_id = o.agent_id AND gm.guild_id = ?
                 LEFT JOIN agents a ON a.id = o.agent_id
                WHERE COALESCE(o.tx_hash, '') = ''  -- no on-chain transaction: paper
                ORDER BY o.created_at DESC LIMIT ?""", (guild_id, limit)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"items": rows, "orders": rows, "total": len(rows), "paper_only": True}


@router.get("/{space_id}/strategies")
async def space_strategies(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    await _member_space(slug, space_id, agent)
    guild_id = await _get_guild_id(slug)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT s.id, s.agent_id, a.name AS agent_name, s.name, s.created_at,
                      COALESCE(s.armed, 0) AS armed, COALESCE(s.live, 0) AS live
                 FROM trading_strategies s
                 JOIN guild_members gm ON gm.agent_id = s.agent_id AND gm.guild_id = ?
                 LEFT JOIN agents a ON a.id = s.agent_id
                ORDER BY s.created_at DESC LIMIT 200""", (guild_id,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"items": rows, "strategies": rows, "total": len(rows), "paper_only": True}


# ── Memory (reuse workspace_memory with space_id as workspace_id) ───────────────

@router.get("/{space_id}/memory")
async def get_memory(slug: str, space_id: str, agent: dict = Depends(get_agent)):
    """Your own rows, plus rows other members shared with the space or guild."""
    await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM workspace_memory
                WHERE workspace_id=? AND (agent_id=? OR visibility IN ('workspace', 'guild'))
                ORDER BY key""",
            (space_id, agent["id"]),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class UpsertMemoryBody(BaseModel):
    value: str
    visibility: str = Field("agent")


@router.put("/{space_id}/memory/{key}")
async def upsert_memory(
    body: UpsertMemoryBody,
    slug: str, space_id: str, key: str,
    agent: dict = Depends(get_agent)
):
    value = body.value
    visibility = body.visibility
    await _member_space(slug, space_id, agent)
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
    await _member_space(slug, space_id, agent)
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
    await _member_space(slug, space_id, agent)
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
    await _member_space(slug, space_id, agent)
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

RESEARCH_ALIASES = {
    "alpha": "alpha",
    "signals": "signals",
    "wallet": "wallet-labels",
    "degen": "degen/top5",
    "narratives": "narratives/hot",
    "watch": "watchlist",
}

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
    await _member_space(slug, space_id, agent)
    # G9: the Research tab's sub-tabs are names, not intel paths. Four of
    # six 404'd because they were proxied verbatim; this maps each name to
    # the route that serves it. A full path still passes through unchanged.
    path = RESEARCH_ALIASES.get(path.strip("/"), path)
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
    """Publish a task claim event to NIP-29.

    The task must exist in this space and be held by the caller: a relay
    event claiming work that was never claimed here is a lie, and the old
    route told it with ``ok: true``. The relay publish itself stays
    best-effort; the response says whether it happened.
    """
    space = await _member_space(slug, space_id, agent)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status, claimed_by_agent_id FROM vantage_tasks WHERE id=? AND workspace_id=? AND guild_slug=?",
            (body.task_id, space_id, slug),
        ) as cur:
            task = await cur.fetchone()
    if not task:
        raise HTTPException(404, "Task not found in this space")
    if task["claimed_by_agent_id"] != agent["id"]:
        raise HTTPException(403, "You do not hold a claim on this task")
    published = False
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("chat_channel_slug") or space_id
        await ws_module.publish_task_claim(RELAY_WS_URL, pk, group_id, body.task_id, body.task_desc)
        published = True
    except Exception as exc:
        logger.warning("NIP-29 claim publish failed (non-fatal): %s", exc)
    return {"ok": True, "published": published}


@router.post("/{space_id}/publish/status")
async def publish_status(slug: str, space_id: str, body: _StatusPub, agent: dict = Depends(get_agent)):
    """Publish agent status to NIP-29 (best-effort)."""
    space = await _member_space(slug, space_id, agent)
    published = False
    try:
        from ..nostr import workspace as ws_module
        from ..buzz_registration import RELAY_WS_URL
        from ..buzz_identity import derive_buzz_keypair
        pk = await derive_buzz_keypair(agent["id"])
        group_id = space.get("chat_channel_slug") or space_id
        await ws_module.publish_status(RELAY_WS_URL, pk, group_id, body.status, body.detail)
        published = True
    except Exception as exc:
        logger.warning("NIP-29 status publish failed (non-fatal): %s", exc)
    return {"ok": True, "published": published}
