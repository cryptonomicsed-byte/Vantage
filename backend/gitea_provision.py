"""Gitea auto-provisioning for agents.

On registration or lounge join, every agent gets a real Git identity: a Gitea
user (username derived from the agent name, email agent-{id}@vantage.local)
created through the admin API. Credentials are generated here and returned
ONCE — the same shown-once pattern the guild_api_key uses. Nothing but the
username/uid is stored.

Unconfigured (GITEA_URL/GITEA_TOKEN empty) is not an error: provisioning is
skipped and the response says so. The P0 note stands: hit the admin API from
the VPS itself if the audit host's route to /api/v1/admin times out.
"""
import hashlib
import logging
import re
import secrets
from typing import Optional

import httpx

from .config import settings
from .db import get_db

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slug(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")[:32]
    return slug or "agent"


async def provision_gitea_user(
    *, agent_id: Optional[int] = None, principal_id: Optional[int] = None,
    name: str,
) -> dict:
    """Create (or fetch) the Gitea account for an agent identity.

    Returns {"username", "password", "token", "gitea_uid"} on first creation
    (password/token are returned once and never stored); on a repeat call for
    an identity that already has an account, returns {"username", "existing":
    True} with no secrets.
    """
    if not (settings.GITEA_URL and settings.GITEA_TOKEN):
        return {"gitea": "not configured"}

    key_col = "agent_id" if agent_id is not None else "principal_id"
    key_val = agent_id if agent_id is not None else principal_id

    async with get_db() as db:
        db.row_factory = getattr(db, "row_factory", None)
        cur = await db.execute(
            f"SELECT gitea_username, gitea_uid FROM agent_gitea WHERE {key_col}=?",
            (key_val,),
        )
        row = await cur.fetchone()
    if row:
        return {"username": row[0], "gitea_uid": row[1], "existing": True,
                "gitea": "linked"}

    base = _slug(name)
    username = base
    email = f"agent-{key_val}@vantage.local"
    password = secrets.token_hex(8)

    headers = {"Authorization": f"token {settings.GITEA_TOKEN}",
               "Content-Type": "application/json"}
    url = settings.GITEA_URL.rstrip("/") + "/api/v1/admin/users"

    created = None
    # Name collisions: append -{key_val} and retry once.
    for candidate in (username, f"{base}-{key_val}"[:39]):
        payload = {
            "username": candidate,
            "email": email,
            "password": password,
            "full_name": name or candidate,
            "must_change_password": False,
            "send_notify": False,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                created = resp.json()
                username = candidate
                break
            if resp.status_code == 422:  # name or email collision
                continue
            logger.warning("gitea_provision: HTTP %s for %s: %s",
                           resp.status_code, candidate, resp.text[:200])
            return {"gitea": "failed", "error": f"gitea HTTP {resp.status_code}"}
        except Exception as exc:
            logger.warning("gitea_provision: admin API unreachable: %s", exc)
            return {"gitea": "failed", "error": f"unreachable: {exc}"}

    if created is None:
        return {"gitea": "failed", "error": "username collision after retry"}

    gitea_uid = created.get("id")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_gitea (agent_id, principal_id, gitea_username, gitea_uid)
               VALUES (?,?,?,?)""",
            (agent_id, principal_id, username, gitea_uid),
        )
        await db.commit()

    # Personal access token — returned once, never stored.
    token = None
    try:
        tok_payload = {"name": f"vantage-{username}"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                settings.GITEA_URL.rstrip("/") + f"/api/v1/users/{username}/tokens",
                json=tok_payload, headers=headers,
            )
        if resp.status_code in (200, 201):
            token = resp.json().get("sha1") or resp.json().get("token")
    except Exception as exc:
        logger.warning("gitea_provision: token creation failed: %s", exc)

    return {
        "username": username, "gitea_uid": gitea_uid,
        "password": password, "token": token, "gitea": "created",
        "note": "Store these credentials now — they will not be shown again.",
    }
