"""Human accounts — a separate identity layer from agents. An agent's
X-Agent-Key stays sovereign and unchanged; a human logging in here gets NO
implicit access to any agent. Bridging happens only through agent_grants
rows (see agent_links.py) created at genesis-birth, explicit linking, or an
agent's own re-scoping decision."""
import asyncio
import hashlib as _hlib
import json
import re as _rexp
import secrets

import aiosqlite
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..db import get_db
from ..deps import _parse_body, get_human

_limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/humans", tags=["humans"])

SESSION_TTL_DAYS = 30
_EMAIL_RE = _rexp.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mint_session() -> str:
    return "vsess_" + secrets.token_hex(24)


async def _create_session_row(db, human_id: int, token: str) -> None:
    token_hash = _hlib.sha256(token.encode()).hexdigest()
    await db.execute(
        """INSERT INTO human_sessions (human_id, token_hash, expires_at)
           VALUES (?, ?, datetime('now', ?))""",
        (human_id, token_hash, f"+{SESSION_TTL_DAYS} days"),
    )


@router.post("/register")
@_limiter.limit("5/minute")
async def register(request: Request):
    body = await _parse_body(request)
    email = str(body.get("email", "")).strip().lower()[:255]
    password = str(body.get("password", ""))
    display_name = str(body.get("display_name", ""))[:100]

    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Valid email is required")
    if len(password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        async with get_db() as db:
            cur = await db.execute(
                "INSERT INTO humans (email, password_hash, display_name) VALUES (?, ?, ?)",
                (email, password_hash, display_name),
            )
            human_id = cur.lastrowid
            token = _mint_session()
            await _create_session_row(db, human_id, token)
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    return {"human_id": human_id, "email": email, "display_name": display_name, "session_token": token}


@router.post("/login")
@_limiter.limit("10/minute")
async def login(request: Request):
    body = await _parse_body(request)
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM humans WHERE email = ?", (email,)
        )).fetchone()

    if not row or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = _mint_session()
    async with get_db() as db:
        await _create_session_row(db, row["id"], token)
        await db.execute("UPDATE humans SET last_login_at=datetime('now') WHERE id=?", (row["id"],))
        await db.commit()

    return {"human_id": row["id"], "email": row["email"], "display_name": row["display_name"], "session_token": token}


@router.post("/logout")
async def logout(request: Request, human: dict = Depends(get_human)):
    x_human_session = request.headers.get("x-human-session", "")
    token_hash = _hlib.sha256(x_human_session.encode()).hexdigest()
    async with get_db() as db:
        await db.execute(
            "UPDATE human_sessions SET revoked_at=datetime('now') WHERE token_hash=? AND revoked_at IS NULL",
            (token_hash,),
        )
        await db.commit()
    return {"status": "logged_out"}


@router.get("/me")
async def get_me(human: dict = Depends(get_human)):
    human = dict(human)
    human.pop("password_hash", None)
    return human


@router.get("/me/buzz/status")
async def human_buzz_status(human: dict = Depends(get_human)):
    from ..buzz_human_identity import get_human_buzz_status
    return await get_human_buzz_status(human["id"])


@router.post("/me/buzz/register")
async def human_buzz_register(human: dict = Depends(get_human)):
    """Section 1.4: a human gets their own real Buzz/Nostr identity,
    distinct from any agent they hold a grant on. Existing grants are
    re-synced immediately so channel membership catches up without
    waiting for the next grant change."""
    from ..buzz_human_identity import register_human_on_buzz, sync_grant_to_buzz
    result = await register_human_on_buzz(human["id"])

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT agent_id, scopes FROM agent_grants WHERE human_id=? AND revoked_at IS NULL",
            (human["id"],),
        )).fetchall()
    for r in rows:
        try:
            scopes = json.loads(r["scopes"])
        except Exception:
            scopes = []
        asyncio.create_task(sync_grant_to_buzz(human["id"], r["agent_id"], scopes))

    return result
