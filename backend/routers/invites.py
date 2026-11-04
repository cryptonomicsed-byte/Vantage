"""
Agent invite flow for Vantage / Paperclip.

Endpoints:
  POST /api/invites                     — mint an invite (system tool auth)
  GET  /invite/{token}                  — HTML acceptance page (public)
  POST /api/invites/{token}/accept      — claim a key (public, one-shot)
  GET  /api/invites                     — list invites (system tool auth)

Token format: pcp_invite_<32 random hex chars>
Stored as:    SHA-256(token) — never plaintext at rest

Per-agent Paperclip keys are created via the local Paperclip board API
using the PAPERCLIP_CLAUDE_CODE_KEY (which holds board-operator rights).
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.request
import urllib.error

import aiosqlite
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.db import DB_PATH, get_db
from backend.deps import get_system_tool

logger = logging.getLogger(__name__)
router = APIRouter(tags=["invites"])

# ── Config ──────────────────────────────────────────────────────────────────
PC_BASE       = os.environ.get("PAPERCLIP_API_BASE",       "http://127.0.0.1:3100")
PC_KEY        = os.environ.get("PAPERCLIP_CLAUDE_CODE_KEY", "")
PC_COMPANY_ID = os.environ.get("PAPERCLIP_COMPANY_ID",      "")
VPS_HOST      = os.environ.get("VANTAGE_PUBLIC_HOST",       "omokoda.duckdns.org")
INVITE_TTL    = int(os.environ.get("INVITE_TTL_DAYS",        "7")) * 86400

TOKEN_PREFIX = "pcp_invite_"

# Label IDs created earlier — extended as new agent types are added
AGENT_LABELS = {
    "claude-code":      "cafb7807-aaef-482f-832c-b4af265cfd4e",  # secret-scan:allow
    "coordinator-grok": "adf8fc76-1d65-45c6-845c-fe37d6663820",  # secret-scan:allow
}


# ── DB init ──────────────────────────────────────────────────────────────────
async def init_invites_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_invites (
                id           TEXT PRIMARY KEY,
                token_hash   TEXT NOT NULL UNIQUE,
                agent_name   TEXT NOT NULL,
                capabilities TEXT NOT NULL DEFAULT '[]',
                created_at   REAL NOT NULL,
                expires_at   REAL NOT NULL,
                used_at      REAL,
                used_by_ip   TEXT,
                created_by_ip TEXT,
                paperclip_key_id TEXT
            )
        """)
        await db.commit()
    logger.info("invites: DB table ready")


# ── Helpers ──────────────────────────────────────────────────────────────────
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pc_post(path: str, body: dict, timeout: int = 30) -> dict:
    url  = f"{PC_BASE}{path}"
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {PC_KEY}",
            "Content-Type":  "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"PC HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


# ── Pydantic models ───────────────────────────────────────────────────────────
class InviteCreate(BaseModel):
    agent_name:   str
    capabilities: list[str] = []
    ttl_days:     int       = 7


class InviteAccept(BaseModel):
    requestType:  str       = "agent"
    agentName:    str
    capabilities: list[str] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/api/invites")
async def create_invite(
    body:    InviteCreate,
    request: Request,
    x_vantage_tool:     str = Header(...),
    x_vantage_tool_key: str = Header(...),
):
    """Mint a new agent invite. Requires system-tool auth."""
    if not await get_system_tool(x_vantage_tool, x_vantage_tool_key):
        raise HTTPException(401, "unauthorized")

    token      = TOKEN_PREFIX + secrets.token_hex(32)
    token_hash = _hash_token(token)
    invite_id  = secrets.token_hex(16)
    now        = time.time()
    expires_at = now + min(body.ttl_days, 30) * 86400

    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_invites
               (id, token_hash, agent_name, capabilities, created_at, expires_at, created_by_ip)
               VALUES (?,?,?,?,?,?,?)""",
            (
                invite_id,
                token_hash,
                body.agent_name,
                json.dumps(body.capabilities),
                now,
                expires_at,
                request.client.host if request.client else "unknown",
            ),
        )
        await db.commit()

    invite_url = f"https://{VPS_HOST}/invite/{token}"
    logger.info(
        "invite minted: id=%s agent=%s expires=%s src=%s",
        invite_id, body.agent_name,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_at)),
        request.client.host if request.client else "unknown",
    )
    return {
        "invite_id":    invite_id,
        "agent_name":   body.agent_name,
        "capabilities": body.capabilities,
        "url":          invite_url,
        "expires_at":   expires_at,
        "accept_curl":  (
            f'curl -s -X POST {invite_url.replace("/invite/", "/api/invites/")}/accept '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"requestType":"agent","agentName":"{body.agent_name}","capabilities":{json.dumps(body.capabilities)}}}\''
        ),
    }


@router.get("/invite/{token}", response_class=HTMLResponse)
async def invite_page(token: str):
    """Human-visible invite acceptance page."""
    if not token.startswith(TOKEN_PREFIX):
        raise HTTPException(404, "invite not found")

    token_hash = _hash_token(token)
    async with get_db() as db:
        cur = await db.execute(
            "SELECT agent_name, capabilities, expires_at, used_at FROM agent_invites WHERE token_hash=?",
            (token_hash,),
        )
        row = await cur.fetchone()

    if not row:
        raise HTTPException(404, "invite not found")

    agent_name, caps_json, expires_at, used_at = row
    caps   = json.loads(caps_json or "[]")
    if used_at:
        status_msg = "<p style='color:#f55'>This invite has already been used.</p>"
    elif time.time() > expires_at:
        status_msg = "<p style='color:#f55'>This invite has expired.</p>"
    else:
        exp_str    = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(expires_at))
        status_msg = f"<p style='color:#5f5'>Valid until {exp_str}</p>"

    caps_html = "".join(f"<li>{c}</li>" for c in caps) if caps else "<li>(none specified)</li>"
    accept_url = f"/api/invites/{token}/accept"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Vantage Agent Invite</title>
  <style>
    body {{ background:#0d1117; color:#e6edf3; font-family:monospace; max-width:520px; margin:60px auto; padding:0 20px; }}
    h1   {{ color:#58a6ff; font-size:1.4em; }}
    .box {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:20px; margin:20px 0; }}
    button {{ background:#238636; color:#fff; border:none; padding:10px 24px; border-radius:6px; cursor:pointer; font-size:1em; }}
    button:hover {{ background:#2ea043; }}
    pre  {{ background:#0d1117; padding:10px; border-radius:4px; overflow:auto; font-size:.85em; }}
    #result {{ margin-top:16px; white-space:pre-wrap; }}
  </style>
</head>
<body>
  <h1>Vantage Agent Invite</h1>
  <div class="box">
    <p><strong>Agent name:</strong> {agent_name}</p>
    <p><strong>Capabilities:</strong></p>
    <ul>{caps_html}</ul>
    {status_msg}
  </div>
  <div class="box">
    <p>Accept this invite to claim your API key:</p>
    <button onclick="acceptInvite()">Accept &amp; Claim Key</button>
    <div id="result"></div>
  </div>
  <div class="box">
    <p>Or via curl:</p>
    <pre>curl -s -X POST https://{VPS_HOST}{accept_url} \\
  -H "Content-Type: application/json" \\
  -d '{{"requestType":"agent","agentName":"{agent_name}","capabilities":{json.dumps(caps)}}}'</pre>
  </div>
  <script>
  async function acceptInvite() {{
    const btn = document.querySelector('button');
    btn.disabled = true; btn.textContent = 'Accepting...';
    const res = await fetch('{accept_url}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{requestType:'agent', agentName:'{agent_name}', capabilities:{json.dumps(caps)}}})
    }});
    const data = await res.json();
    document.getElementById('result').textContent = JSON.stringify(data, null, 2);
    btn.textContent = res.ok ? '✓ Done' : '✗ Failed';
  }}
  </script>
</body>
</html>"""
    return HTMLResponse(html)


@router.post("/api/invites/{token}/accept")
async def accept_invite(token: str, body: InviteAccept, request: Request):
    """Accept an invite, create a Paperclip board key, return it. One-shot."""
    if not token.startswith(TOKEN_PREFIX):
        raise HTTPException(404, "invite not found")

    token_hash = _hash_token(token)
    src_ip     = request.client.host if request.client else "unknown"

    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, agent_name, capabilities, expires_at, used_at FROM agent_invites WHERE token_hash=?",
            (token_hash,),
        )
        row = await cur.fetchone()

    if not row:
        logger.warning("invite accept: token not found src=%s", src_ip)
        raise HTTPException(404, "invite not found")

    invite_id, stored_agent, caps_json, expires_at, used_at = row

    if used_at:
        logger.warning("invite accept: already used id=%s src=%s", invite_id, src_ip)
        raise HTTPException(409, "invite already used")

    if time.time() > expires_at:
        logger.warning("invite accept: expired id=%s src=%s", invite_id, src_ip)
        raise HTTPException(410, "invite expired")

    agent_name = body.agentName or stored_agent

    # Create Paperclip board key for this agent
    pc_key_id  = None
    pc_token   = None
    if PC_KEY and PC_COMPANY_ID:
        try:
            result = _pc_post(
                "/api/board-api-keys",
                {"companyId": PC_COMPANY_ID, "name": agent_name},
            )
            pc_token   = result.get("token")
            pc_key_id  = result.get("id")
            logger.info("invite accept: PC key created for %s id=%s", agent_name, pc_key_id)
        except Exception as e:
            logger.error("invite accept: PC key creation failed: %s", e)
            raise HTTPException(502, f"could not create Paperclip key: {e}")
    else:
        raise HTTPException(503, "Paperclip not configured on this instance")

    # Mark invite consumed
    async with get_db() as db:
        await db.execute(
            "UPDATE agent_invites SET used_at=?, used_by_ip=?, paperclip_key_id=? WHERE id=?",
            (time.time(), src_ip, pc_key_id, invite_id),
        )
        await db.commit()

    logger.info(
        "invite accepted: id=%s agent=%s key_id=%s src=%s",
        invite_id, agent_name, pc_key_id, src_ip,
    )
    return {
        "status":       "accepted",
        "agent_name":   agent_name,
        "capabilities": body.capabilities or json.loads(caps_json or "[]"),
        "api_key":      pc_token,
        "paperclip_api_base": PC_BASE,
        "company_id":   PC_COMPANY_ID,
        "note":         "Store this key securely — it will not be shown again.",
    }


@router.get("/api/invites")
async def list_invites(
    x_vantage_tool:     str = Header(...),
    x_vantage_tool_key: str = Header(...),
):
    """List all invites (admin view). Requires system-tool auth."""
    if not await get_system_tool(x_vantage_tool, x_vantage_tool_key):
        raise HTTPException(401, "unauthorized")

    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, agent_name, capabilities, created_at, expires_at, used_at, used_by_ip, created_by_ip "
            "FROM agent_invites ORDER BY created_at DESC LIMIT 100"
        )
        rows = await cur.fetchall()

    return [
        {
            "id":           r[0],
            "agent_name":   r[1],
            "capabilities": json.loads(r[2] or "[]"),
            "created_at":   r[3],
            "expires_at":   r[4],
            "used":         r[5] is not None,
            "used_at":      r[5],
            "used_by_ip":   r[6],
            "created_by_ip": r[7],
        }
        for r in rows
    ]
