"""Human Input inbox — sign-off cards (allow / deny).

A consequential action is never applied by the request that asks for it. The
request files a card and returns at once (non-blocking); the requester keeps
working. A *different* human operator with an approver role decides. On
approval the action is cleared with Èṣù and then applied; a gate halt leaves
the card `halted` with the reason, and nothing changes.

Rules enforced here, not in the UI:
  * requester != approver (no self-approval)
  * trading / execution kinds need an approver role (owner or security)
  * expiry means deny
  * an identical pending card is reused, not duplicated
"""
from __future__ import annotations

import json
import uuid
from typing import Awaitable, Callable, Optional

import aiosqlite
from fastapi import HTTPException

from .. import esu_gate, trading_mode
from ..config import settings
from ..db import get_db
from . import live_controls
from .operators import APPROVER_ROLES

STATUSES = ("pending", "approved", "denied", "expired", "halted")


async def ensure_tables(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS signoff_requests (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            risk TEXT NOT NULL DEFAULT 'high',
            requested_by_human INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_by_human INTEGER,
            decided_at TEXT,
            decision_note TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_signoff_status ON signoff_requests(status, created_at)")


# kind -> (risk, applier). Every P0 kind moves money, so every one is critical
# and needs an approver role.
async def _apply_live_mode_on(p: dict, actor: str) -> dict:
    return await live_controls.set_live_mode(enabled=True, actor=actor, note=p.get("note", ""))


async def _apply_daemon_setting(p: dict, actor: str) -> dict:
    return await live_controls.set_daemon_setting(key=p["key"], value=p["value"], actor=actor, note=p.get("note", ""))


async def _apply_daemon_start(p: dict, actor: str) -> dict:
    from ..routers.daemons import apply_action
    return await apply_action(p["unit"], "start", actor)


async def _apply_daemon_restart(p: dict, actor: str) -> dict:
    from ..routers.daemons import apply_action
    return await apply_action(p["unit"], "restart", actor)


KINDS: dict[str, tuple[str, Callable[[dict, str], Awaitable[dict]]]] = {
    "daemon.start": ("high", _apply_daemon_start),
    "daemon.restart": ("high", _apply_daemon_restart),
    "trading.live_mode_on": ("critical", _apply_live_mode_on),
    "trading.daemon_arm": ("critical", _apply_daemon_setting),
    "trading.daemon_wallet": ("critical", _apply_daemon_setting),
}


def _row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"] or "{}")
    return d


async def file(*, kind: str, payload: dict, requester_human_id: int, summary: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown sign-off kind {kind}")
    body = json.dumps(payload, sort_keys=True)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _expire(db)
        existing = await (await db.execute(
            "SELECT * FROM signoff_requests WHERE kind=? AND payload=? AND status='pending'", (kind, body)
        )).fetchone()
        if existing:
            return _row(existing)
        sid = uuid.uuid4().hex
        await db.execute(
            """INSERT INTO signoff_requests (id, kind, payload, summary, risk, requested_by_human, expires_at)
               VALUES (?,?,?,?,?,?, datetime('now', ?))""",
            (sid, kind, body, summary[:300], KINDS[kind][0], requester_human_id,
             f"+{int(settings.SIGNOFF_TTL_HOURS)} hours"),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM signoff_requests WHERE id=?", (sid,))).fetchone()
    return _row(row)


async def _expire(db) -> None:
    await db.execute(
        "UPDATE signoff_requests SET status='expired', result='expired: treated as deny' "
        "WHERE status='pending' AND expires_at <= datetime('now')"
    )
    await db.commit()


async def list_cards(status: Optional[str] = "pending", limit: int = 100) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _expire(db)
        if status:
            rows = await (await db.execute(
                "SELECT * FROM signoff_requests WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM signoff_requests ORDER BY created_at DESC LIMIT ?", (limit,)
            )).fetchall()
    return [_row(r) for r in rows]


async def _set(sid: str, **fields) -> dict:
    cols = ", ".join(f"{k}=?" for k in fields)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(f"UPDATE signoff_requests SET {cols} WHERE id=?", (*fields.values(), sid))
        await db.commit()
        row = await (await db.execute("SELECT * FROM signoff_requests WHERE id=?", (sid,))).fetchone()
    return _row(row)


async def decide(*, sid: str, approver: dict, approve: bool, note: str = "") -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _expire(db)
        row = await (await db.execute("SELECT * FROM signoff_requests WHERE id=?", (sid,))).fetchone()
    if not row:
        raise HTTPException(404, "No such sign-off card")
    card = _row(row)
    if card["status"] != "pending":
        raise HTTPException(409, f"Card is already {card['status']}")
    if int(card["requested_by_human"]) == int(approver["id"]):
        raise HTTPException(403, "No self-approval: the requester cannot decide their own card")
    if approve and approver.get("operator_role") not in APPROVER_ROLES:
        raise HTTPException(403, "Trading and execution cards need an owner or security operator")

    stamp = {"decided_by_human": approver["id"], "decision_note": (note or "")[:500]}
    if not approve:
        return await _set(sid, status="denied", decided_at=_now(), result="denied", **stamp)

    risk, applier = KINDS[card["kind"]]
    resource = "instance.daemons" if card["kind"].startswith("daemon.") else "instance.trading"
    verdict = await esu_gate.evaluate(esu_gate.Operation(
        tool=card["kind"], params=json.dumps(card["payload"], sort_keys=True),
        intent=card["summary"] or card["kind"], agent_id=None,
        action_intent=esu_gate.ActionIntent(purpose=card["kind"], resource=resource,
                                            mutations=[card["kind"]], expected_effects=[card["summary"] or card["kind"]]),
    ))
    esu_note = ""
    if not verdict.is_approved() and trading_mode.demo_mode():
        # Demo: settlement is hard-refused at every edge, so nothing this card
        # enables can move funds. Record that the gate did not clear it.
        esu_note = f" (demo: esu not cleared — {verdict.halt_reason()}; settlement refused)"
    elif not verdict.is_approved():
        return await _set(sid, status="halted", decided_at=_now(), result=f"esu: {verdict.halt_reason()}", **stamp)

    actor = f"signoff:{sid} requested_by_human:{card['requested_by_human']} approved_by_human:{approver['id']}"
    try:
        applied = await applier(card["payload"], actor)
    except HTTPException as exc:
        return await _set(sid, status="halted", decided_at=_now(), result=f"precondition: {exc.detail}", **stamp)
    return await _set(sid, status="approved", decided_at=_now(), result=(json.dumps(applied)[:900] + esu_note), **stamp)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
