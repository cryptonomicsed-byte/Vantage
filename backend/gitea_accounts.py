"""Per-agent Gitea accounts.

Every Vantage agent gets its own Gitea identity, so a repo an agent creates is
owned by that agent's account rather than by one shared platform login. This
module provisions those identities against the local Gitea instance and hands
back a scoped access token.

Why a real account per agent:
  * tenancy — repos never land in another user's namespace
  * revocation — deleting an agent's token cuts its git access alone
  * audit — git history attributes work to the agent that did it

Tokens are encrypted at rest with secret_vault (AES-256-GCM) keyed by a
per-agent principal, the same mechanism provider credentials use.
"""
from __future__ import annotations

import os
import re
import secrets
import string
from typing import Optional

import httpx

from .db import get_db
from .secret_vault import decrypt_secret, encrypt_secret

GITEA_URL = os.environ.get("VANTAGE_GITEA_URL", "http://127.0.0.1:3001").rstrip("/")
API = f"{GITEA_URL}/api/v1"
ADMIN_TOKEN = os.environ.get("VANTAGE_GITEA_ADMIN_TOKEN", "").strip()
TIMEOUT = float(os.environ.get("VANTAGE_GITEA_TIMEOUT", "15"))

# Set by deps.get_agent for the duration of a request so modules like
# repo_host can resolve "who is acting" without threading the agent through
# every call site.
from .agent_context import current_agent  # shared per-request agent context


def _principal(agent_id: int) -> str:
    return f"gitea:{agent_id}"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")
    return (s or "agent")[:32]


async def ensure_tables() -> None:
    async with get_db() as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS agent_gitea_accounts (
                   agent_id     INTEGER PRIMARY KEY,
                   gitea_login  TEXT NOT NULL UNIQUE,
                   token_enc    TEXT NOT NULL,
                   created_at   TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        await db.commit()


def _admin_headers() -> dict:
    return {"Authorization": f"token {ADMIN_TOKEN}", "Content-Type": "application/json"}



def _cli_token(login: str) -> str:
    """Issue a repo-scoped access token for `login` via the gitea CLI."""
    import subprocess
    cmd = [
        "sudo", "-u", "git", "gitea", "admin", "user", "generate-access-token",
        "--config", os.environ.get("VANTAGE_GITEA_CONFIG", "/etc/gitea/app.ini"),
        "--username", login,
        "--token-name", f"vantage-{login}-{int(__import__('time').time())}",
        "--scopes", "write:repository,write:user",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    blob = (out.stdout or "") + (out.stderr or "")
    m = re.search(r"\b([0-9a-f]{40})\b", blob)
    if not m:
        raise RuntimeError(f"gitea token CLI failed: {blob.strip()[:160]}")
    return m.group(1)


async def get_account(agent_id: int) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = None
        row = await (await db.execute(
            "SELECT agent_id, gitea_login, token_enc, created_at FROM agent_gitea_accounts WHERE agent_id=?",
            (agent_id,),
        )).fetchone()
    if not row:
        return None
    return {"agent_id": row[0], "gitea_login": row[1], "token_enc": row[2], "created_at": row[3]}


async def provision(agent: dict) -> dict:
    """Create (or return) this agent's Gitea account and token."""
    agent_id = int(agent["id"])
    existing = await get_account(agent_id)
    if existing:
        return {"gitea_login": existing["gitea_login"], "created": False}

    if not ADMIN_TOKEN:
        raise RuntimeError("VANTAGE_GITEA_ADMIN_TOKEN is not configured")

    login = _slug(agent.get("name") or f"agent-{agent_id}")
    # Gitea logins are unique; suffix rather than fail if the name is taken.
    base, n = login, 1
    async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
        while True:
            chk = await cl.get(f"{API}/users/{login}")
            if chk.status_code == 404:
                break
            n += 1
            login = f"{base}-{n}"

        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
        payload = {
            "username": login,
            "email": f"{login}@vantage.local",
            "password": password,
            "must_change_password": False,
            "send_notify": False,
        }
        r = await cl.post(f"{API}/admin/users", json=payload, headers=_admin_headers())
        if r.status_code not in (200, 201):
            raise RuntimeError(f"gitea user create failed: {r.status_code} {r.text[:120]}")

    # Mint the agent's own token with the Gitea CLI. The REST token endpoint
    # (POST /users/{login}/tokens) requires that user's basic auth, which we
    # deliberately never store; the CLI runs as the service account and can
    # issue scoped tokens for any user.
    token = _cli_token(login)

    if not token:
        raise RuntimeError("gitea returned no token")

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO agent_gitea_accounts (agent_id, gitea_login, token_enc) VALUES (?,?,?)",
            (agent_id, login, encrypt_secret(_principal(agent_id), token)),
        )
        await db.commit()

    return {"gitea_login": login, "created": True}


async def token_for(agent: Optional[dict]) -> str:
    """The acting agent's Gitea token, or "" when it has no account yet."""
    if not agent:
        return ""
    acct = await get_account(int(agent["id"]))
    if not acct:
        return ""
    try:
        return decrypt_secret(_principal(int(agent["id"])), acct["token_enc"])
    except Exception:
        return ""


async def ensure_for_current_agent() -> str:
    """Provision on first use, then return the token."""
    agent = current_agent.get()
    if not agent:
        return ""
    tok = await token_for(agent)
    if tok:
        return tok
    await provision(agent)
    return await token_for(agent)
