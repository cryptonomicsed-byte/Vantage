"""User-defined tools: custom HTTP integrations and custom skills.

Two things a user can add without touching code:

  integration  an HTTP endpoint plus named actions. Each action becomes a tool
               the agent can call (`custom__<slug>__<action>`). Credentials are
               sealed with the agent's principal, never returned by the API.

  skill        standing instructions the agent follows on every turn, plus a
               short description so the model knows when they apply.

Both are per-agent: `agent_id` is the tenancy boundary, so one user's
integration and another's can share a slug without colliding.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import httpx

from .db import get_db
from .secret_vault import decrypt_secret, encrypt_secret

MAX_ACTIONS = 25
MAX_SKILL_CHARS = 4000
MAX_SKILLS_IN_PROMPT = 12


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:32] or "integration"


async def ensure_tables() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_integrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                base_url TEXT NOT NULL,
                auth_header TEXT DEFAULT '',
                secret_enc TEXT DEFAULT '',
                actions_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(agent_id, slug)
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_instructions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                instructions TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(agent_id, name)
            )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_integrations_agent ON agent_integrations(agent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_skills_agent ON agent_instructions(agent_id)")
        await db.commit()


def _row_dict(cur, row) -> dict:
    return dict(zip([d[0] for d in (cur.description or [])], row))


# ── integrations ───────────────────────────────────────────────────────────
async def list_integrations(agent_id: int) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, agent_id, name, slug, base_url, auth_header, secret_enc, "
            "actions_json, enabled, created_at "
            "FROM agent_integrations WHERE agent_id=? ORDER BY id", (agent_id,))
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = _row_dict(cur, r)
            # the sealed blob never leaves this module; only its existence is exposed
            d["has_secret"] = bool(d.pop("secret_enc", ""))
            try:
                d["actions"] = json.loads(d.pop("actions_json") or "[]")
            except Exception:
                d["actions"] = []
            out.append(d)
        return out


async def create_integration(agent_id: int, name: str, base_url: str,
                             actions: list[dict], auth_header: str = "",
                             secret: str = "") -> dict:
    if not name or not base_url:
        raise ValueError("name and base_url are required")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    clean = []
    for a in (actions or [])[:MAX_ACTIONS]:
        aname = _slugify(a.get("name", ""))
        if not aname:
            continue
        clean.append({
            "name": aname,
            "description": (a.get("description") or "")[:300],
            "method": (a.get("method") or "GET").upper(),
            "path": a.get("path") or "/",
            "params": a.get("params") or [],
        })
    if not clean:
        raise ValueError("at least one action is required")

    slug = _slugify(name)
    principal = f"agent:{agent_id}"
    enc = encrypt_secret(principal, secret) if secret else ""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO agent_integrations (agent_id, name, slug, base_url, auth_header, secret_enc, actions_json, enabled, created_at) "
            "VALUES (?,?,?,?,?,?,?,1,?)",
            (agent_id, name, slug, base_url.rstrip("/"), auth_header or "", enc,
             json.dumps(clean), time.strftime("%Y-%m-%dT%H:%M:%SZ")))
        await db.commit()
    return {"slug": slug, "actions": len(clean)}


async def delete_integration(agent_id: int, integration_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM agent_integrations WHERE id=? AND agent_id=?",
                               (integration_id, agent_id))
        await db.commit()
        return cur.rowcount > 0


async def integration_toolset(agent_id: int) -> tuple[list[dict], dict[str, dict]]:
    """Every enabled action of every integration, as model-facing tools."""
    schemas, dispatch = [], {}
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, agent_id, name, slug, base_url, auth_header, secret_enc, "
            "actions_json FROM agent_integrations WHERE agent_id=? AND enabled=1", (agent_id,))
        rows = await cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]

    for r in rows:
        row = dict(zip(cols, r))
        try:
            actions = json.loads(row.get("actions_json") or "[]")
        except Exception:
            continue
        for a in actions:
            fname = f"custom__{row['slug']}__{a['name']}"[:64]
            props, required = {}, []
            for p in (a.get("params") or []):
                pn = p.get("name")
                if not pn:
                    continue
                props[pn] = {"type": p.get("type", "string"),
                             "description": (p.get("description") or "")[:180]}
                if p.get("required"):
                    required.append(pn)
            schema = {"type": "object", "properties": props}
            if required:
                schema["required"] = required
            schemas.append({"type": "function", "function": {
                "name": fname,
                "description": (a.get("description") or f"{row['name']}: {a['name']}")[:300],
                "parameters": schema}})
            dispatch[fname] = {"kind": "custom", "integration": row, "action": a}
    return schemas, dispatch


async def execute_action(target: dict, args: dict) -> str:
    row, action = target["integration"], target["action"]
    headers = {"Accept": "application/json"}
    if row.get("auth_header") and row.get("secret_enc"):
        try:
            headers[row["auth_header"]] = decrypt_secret(f"agent:{row['agent_id']}",
                                                         row["secret_enc"])
        except Exception as exc:
            return f"integration auth error: {exc}"
    url = row["base_url"].rstrip("/") + "/" + (action.get("path") or "").lstrip("/")
    method = (action.get("method") or "GET").upper()
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as cl:
            if method in ("GET", "DELETE", "HEAD"):
                r = await cl.request(method, url, params=args, headers=headers)
            else:
                r = await cl.request(method, url, json=args, headers=headers)
        body = r.text[:3000]
        return f"HTTP {r.status_code}\n{body}"
    except Exception as exc:
        return f"request failed: {exc}"


# ── skills ─────────────────────────────────────────────────────────────────
async def list_skills(agent_id: int) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, name, description, instructions, enabled, created_at "
            "FROM agent_instructions WHERE agent_id=? ORDER BY id", (agent_id,))
        rows = await cur.fetchall()
        return [_row_dict(cur, r) for r in rows]


async def create_skill(agent_id: int, name: str, description: str, instructions: str) -> dict:
    if not name or not instructions:
        raise ValueError("name and instructions are required")
    async with get_db() as db:
        await db.execute(
            "INSERT INTO agent_instructions (agent_id, name, description, instructions, enabled, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (agent_id, name[:80], (description or "")[:300],
             instructions[:MAX_SKILL_CHARS], time.strftime("%Y-%m-%dT%H:%M:%SZ")))
        await db.commit()
    return {"name": name}


async def delete_skill(agent_id: int, skill_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM agent_instructions WHERE id=? AND agent_id=?",
                               (skill_id, agent_id))
        await db.commit()
        return cur.rowcount > 0


async def skills_prompt(agent_id: int) -> str:
    """Standing instructions, injected into the system message."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT name, description, instructions FROM agent_instructions "
            "WHERE agent_id=? AND enabled=1 ORDER BY id LIMIT ?",
            (agent_id, MAX_SKILLS_IN_PROMPT))
        rows = await cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]
    if not rows:
        return ""
    parts = ["The user has configured these standing skills. Follow them."]
    for r in rows:
        d = dict(zip(cols, r))
        head = f"- {d['name']}" + (f" ({d['description']})" if d.get("description") else "")
        parts.append(f"{head}\n{d['instructions']}")
    return "\n\n".join(parts)[:6000]