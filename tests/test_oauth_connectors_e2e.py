#!/usr/bin/env python3
"""End-to-end proof of the Vantage OAuth connector layer.

Runs against a SANDBOX database (VANTAGE_DATA_DIR override) so it never touches
real data. Exercises the real router, the real store, and the real get_agent
dependency -- not copies of them.

Proves, in order:
  1  profile validation (platform redirect/client_id allowlists)
  2  DCR client registration
  3  authorize -> consent page
  4  consent submit -> agent created -> code issued
  5  PKCE token exchange
  6  the token actually authenticates a protected route as the right agent
  7  RE-AUTHORIZATION REUSES THE AGENT (the "new agent per reconnect" bug)
  8  refresh rotates and kills the old refresh token
  9  introspection
 10  revocation -> token dead
 11  negative cases: bad redirect, missing PKCE, wrong verifier, code replay
"""
import base64
import hashlib
import os
import secrets
import sys
import tempfile

SANDBOX = tempfile.mkdtemp(prefix="vantage-oauth-test-")
os.environ["VANTAGE_DATA_DIR"] = SANDBOX
os.environ["VANTAGE_MEDIA_DIR"] = os.path.join(SANDBOX, "media")

sys.path.insert(0, os.path.expanduser("~/Vantage"))

import aiosqlite  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.db import DB_PATH  # noqa: E402
from backend.deps import get_agent  # noqa: E402
from backend import oauth_store as store  # noqa: E402
from backend.connector_profiles import (  # noqa: E402
    validate_client_id, validate_redirect, detect_platform, resolve_scopes,
)
from backend.routers import oauth as oauth_router  # noqa: E402

PASS, FAIL = [], []


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))


def pkce():
    v = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


async def make_sandbox_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                bio TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                agent_status TEXT DEFAULT 'active',
                last_seen_at TEXT
            )
        """)
        await db.commit()
    # OAuth tables are deliberately NOT created here. The store must
    # self-initialize on first use -- creating them up front in the harness
    # would hide the lifespan/on_event bug that broke this in production.


# ── build an app with the real router + a route guarded by the real get_agent ──
app = FastAPI()
app.include_router(oauth_router.router)


@app.get("/whoami")
async def whoami(agent: dict = Depends(get_agent)):
    return {"id": agent["id"], "name": agent["name"]}


import asyncio  # noqa: E402
asyncio.get_event_loop().run_until_complete(make_sandbox_schema())

client = TestClient(app, base_url="http://127.0.0.1:9999", follow_redirects=False)
print(f"sandbox: {SANDBOX}\n")

# ── 0. the store must self-initialize (regression guard) ──────────────────────
print("0. schema self-initialization")


async def _oauth_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'oauth%'"
        ) as c:
            return sorted(r[0] for r in await c.fetchall())


before = asyncio.get_event_loop().run_until_complete(_oauth_tables())
check("no oauth tables before first use", before == [], str(before))
# a plain read is enough to trigger initialization -- no app startup involved
asyncio.get_event_loop().run_until_complete(store.get_client("does-not-exist"))
after = asyncio.get_event_loop().run_until_complete(_oauth_tables())
check("store self-initialized its schema on first query", len(after) >= 5, str(after))
check("all five tables present",
      {"oauth_clients", "oauth_codes", "oauth_tokens", "oauth_identities",
       "oauth_audit"}.issubset(set(after)), str(after))

# ── 1. profile validation ─────────────────────────────────────────────────────
print("\n1. profile allowlists")
check("chatgpt redirect accepted",
      validate_redirect("chatgpt", "https://chatgpt.com/connector/oauth/abc123"))
check("chatgpt redirect rejected on other host",
      not validate_redirect("chatgpt", "https://evil.com/connector/oauth/abc"),
      "redirect allowlist is the code-leak guard")
check("codex loopback accepted",
      validate_redirect("codex", "http://127.0.0.1:1455/callback/xyz"))
check("codex rejects localhost DNS name",
      not validate_redirect("codex", "http://localhost:1455/callback/xyz"),
      "localhost is re-pointable, loopback literal is not")
check("codex rejects LAN IP",
      not validate_redirect("codex", "http://192.168.1.50:1455/callback/x"))
check("generic rejects http",
      not validate_redirect("generic", "http://example.com/cb"))
check("generic accepts https",
      validate_redirect("generic", "https://example.com/cb"))
check("CIMD client_id must be the platform host",
      validate_client_id("chatgpt", "https://chatgpt.com/oauth/x/client.json")
      and not validate_client_id("chatgpt", "https://attacker.com/client.json"),
      "otherwise any site could claim to be ChatGPT")
check("platform detection", detect_platform("", "http://127.0.0.1:1/callback/a") == "codex")
check("scope never escalates beyond profile",
      "admin" not in resolve_scopes("chatgpt", "vantage.read admin offline_access"))

# ── 2. DCR ─────────────────────────────────────────────────────────────────────
print("\n2. dynamic client registration")
redir = "http://127.0.0.1:1455/callback/test-cb"
r = client.post("/oauth/register", json={
    "client_name": "Test MCP Client",
    "redirect_uris": [redir],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
})
check("DCR returns 201", r.status_code == 201, f"status={r.status_code}")
reg = r.json() if r.status_code == 201 else {}
cid = reg.get("client_id", "")
check("client_id issued", cid.startswith("vgcl_"), cid[:22])

r = client.post("/oauth/register", json={
    "client_name": "Cleartext", "redirect_uris": ["http://evil.example/cb"]})
check("DCR rejects a cleartext http redirect", r.status_code == 400, f"status={r.status_code}")

# A DCR client must not be able to authorize against a redirect it never
# registered -- otherwise registration is decorative and the code can be
# redirected anywhere the attacker controls.
r = client.get("/oauth/authorize", params={
    "response_type": "code", "client_id": cid,
    "redirect_uri": "https://attacker.example/steal",
    "code_challenge": pkce()[1], "code_challenge_method": "S256"})
check("registered-vs-requested redirect mismatch rejected",
      r.status_code == 400, f"status={r.status_code} (code-leak guard)")

# ── 3/4. authorize ─────────────────────────────────────────────────────────────
print("\n3. authorize + consent")
verifier, challenge = pkce()
params = {
    "response_type": "code", "client_id": cid, "redirect_uri": redir,
    "code_challenge": challenge, "code_challenge_method": "S256",
    "scope": "vantage.read vantage.write vantage.account offline_access",
    "state": "xyz-state", "resource": "http://127.0.0.1:9999/mcp",
}
r = client.get("/oauth/authorize", params=params)
check("consent page renders", r.status_code == 200 and "Connect Vantage" in r.text,
      f"status={r.status_code}")

r = client.post("/oauth/authorize", data={**params, "mode": "create",
                                         "agent_name": "chatgpt-user-alpha",
                                         "identifier": "alpha@example.com"})
check("authorize redirects with code", r.status_code == 302, f"status={r.status_code}")
loc = r.headers.get("location", "")
check("state echoed back", "state=xyz-state" in loc)
code = loc.split("code=")[1].split("&")[0] if "code=" in loc else ""
check("code issued", code.startswith("vgc_"))

# ── 5. PKCE exchange ───────────────────────────────────────────────────────────
print("\n4. PKCE token exchange")
r = client.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code, "redirect_uri": redir,
    "client_id": cid, "code_verifier": verifier})
check("token issued", r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
tok = r.json() if r.status_code == 200 else {}
access, refresh = tok.get("access_token", ""), tok.get("refresh_token", "")
check("access token shape", access.startswith("vgt_"))
check("refresh token issued (offline_access)", refresh.startswith("vgr_"))
check("expires_in present", tok.get("expires_in") == 3600, str(tok.get("expires_in")))

# ── 6. the token authenticates a real route ────────────────────────────────────
print("\n5. bearer token -> real protected route")
r = client.get("/whoami", headers={"Authorization": f"Bearer {access}"})
check("bearer authenticates", r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
who = r.json() if r.status_code == 200 else {}
check("resolves to the created agent", who.get("name") == "chatgpt-user-alpha", str(who))
first_id = who.get("id")

r = client.get("/whoami", headers={"Authorization": "Bearer vgt_bogus"})
check("bogus bearer rejected", r.status_code == 401, f"status={r.status_code}")
r = client.get("/whoami")
check("no credentials rejected", r.status_code == 401, f"status={r.status_code}")

# ── 7. re-authorization must REUSE the agent ───────────────────────────────────
print("\n6. re-authorization reuses the same agent (regression guard)")
v2, c2 = pkce()
p2 = dict(params, code_challenge=c2)
r = client.post("/oauth/authorize", data={**p2, "mode": "create",
                                          "agent_name": "chatgpt-user-alpha",
                                          "identifier": "alpha@example.com"})
code2 = r.headers.get("location", "").split("code=")[1].split("&")[0]
r = client.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code2, "redirect_uri": redir,
    "client_id": cid, "code_verifier": v2})
access2 = r.json().get("access_token", "")
r = client.get("/whoami", headers={"Authorization": f"Bearer {access2}"})
check("same agent on reconnect", r.json().get("id") == first_id,
      f"first={first_id} second={r.json().get('id')}")

# a DIFFERENT human gets a DIFFERENT agent
v3, c3 = pkce()
r = client.post("/oauth/authorize", data={**dict(params, code_challenge=c3),
                                          "mode": "create", "agent_name": "beta",
                                          "identifier": "beta@example.com"})
code3 = r.headers.get("location", "").split("code=")[1].split("&")[0]
r = client.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code3, "redirect_uri": redir,
    "client_id": cid, "code_verifier": v3})
access3 = r.json().get("access_token", "")
r = client.get("/whoami", headers={"Authorization": f"Bearer {access3}"})
check("different identity -> different agent", r.json().get("id") != first_id,
      f"beta={r.json().get('id')} alpha={first_id}")

# ── 8. refresh rotation ────────────────────────────────────────────────────────
print("\n7. refresh rotation")
r = client.post("/oauth/token", data={
    "grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
check("refresh succeeds", r.status_code == 200, f"status={r.status_code} {r.text[:100]}")
new_access = r.json().get("access_token", "")
new_refresh = r.json().get("refresh_token", "")
check("new access token works",
      client.get("/whoami", headers={"Authorization": f"Bearer {new_access}"}).status_code == 200)
r = client.post("/oauth/token", data={
    "grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
check("rotated-away refresh token is dead", r.status_code == 400, f"status={r.status_code}")

# ── 9. introspection ───────────────────────────────────────────────────────────
print("\n8. introspection")
r = client.post("/oauth/introspect", data={"token": new_access})
check("active token introspects true", r.json().get("active") is True, str(r.json())[:120])
check("introspection reports audience",
      r.json().get("aud") == "http://127.0.0.1:9999/mcp", str(r.json().get("aud")))
check("dead token introspects false",
      client.post("/oauth/introspect", data={"token": "vgt_nope"}).json().get("active") is False)

# ── 10. revocation ─────────────────────────────────────────────────────────────
print("\n9. revocation")
r = client.post("/oauth/revoke", data={"token": new_access})
check("revoke returns ok", r.status_code == 200)
check("revoked token now 401",
      client.get("/whoami", headers={"Authorization": f"Bearer {new_access}"}).status_code == 401)
n = asyncio.get_event_loop().run_until_complete(store.revoke_agent_tokens(int(first_id)))
check("revoke_agent_tokens kills the whole connector session", n >= 1, f"revoked={n}")
check("agent-revoked token 401",
      client.get("/whoami", headers={"Authorization": f"Bearer {access}"}).status_code == 401)

# ── 11. negative cases ─────────────────────────────────────────────────────────
print("\n10. negative cases")
v4, c4 = pkce()
r = client.post("/oauth/authorize", data={**dict(params, code_challenge=c4),
                                          "mode": "create", "identifier": "gamma@x.com"})
code4 = r.headers.get("location", "").split("code=")[1].split("&")[0]
r = client.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code4, "redirect_uri": redir,
    "client_id": cid, "code_verifier": "wrong-verifier-entirely"})
check("wrong PKCE verifier rejected", r.status_code == 400, f"status={r.status_code}")
r = client.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code4, "redirect_uri": redir,
    "client_id": cid, "code_verifier": v4})
check("failed exchange BURNS the code (fail closed, no verifier brute-force)",
      r.status_code == 400, f"status={r.status_code}")
r = client.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code4, "redirect_uri": redir,
    "client_id": cid, "code_verifier": v4})
check("code replay rejected", r.status_code == 400, f"status={r.status_code}")

r = client.get("/oauth/authorize", params={**params, "redirect_uri": "https://evil.com/cb"})
check("disallowed redirect rejected at authorize", r.status_code == 400, f"status={r.status_code}")
r = client.get("/oauth/authorize", params={k: v for k, v in params.items()
                                          if k != "code_challenge"})
check("missing PKCE rejected", r.status_code == 422 or r.status_code == 400,
      f"status={r.status_code}")
r = client.get("/oauth/authorize", params={**params, "code_challenge_method": "plain"})
check("PKCE plain rejected", r.status_code == 400, f"status={r.status_code}")

# ── discovery docs ─────────────────────────────────────────────────────────────
print("\n11. discovery")
r = client.get("/.well-known/oauth-authorization-server")
asdoc = r.json()
check("AS metadata advertises CIMD", asdoc.get("client_id_metadata_document_supported") is True)
check("AS metadata advertises S256", asdoc.get("code_challenge_methods_supported") == ["S256"])
check("offline_access advertised (refresh support)",
      "offline_access" in asdoc.get("scopes_supported", []),
      "without this ChatGPT re-auths on every expiry")
check("registration_endpoint advertised", bool(asdoc.get("registration_endpoint")))
check("iss param NOT advertised", "authorization_response_iss_parameter_supported" not in asdoc,
      "Codex hard-fails on an iss mismatch, so we must not claim it")
r = client.get("/.well-known/oauth-protected-resource")
pr = r.json()
check("protected resource metadata has resource", pr.get("resource", "").endswith("/mcp"))
check("protected resource lists our AS", pr.get("authorization_servers") == ["http://127.0.0.1:9999"])
r = client.get("/.well-known/oauth-protected-resource/mcp/guild")
check("path-inserted resource metadata (RFC 9728)",
      r.json().get("resource", "").endswith("/mcp/guild"), r.json().get("resource"))
r = client.get("/oauth/connectors")
check("connector profile registry lists 5 platforms",
      len(r.json().get("profiles", {})) == 5, str(list(r.json().get("profiles", {}))))

# ── audit trail ────────────────────────────────────────────────────────────────
async def audit_count():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM oauth_audit") as c:
            return (await c.fetchone())[0]

n = asyncio.get_event_loop().run_until_complete(audit_count())
check("audit trail written", n > 5, f"{n} events")

print(f"\n{'='*62}\nPASS {len(PASS)}   FAIL {len(FAIL)}")
for f in FAIL:
    print("  FAILED:", f)
print(f"sandbox left at {SANDBOX} (delete it)")
sys.exit(1 if FAIL else 0)
