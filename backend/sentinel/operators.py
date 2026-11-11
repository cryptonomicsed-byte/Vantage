"""Named instance operators.

An operator is a *human* (humans table, session auth) holding one instance
role. Roles are per instance, never "the admin is <someone>":

  owner     may approve every sign-off, including trading and execution
  security  may approve every sign-off, including trading and execution
  operator  may request sign-offs and apply brakes; may not approve trading

The first operator is bootstrapped with the instance admin key; after that,
operators are managed through Sentinel. No operators = nothing can be approved.
"""
from __future__ import annotations

from typing import Optional

import aiosqlite
from fastapi import Depends, HTTPException

from ..db import get_db
from ..deps import get_human

ROLES = ("owner", "security", "operator")
APPROVER_ROLES = ("owner", "security")


async def ensure_tables(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS instance_operators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            human_id INTEGER NOT NULL UNIQUE REFERENCES humans(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('owner','security','operator')),
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            added_by TEXT NOT NULL DEFAULT '',
            revoked_at TEXT
        )
    """)


async def role_of(human_id: int) -> Optional[str]:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT role FROM instance_operators WHERE human_id=? AND revoked_at IS NULL", (human_id,)
        )).fetchone()
    return row[0] if row else None


async def require_operator(human: dict = Depends(get_human)) -> dict:
    """Dependency: a logged-in human who holds an active instance role."""
    role = await role_of(human["id"])
    if role is None:
        raise HTTPException(403, "Sentinel: an instance operator role is required")
    return {**human, "operator_role": role}


async def upsert(human_id: int, role: str, added_by: str) -> dict:
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(ROLES)}")
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        h = await (await db.execute("SELECT id FROM humans WHERE id=?", (human_id,))).fetchone()
        if not h:
            raise HTTPException(404, "No such human account")
        await db.execute(
            """INSERT INTO instance_operators (human_id, role, added_by) VALUES (?,?,?)
               ON CONFLICT(human_id) DO UPDATE SET role=excluded.role, added_by=excluded.added_by,
                                                   added_at=datetime('now'), revoked_at=NULL""",
            (human_id, role, added_by[:200]),
        )
        await db.commit()
    return {"human_id": human_id, "role": role}


async def revoke(human_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE instance_operators SET revoked_at=datetime('now') WHERE human_id=? AND revoked_at IS NULL",
            (human_id,),
        )
        await db.commit()
    return cur.rowcount > 0


async def list_all() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT o.human_id, o.role, o.added_at, o.added_by, o.revoked_at, h.display_name
               FROM instance_operators o JOIN humans h ON h.id=o.human_id ORDER BY o.id"""
        )).fetchall()
    return [dict(r) for r in rows]
