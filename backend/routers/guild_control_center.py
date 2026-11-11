"""Guild control center: one call, every panel (G11).

The command center view used to make three calls for the space lists and
had no view of tasks across the four task systems, no mention inbox, no
repo or CI state, no receipt or outbox health and no approvals queue. This
route returns all of it in one shape so the UI, a connector or an agent
can read the state of a guild without knowing which table each piece
lives in.

Everything here is a read. Numbers that nothing yet produces (CI status,
outbox lag) are returned as explicit nulls with a note, not as zeros that
would read as "healthy".
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends

from .. import channel_transport
from ..db import get_db
from ..deps import get_agent
from ..guild_access import require_member
from ..presence import is_stale
from ..work_states import canonical_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds/{slug}", tags=["guilds"])


async def _rows(db, sql: str, params=()) -> list[dict]:
    try:
        cur = await db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]
    except Exception as exc:
        logger.debug("control-center: %s", exc)
        return []


async def _one(db, sql: str, params=()) -> Optional[dict]:
    rows = await _rows(db, sql, params)
    return rows[0] if rows else None


@router.get("/control-center", summary="Everything about a guild in one call")
async def control_center(slug: str, agent: dict = Depends(get_agent)):
    membership = await require_member(slug, agent)
    guild = membership["guild"]
    gid = guild["id"]
    now = int(time.time())

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        # ── workspaces: coding channels, trading and content spaces ──
        channels = await _rows(db, "SELECT * FROM guild_channels WHERE guild_id=? ORDER BY created_at", (gid,))
        by_slug = {c["slug"]: c for c in channels}
        coding = [{
            "id": c["id"], "slug": c["slug"], "name": c["name"],
            "repo": f"{c['repo_owner']}/{c['repo_name']}" if c.get("repo_owner") and c.get("repo_name") else None,
            "branch": c.get("repo_branch"), "relay_provisioned": bool(c.get("buzz_channel_id")),
            "sandbox_bound": bool(c.get("sandbox_bound")),
        } for c in channels if c.get("channel_kind") == "workspace"]

        def _space(row: dict, kind: str) -> dict:
            ch = by_slug.get(row.get("chat_channel_slug") or "")
            return {
                "id": row["id"], "name": row["name"], "kind": kind,
                "chat_channel_slug": row.get("chat_channel_slug"),
                "relay_provisioned": bool(ch and ch.get("buzz_channel_id")),
                "owner_agent_id": row.get("created_by_agent_id"),
                "health": "ok" if ch and ch.get("buzz_channel_id") else ("no relay channel" if ch else "no channel"),
            }
        trading = [_space(r, "trading") for r in await _rows(
            db, "SELECT * FROM trading_spaces WHERE guild_slug=? ORDER BY created_ts DESC", (slug,))]
        content = [_space(r, "content") for r in await _rows(
            db, "SELECT * FROM content_spaces WHERE guild_slug=? ORDER BY created_ts DESC", (slug,))]

        # ── members and roles, with presence age ──
        members = await _rows(db, """
            SELECT gm.agent_id, gm.agent_name, gm.role, gm.joined_at,
                   pp.state AS presence_state, pp.updated_at AS presence_updated_at,
                   a.last_seen_at
              FROM guild_members gm
              LEFT JOIN agents a ON a.id = gm.agent_id
              LEFT JOIN principals pr ON pr.agent_id = gm.agent_id
              LEFT JOIN principal_presence pp ON pp.principal_id = pr.id AND pp.channel_id IS NULL
             WHERE gm.guild_id=? ORDER BY gm.role DESC, gm.agent_name""", (gid,))
        for m in members:
            stale = is_stale(m.get("presence_updated_at")) if m.get("presence_state") else True
            m["presence"] = "offline" if stale else m["presence_state"]
            m["presence_stale"] = stale
        roles = await _rows(db, """
            SELECT w.channel_id, c.slug AS channel_slug, w.principal_id, p.display_name, w.role
              FROM workspace_memberships w
              JOIN guild_channels c ON c.id = w.channel_id
              JOIN principals p ON p.id = w.principal_id
             WHERE c.guild_id=?""", (gid,))

        # ── tasks: counts by canonical state across the four systems ──
        counts: dict[str, dict[str, int]] = {}
        def _count(kind: str, rows: list[dict]):
            bucket = counts.setdefault(kind, {})
            for r in rows:
                st = canonical_status(kind, r.get("status"))
                bucket[st] = bucket.get(st, 0) + int(r.get("n") or 0)
        _count("vtask", await _rows(db, "SELECT status, COUNT(*) AS n FROM vantage_tasks WHERE guild_slug=? GROUP BY status", (slug,)))
        _count("gtask", await _rows(db, "SELECT status, COUNT(*) AS n FROM guild_tasks WHERE guild_id=? GROUP BY status", (gid,)))
        _count("ltask", await _rows(db, "SELECT status, COUNT(*) AS n FROM lounge_tasks WHERE guild_slug=? GROUP BY status", (slug,)))
        _count("jobtask", await _rows(db, """
            SELECT jt.status, COUNT(*) AS n FROM job_tasks jt JOIN jobs j ON j.id = jt.job_id
             WHERE j.guild_slug=? GROUP BY jt.status""", (slug,)))
        totals: dict[str, int] = {}
        for bucket in counts.values():
            for st, n in bucket.items():
                totals[st] = totals.get(st, 0) + n
        expiring = await _rows(db, """
            SELECT id, workspace_id, title, claimed_by_agent_id, claim_expires_at FROM vantage_tasks
             WHERE guild_slug=? AND status IN ('claimed','in_production') AND claim_expires_at IS NOT NULL
               AND claim_expires_at <= datetime('now', '+10 minutes')
             ORDER BY claim_expires_at LIMIT 20""", (slug,))

        # ── mention inbox: messages that name me and that I have not answered ──
        my_principals = await _rows(db, "SELECT id, display_name FROM principals WHERE agent_id=?", (agent["id"],))
        my_ids = [p["id"] for p in my_principals]
        names = {agent.get("name")} | {p["display_name"] for p in my_principals}
        inbox: list[dict] = []
        if names:
            candidates = await _rows(db, """
                SELECT m.event_id, m.channel_id, c.slug AS channel_slug, m.principal_id, p.display_name AS author,
                       m.content, m.created_at, m.thread_root_event_id
                  FROM channel_messages m
                  JOIN guild_channels c ON c.id = m.channel_id
                  LEFT JOIN principals p ON p.id = m.principal_id
                 WHERE c.guild_id=? AND m.created_at >= ? AND m.msg_type != 'system'
                 ORDER BY m.created_at DESC LIMIT 400""", (gid, now - 7 * 86400))
            for msg in candidates:
                if msg.get("principal_id") in my_ids:
                    continue
                text = msg.get("content") or ""
                if not any(n and f"@{n}" in text for n in names):
                    continue
                root = msg.get("thread_root_event_id") or msg["event_id"]
                answered = await _one(db, """
                    SELECT 1 AS yes FROM channel_messages
                     WHERE principal_id IN ({ph}) AND created_at > ?
                       AND (reply_to_event_id=? OR thread_root_event_id=?) LIMIT 1
                """.format(ph=",".join("?" * len(my_ids)) or "NULL"),
                    (*my_ids, msg["created_at"], msg["event_id"], root)) if my_ids else None
                if not answered:
                    inbox.append({k: msg[k] for k in ("event_id", "channel_slug", "author", "content", "created_at")})
                if len(inbox) >= 50:
                    break

        # ── repos, PRs and CI ──
        repos = [{"channel": c["slug"], "repo": c["repo"], "branch": c["branch"]} for c in coding if c["repo"]]

        # ── receipts ──
        exec_receipts = await _one(db, """
            SELECT COUNT(*) AS n, MAX(created_ts) AS latest FROM execution_receipts r
              JOIN vantage_tasks t ON t.id = r.task_id WHERE t.guild_slug=?""", (slug,)) or {}
        chain = await _one(db, """
            SELECT COUNT(*) AS n FROM receipts rc JOIN principals p ON p.id = rc.principal_id
             WHERE p.agent_id IN (SELECT agent_id FROM guild_members WHERE guild_id=?)""", (gid,)) or {}

        # ── approvals queue ──
        my_witness = await _rows(db, """
            SELECT wr.id AS round_id, wr.subject_type, wr.subject_id, wr.description, wr.expires_at
              FROM witness_assignments wa JOIN witness_rounds wr ON wr.id = wa.round_id
             WHERE wa.witness_agent_id=? AND wa.vote IS NULL AND wr.status='open'
               AND (wr.guild_slug=? OR wr.guild_slug IS NULL) ORDER BY wr.opened_at DESC LIMIT 50""",
            (agent["id"], slug))
        in_review = await _rows(db, """
            SELECT id, workspace_id, title, claimed_by_agent_id, updated_ts FROM vantage_tasks
             WHERE guild_slug=? AND status IN ('review','in_review') ORDER BY updated_ts DESC LIMIT 50""", (slug,))
        in_review += await _rows(db, """
            SELECT id, NULL AS workspace_id, title, claimed_by_id AS claimed_by_agent_id, updated_at AS updated_ts
              FROM guild_tasks WHERE guild_id=? AND status='review' ORDER BY updated_at DESC LIMIT 50""", (gid,))
        join_requests = await _one(db,
            "SELECT COUNT(*) AS n FROM join_challenges WHERE guild_id=? AND consumed_at IS NULL", (gid,)) or {}
        live_enable = await _rows(db, """
            SELECT id, name FROM trading_strategies WHERE guild_slug=? AND live_requested=1 AND live_enabled=0""", (slug,))

    # ── health ──
    transports = [{"name": i.name, "configured": i.configured, "proven": i.proven,
                   "can_receive": i.can_receive} for i in channel_transport.available()]
    unprovisioned = [c["slug"] for c in channels if not c.get("buzz_channel_id")]
    from ..routers.workspace import CODE_SANDBOX_URL
    gitea_configured = bool(__import__("backend.config", fromlist=["settings"]).settings.GITEA_URL)

    return {
        "guild": {"slug": slug, "name": guild["name"], "your_role": membership["role"]},
        "generated_at": now,
        "workspaces": {"coding": coding, "trading": trading, "content": content},
        "members": {"roster": members, "workspace_roles": roles, "count": len(members),
                    "online": sum(1 for m in members if not m["presence_stale"])},
        "tasks": {"by_kind": counts, "totals": totals, "leases_expiring_soon": expiring},
        "mention_inbox": {"items": inbox, "count": len(inbox)},
        "repos": {"bindings": repos, "open_prs": None, "ci": None,
                  "note": "PR and CI state arrives with webhook ingest (G6); none is ingested yet"},
        "receipts": {"execution_receipts": exec_receipts.get("n", 0), "latest_ts": exec_receipts.get("latest"),
                     "chain_receipts": chain.get("n", 0)},
        "health": {"transports": transports, "unprovisioned_channels": unprovisioned,
                   "sandbox_configured": bool(CODE_SANDBOX_URL), "gitea_configured": gitea_configured,
                   "outbox_pending": None, "indexer_lag_seconds": None,
                   "note": "outbox and indexer lag are not measured yet"},
        "approvals": {"witness_votes": my_witness, "work_in_review": in_review,
                      "join_requests_pending": join_requests.get("n", 0),
                      "live_enable_blocked": live_enable,
                      "count": len(my_witness) + len(in_review) + int(join_requests.get("n", 0) or 0)},
    }
