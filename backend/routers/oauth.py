"""OAuth 2.1 connector endpoints for MCP clients (ChatGPT, Codex, Claude, Grok, ...).

Implements the MCP authorization flow:
  * RFC 9728 protected resource metadata  -> /.well-known/oauth-protected-resource
  * RFC 8414 authorization server metadata -> /.well-known/oauth-authorization-server
  * authorization code + PKCE (S256)       -> /oauth/authorize, /oauth/token
  * RFC 7591 dynamic client registration   -> /oauth/register
  * RFC 7662 introspection, RFC 7009 revoke

Design notes that matter:

* The token core is shared; platform behaviour comes from connector_profiles.py.
  Nothing here branches on platform except through a profile lookup.
* Issuer/resource are derived from the incoming request (nginx terminates TLS
  and forwards), overridable with OAUTH_ISSUER for a dedicated auth host.
* `iss` is deliberately NOT emitted and authorization_response_iss_parameter_
  supported is NOT advertised. Codex hard-fails a code exchange on an `iss`
  mismatch, so advertising a capability we do not implement is worse than
  advertising less.
* The consent page is the account-creation surface: the person picks "create a
  new Vantage agent" (mints the agent + shows its api_key exactly once) or
  "use an existing agent" (pastes a key). That is how an LLM's owner ends up
  with a real Vantage identity from inside ChatGPT/Grok/Claude.
"""
import html
import json
import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import quote, urlencode, urlparse

import httpx
from fastapi import APIRouter, Body, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import oauth_store as store
from ..connector_profiles import (
    PROFILES,
    SCOPE_ACCOUNT,
    SCOPE_OFFLINE,
    SCOPE_READ,
    SCOPE_WRITE,
    client_registration_modes,
    detect_platform,
    get_profile,
    redirect_allowed,
    resolve_scopes,
    scopes_for,
    validate_client_id,
    validate_redirect,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["oauth"])

ACCESS_TTL = store.ACCESS_TTL


# ── base URLs ──────────────────────────────────────────────────────────────────

def _base_url(request: Request) -> str:
    override = os.environ.get("OAUTH_ISSUER", "").strip()
    if override:
        return override.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _resource_for(request: Request) -> str:
    """The canonical MCP resource. ChatGPT sends this exact value as the
    `resource` query parameter, and we bind it into the token."""
    return _base_url(request) + "/mcp"


# ── discovery ──────────────────────────────────────────────────────────────────

def _protected_resource_doc(request: Request, path: str = "") -> dict:
    base = _base_url(request)
    resource = base + (path or "/mcp")
    return {
        "resource": resource,
        "authorization_servers": [base],
        "scopes_supported": [SCOPE_READ, SCOPE_WRITE, SCOPE_ACCOUNT, SCOPE_OFFLINE],
        "bearer_methods_supported": ["header"],
        "resource_documentation": base + "/docs",
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_root(request: Request):
    return JSONResponse(_protected_resource_doc(request))


@router.get("/.well-known/oauth-protected-resource/{rest:path}")
async def protected_resource_path(request: Request, rest: str):
    """RFC 9728 path insertion: /mcp -> /.well-known/oauth-protected-resource/mcp"""
    return JSONResponse(_protected_resource_doc(request, "/" + rest))


def _auth_server_doc(request: Request) -> dict:
    base = _base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "introspection_endpoint": base + "/oauth/introspect",
        "revocation_endpoint": base + "/oauth/revoke",
        "scopes_supported": [SCOPE_READ, SCOPE_WRITE, SCOPE_ACCOUNT, SCOPE_OFFLINE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        # ChatGPT registers through CIMD when this is true, which gives it one
        # stable client identity per MCP server instead of a new DCR client on
        # every reconnect.
        "client_id_metadata_document_supported": True,
        # NOTE: authorization_response_iss_parameter_supported is intentionally
        # absent -- see module docstring.
    }


@router.get("/.well-known/oauth-authorization-server")
async def auth_server_metadata(request: Request):
    return JSONResponse(_auth_server_doc(request))


# ── client resolution / registration ───────────────────────────────────────────

async def _resolve_client(request: Request, client_id: str, redirect_uri: str) -> dict:
    """Return a client record, registering-on-first-sight for CIMD clients.

    CIMD: client_id IS an https metadata-document URL. We enforce the platform's
    host allowlist (a bare https check would let any site claim to be ChatGPT)
    and try to fetch the document for its name. A fetch failure is tolerated --
    it must not strand an authorization -- but the host check is not.
    """
    if not client_id:
        raise HTTPException(400, "client_id required")

    platform = detect_platform(client_id, redirect_uri)

    existing = await store.get_client(client_id)
    if existing:
        if not redirect_allowed(existing, platform, redirect_uri):
            raise HTTPException(400, "redirect_uri not allowed for this client")
        return existing

    if client_id.startswith("https://"):
        # ── CIMD ──
        if not validate_client_id(platform, client_id):
            raise HTTPException(400, "client_id metadata document not allowed for this platform")
        name, uris = "", [redirect_uri]
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as c:
                r = await c.get(client_id, headers={"Accept": "application/json"})
                if r.status_code == 200:
                    doc = r.json()
                    name = doc.get("client_name") or ""
                    if isinstance(doc.get("redirect_uris"), list):
                        uris = [u for u in doc["redirect_uris"] if isinstance(u, str)]
                    for u in doc.get("redirect_uris") or []:
                        if not validate_redirect(platform, u):
                            raise HTTPException(400, "CIMD document contains a disallowed redirect_uri")
        except HTTPException:
            raise
        except Exception as exc:
            logger.info("CIMD fetch failed (continuing with host check only): %s", exc)

        client = await store.upsert_client(
            client_id, platform=platform, registration_method="cimd",
            client_name=name or platform, redirect_uris=uris,
            scopes=" ".join(scopes_for(platform)), metadata_url=client_id,
        )
        await store.audit("client_cimd_registered", platform, client_id, None, client_id)
        if not redirect_allowed(client, platform, redirect_uri):
            raise HTTPException(400, "redirect_uri not allowed for this client")
        return client

    # Locally issued (DCR or predefined) -- must already exist.
    raise HTTPException(400, "unknown client_id; register at /oauth/register")


@router.post("/oauth/register", status_code=201)
async def register_client(request: Request, body: dict = Body(...)):
    """RFC 7591 dynamic client registration. ChatGPT calls this once per
    connector instance when it is not using CIMD."""
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise HTTPException(400, "redirect_uris required")

    platform = "generic"
    for u in redirect_uris:
        if not isinstance(u, str):
            raise HTTPException(400, "redirect_uris must be strings")
        platform = detect_platform(body.get("client_id") or "", u)
        if not validate_redirect(platform, u):
            # Try every profile's checker before rejecting: a client that says
            # nothing about itself still gets a chance under a permissive profile.
            if not any(validate_redirect(p, u) for p in PROFILES):
                raise HTTPException(400, f"redirect_uri not allowed: {u}")

    want_secret = (body.get("token_endpoint_auth_method") or "none") != "none"
    client_id = "vgcl_" + secrets.token_urlsafe(24)
    client_secret = secrets.token_urlsafe(32) if want_secret else None

    await store.upsert_client(
        client_id, platform=platform, registration_method="dcr",
        client_name=body.get("client_name") or "MCP client",
        redirect_uris=redirect_uris,
        scopes=resolve_scopes(platform, body.get("scope")),
        client_secret=client_secret,
    )
    await store.audit("client_dcr_registered", platform, client_id, None,
                      ",".join(redirect_uris))

    out = {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": resolve_scopes(platform, body.get("scope")),
        "token_endpoint_auth_method": "client_secret_post" if want_secret else "none",
        "client_id_issued_at": int(time.time()),
    }
    if client_secret:
        out["client_secret"] = client_secret
        out["client_secret_expires_at"] = 0
    return JSONResponse(out, status_code=201)


# ── authorize ──────────────────────────────────────────────────────────────────

def _consent_html(request: Request, *, client: dict, platform: str, redirect_uri: str,
                  code_challenge: str, code_challenge_method: str, scope: str,
                  resource: str, state: str, error: str = "") -> str:
    prof = get_profile(platform)
    e = html.escape
    err_block = f'<div class="err">{e(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(prof['consent_title'])}</title>
<style>
  :root {{ --bg:#0b0d12; --card:#141821; --ink:#e8ecf8; --dim:#8b93ad;
           --edge:#232a38; --acc:#6ea8fe; --ok:#3ecf8e; --err:#ff6b6b; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         display:flex; align-items:flex-start; justify-content:center; padding:24px 14px; }}
  .card {{ background:var(--card); border:1px solid var(--edge); border-radius:14px;
           max-width:520px; width:100%; padding:22px; }}
  h1 {{ font-size:19px; margin:0 0 6px; }}
  .sub {{ color:var(--dim); font-size:13px; margin-bottom:18px; }}
  .plat {{ display:inline-block; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
           color:var(--acc); border:1px solid var(--edge); border-radius:999px;
           padding:2px 9px; margin-bottom:12px; }}
  label {{ display:block; font-size:12px; color:var(--dim); margin:14px 0 5px; }}
  input[type=text], input[type=password], input[type=email] {{
           width:100%; padding:11px 12px; border-radius:9px; border:1px solid var(--edge);
           background:#0e1219; color:var(--ink); font-size:15px; }}
  fieldset {{ border:1px solid var(--edge); border-radius:11px; padding:12px 14px; margin:0 0 8px; }}
  legend {{ font-size:12px; color:var(--dim); padding:0 6px; }}
  .opt {{ display:flex; gap:10px; align-items:flex-start; margin:9px 0; }}
  .opt input[type=radio] {{ margin-top:3px; }}
  .opt span {{ font-size:14px; }}
  .hint {{ color:var(--dim); font-size:12px; margin-top:3px; }}
  button {{ width:100%; margin-top:20px; padding:13px; border:0; border-radius:10px;
            background:var(--acc); color:#07101f; font-size:15px; font-weight:650; }}
  .scopes {{ font-size:12px; color:var(--dim); margin-top:14px; }}
  .scopes code {{ color:var(--ink); }}
  .err {{ background:#2a1417; border:1px solid #5c2a2f; color:var(--err);
          padding:9px 11px; border-radius:8px; font-size:13px; margin-bottom:14px; }}
  .key {{ background:#0e1219; border:1px solid var(--ok); border-radius:9px; padding:12px;
          font-family:ui-monospace,Menlo,monospace; font-size:12px; word-break:break-all;
          margin-top:10px; }}
  .foot {{ color:var(--dim); font-size:11px; margin-top:16px; }}
</style></head><body><div class="card">
  <div class="plat">{e(prof['display'])} &middot; MCP connector</div>
  <h1>{e(prof['consent_title'])}</h1>
  <div class="sub">{e(prof['consent_blurb'])}</div>
  {err_block}
  <form method="post" action="/oauth/authorize">
    <input type="hidden" name="client_id" value="{e(client['client_id'])}">
    <input type="hidden" name="redirect_uri" value="{e(redirect_uri)}">
    <input type="hidden" name="code_challenge" value="{e(code_challenge)}">
    <input type="hidden" name="code_challenge_method" value="{e(code_challenge_method)}">
    <input type="hidden" name="scope" value="{e(scope)}">
    <input type="hidden" name="resource" value="{e(resource)}">
    <input type="hidden" name="state" value="{e(state)}">
    <input type="hidden" name="platform" value="{e(platform)}">

    <label for="identifier">Who is connecting? (optional but recommended)</label>
    <input type="text" id="identifier" name="identifier" autocomplete="username"
           placeholder="you@example.com">
    <div class="hint">Vantage keeps one agent per identity. Leave blank and this
      connector gets one shared agent.</div>

    <fieldset>
      <legend>Vantage agent</legend>
      <div class="opt">
        <input type="radio" id="m_create" name="mode" value="create" checked>
        <label for="m_create" style="margin:0">
          <span>Create a new Vantage agent</span>
          <div class="hint">Mints a fresh identity and API key for this connection.</div>
        </label>
      </div>
      <div class="opt">
        <input type="radio" id="m_link" name="mode" value="link">
        <label for="m_link" style="margin:0">
          <span>Link an existing agent</span>
          <div class="hint">Paste its API key. It is exchanged for a token and never stored here.</div>
        </label>
      </div>
    </fieldset>

    <label for="agent_name">Agent name (if creating)</label>
    <input type="text" id="agent_name" name="agent_name" placeholder="my-chatgpt-agent">

    <label for="existing_key">Existing agent API key (if linking)</label>
    <input type="password" id="existing_key" name="existing_key" placeholder="vantage_..." autocomplete="off">

    <div class="scopes">Granting: <code>{e(scope)}</code></div>
    <button type="submit">Authorize</button>
  </form>
  <div class="foot">Requested by <code>{e(client.get('client_name') or client['client_id'])}</code>
    &middot; redirects to <code>{e(urlparse(redirect_uri).hostname or redirect_uri)}</code></div>
</div></body></html>"""


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize_get(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(""),
    state: str = Query(""),
    resource: str = Query(""),
):
    if response_type != "code":
        raise HTTPException(400, "only response_type=code is supported")
    if code_challenge_method != "S256":
        raise HTTPException(400, "only code_challenge_method=S256 is supported")
    if not code_challenge:
        raise HTTPException(400, "code_challenge required (PKCE is mandatory)")

    client = await _resolve_client(request, client_id, redirect_uri)
    platform = client.get("platform") or detect_platform(client_id, redirect_uri)
    if not redirect_allowed(client, platform, redirect_uri):
        raise HTTPException(400, "redirect_uri not allowed")

    granted = resolve_scopes(platform, scope)
    return HTMLResponse(_consent_html(
        request, client=client, platform=platform, redirect_uri=redirect_uri,
        code_challenge=code_challenge, code_challenge_method=code_challenge_method,
        scope=granted, resource=resource or _resource_for(request), state=state,
    ))


@router.post("/oauth/authorize")
async def authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form("S256"),
    scope: str = Form(""),
    resource: str = Form(""),
    state: str = Form(""),
    platform: str = Form(""),
    mode: str = Form("create"),
    agent_name: str = Form(""),
    existing_key: str = Form(""),
    identifier: str = Form(""),
):
    client = await _resolve_client(request, client_id, redirect_uri)
    platform = platform or client.get("platform") or detect_platform(client_id, redirect_uri)
    if not redirect_allowed(client, platform, redirect_uri):
        raise HTTPException(400, "redirect_uri not allowed")

    prof = get_profile(platform)
    granted = resolve_scopes(platform, scope)

    # ── resolve the acting agent ──────────────────────────────────────────────
    # Subject: an explicit identifier gives one agent per human. Without one the
    # connector itself is the subject, which is the only stable signal a
    # platform that asserts no end-user identity can offer.
    ident = (identifier or "").strip().lower()
    subject = f"{platform}:{ident}" if ident else f"{platform}:client:{client_id}"
    display = ident or (client.get("client_name") or platform)

    agent_id: Optional[int] = None

    if mode == "link":
        key = (existing_key or "").strip()
        found = await store.agent_by_key(key) if key else None
        if not found:
            return HTMLResponse(_consent_html(
                request, client=client, platform=platform, redirect_uri=redirect_uri,
                code_challenge=code_challenge, code_challenge_method=code_challenge_method,
                scope=granted, resource=resource, state=state,
                error="That API key did not match a Vantage agent."), status_code=400)
        agent_id = int(found["id"])
        await store.bind_identity(platform, subject, agent_id, display)
        await store.audit("identity_linked", platform, client_id, agent_id, ident)
    else:
        # Reuse an existing identity FIRST. Without this, every reconnect or
        # re-authorization would mint a fresh Vantage agent for the same person,
        # and a platform that re-runs the flow on token expiry would grow the
        # agent table without bound.
        existing_link = await store.lookup_identity(platform, subject)
        if existing_link:
            candidate = await store.agent_by_id(int(existing_link["agent_id"]))
            if candidate:
                agent_id = int(candidate["id"])
                await store.bind_identity(platform, subject, agent_id, display)
            # if the linked agent is gone (deleted), fall through and re-mint
        if agent_id is None:
            if mode != "create":
                raise HTTPException(400, "no Vantage agent resolved for this connection")
            if SCOPE_ACCOUNT not in granted.split():
                raise HTTPException(403, "client is not permitted to create Vantage accounts")
            name = (agent_name or "").strip() or f"{platform}-agent"
            new_id, _new_key = await store.create_agent_account(
                name, bio=f"Self-serve {prof['display']} connector account")
            agent_id = int(new_id)
            await store.bind_identity(platform, subject, agent_id, display)
            await store.audit("account_created", platform, client_id, agent_id, name)

    if agent_id is None:
        raise HTTPException(400, "no Vantage agent resolved for this connection")

    await store.touch_identity(platform, subject)
    await store.touch_client(client_id)

    code = await store.create_code(
        client_id=client_id, agent_id=int(agent_id), redirect_uri=redirect_uri,
        code_challenge=code_challenge, code_challenge_method=code_challenge_method,
        scope=granted, resource=resource or _resource_for(request), platform=platform,
    )
    await store.audit("authorize_code_issued", platform, client_id, int(agent_id), mode)

    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(params), status_code=302)


# ── token ──────────────────────────────────────────────────────────────────────

async def _authenticate_client(client_id: str, client_secret: Optional[str]) -> dict:
    client = await store.get_client(client_id)
    if not client:
        raise HTTPException(401, "unknown client")
    stored = client.get("client_secret_hash")
    if stored:
        if not client_secret:
            raise HTTPException(401, "client_secret required")
        if store._sha256(client_secret) != stored:
            raise HTTPException(401, "invalid client_secret")
    return client


@router.post("/oauth/token")
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
):
    client = await _authenticate_client(client_id, client_secret or None)
    platform = client.get("platform") or "generic"

    if grant_type == "authorization_code":
        if not code:
            raise HTTPException(400, "code required")
        rec = await store.consume_code(code)
        if not rec:
            await store.audit("token_code_rejected", platform, client_id)
            raise HTTPException(400, "invalid, expired or already-used code")
        if rec["client_id"] != client_id:
            raise HTTPException(400, "code was issued to a different client")
        if rec["redirect_uri"] != redirect_uri:
            raise HTTPException(400, "redirect_uri does not match the authorization request")

        # PKCE S256: BASE64URL(SHA256(verifier)) must equal the stored challenge.
        import base64
        import hashlib
        if not code_verifier:
            raise HTTPException(400, "code_verifier required")
        digest = hashlib.sha256(code_verifier.encode()).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if not secrets.compare_digest(computed, rec["code_challenge"]):
            await store.audit("token_pkce_failed", platform, client_id, rec["agent_id"])
            raise HTTPException(400, "PKCE verification failed")

        want_refresh = SCOPE_OFFLINE in (rec["scope"] or "")
        out = await store.issue_token(
            client_id=client_id, agent_id=int(rec["agent_id"]), scope=rec["scope"],
            resource=rec["resource"], platform=platform, with_refresh=want_refresh,
        )
        await store.audit("token_issued", platform, client_id, int(rec["agent_id"]),
                          rec["scope"])
        return JSONResponse(out)

    if grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(400, "refresh_token required")
        out = await store.refresh_access_token(refresh_token, client_id)
        if not out:
            raise HTTPException(400, "invalid, expired or revoked refresh_token")
        return JSONResponse(out)

    raise HTTPException(400, f"unsupported grant_type: {grant_type}")


@router.post("/oauth/introspect")
async def introspect(token: str = Form(...)):
    """RFC 7662. Only reveals a presence/existence answer plus a non-sensitive
    agent id -- never scopes of someone else's token unless they present it."""
    rec = await store.resolve_access_token(token)
    if not rec:
        return JSONResponse({"active": False})
    return JSONResponse({
        "active": True,
        "scope": rec["scope"],
        "client_id": rec["client_id"],
        "token_type": "Bearer",
        "exp": rec["expires_at"],
        "iat": rec["issued_at"],
        "aud": rec["resource"],
        "agent_id": rec["agent_id"],
    })


@router.post("/oauth/revoke")
async def revoke(token: str = Form(""), client_id: str = Form(""), client_secret: str = Form("")):
    if client_id:
        await _authenticate_client(client_id, client_secret or None)
    ok = await store.revoke_token(token) if token else False
    await store.audit("token_revoked", "", client_id, None, str(ok))
    # RFC 7009: always 200, even for an unknown token.
    return JSONResponse({"revoked": bool(ok)})


@router.get("/oauth/connectors")
async def list_connectors(request: Request):
    """Platform profile registry, for the UI and for testing harnesses."""
    return {
        "issuer": _base_url(request),
        "resource": _resource_for(request),
        "profiles": {
            k: {
                "display": v["display"],
                "verified": v["verified"],
                "registration_modes": client_registration_modes(k),
                "scopes": v["scopes"],
                "notes": v["notes"],
            }
            for k, v in PROFILES.items()
        },
    }
