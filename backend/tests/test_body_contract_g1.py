"""G1, "One body contract (MCP-safe)" (guild handoff, 2026-11-10).

Acceptance: every guild mutation accepts JSON and form; the OpenAPI schema
(what fastapi-mcp turns into tools) describes a JSON body for each; no
route under the guild surface still declares a Form parameter; the curated
MCP mount carries the space tools.
"""
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import params as fparams
from fastapi.routing import APIRoute

from backend.body_contract import GUILD_BODY_PREFIXES, form_to_dict
from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


def _guild_routes(app):
    for r in app.router.routes:
        if isinstance(r, APIRoute) and any(r.path.startswith(p) for p in GUILD_BODY_PREFIXES):
            yield r


# ── static: the contract holds for every guild route ─────────────────────────

def test_no_form_parameters_remain_on_the_guild_surface(app):
    offenders = []
    for r in _guild_routes(app):
        for dep in r.dependant.body_params:
            if isinstance(dep.field_info, fparams.Form):
                offenders.append(f"{sorted(r.methods)} {r.path} :: {dep.name}")
    assert not offenders, "Form-only params left on guild routes:\n" + "\n".join(offenders)


def test_every_guild_mutation_documents_a_json_body(app):
    schema = app.openapi()
    missing = []
    for r in _guild_routes(app):
        if not r.dependant.body_params:
            continue
        for m in r.methods:
            op = schema["paths"].get(r.path, {}).get(m.lower())
            if not op:
                continue
            content = op.get("requestBody", {}).get("content", {})
            if "application/json" not in content:
                missing.append(f"{m} {r.path}: {list(content)}")
    assert not missing, "\n".join(missing)


def test_curated_mcp_mount_includes_the_space_tags():
    from backend.mcp_server import GUILD_MCP_TAGS
    for tag in ("trading-space", "content-space", "workspace-tasks", "artifacts", "memory", "lounge"):
        assert tag in GUILD_MCP_TAGS


def test_form_to_dict_drops_empty_and_keeps_repeats():
    assert form_to_dict([("a", "1"), ("b", ""), ("c", "x"), ("c", "y")]) == {"a": "1", "c": ["x", "y"]}


# ── live: the same route from JSON and from form ─────────────────────────────

async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G1_{hashed[:10]}"
    cur = await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "t"))
    aid = cur.lastrowid
    if guild_id is not None:
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,?)",
                         (guild_id, aid, name, role))
    return {"id": aid, "name": name, "headers": {"X-Agent-Key": raw}}


@pytest_asyncio.fixture
async def guild(client):
    async with aiosqlite.connect(DB_PATH) as db:
        founder = await _agent(db)
        slug = f"g1-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        a = await _agent(db, gid)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "a": a}


async def test_create_guild_accepts_json(client):
    async with aiosqlite.connect(DB_PATH) as db:
        who = await _agent(db)
        await db.commit()
    slug = f"zz-g1-{secrets.token_hex(3)}"
    r = await client.post("/api/guilds", json={"slug": slug, "name": "JSON guild"}, headers=who["headers"])
    assert r.status_code == 200, r.text
    assert r.json().get("slug") == slug or r.json().get("ok") or "guild" in r.json()


async def test_trading_space_and_task_from_json_and_form(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    base = f"/api/guilds/{slug}/trading-spaces"
    j = await client.post(base, json={"name": "J", "chain": "solana"}, headers=h)
    f = await client.post(base, data={"name": "F", "chain": "solana", "wallet_id": ""}, headers=h)
    assert j.status_code == 200 and f.status_code == 200, (j.text, f.text)
    for sid in (j.json()["id"], f.json()["id"]):
        tj = await client.post(f"{base}/{sid}/tasks", json={"title": "t", "priority": 70}, headers=h)
        tf = await client.post(f"{base}/{sid}/tasks", data={"title": "t", "priority": "70"}, headers=h)
        assert tj.status_code == 200 and tf.status_code == 200, (tj.text, tf.text)
    tasks = (await client.get(f"{base}/{j.json()['id']}/tasks", headers=h)).json()
    assert tasks["total"] == 2 and all(t["priority"] == 70 for t in tasks["tasks"])


async def test_multipart_form_without_files_is_rewritten(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    r = await client.post(f"/api/guilds/{slug}/content-spaces", files={"name": (None, "M")}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "M"


async def test_memory_put_from_json_and_form(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/memory"
    assert (await client.put(f"{base}/k1", json={"value": "v1", "visibility": "workspace"}, headers=h)).status_code == 200
    assert (await client.put(f"{base}/k2", data={"value": "v2"}, headers=h)).status_code == 200
    rows = (await client.get(base, headers=h)).json()
    assert {r["key"] for r in rows} == {"k1", "k2"}


async def test_validation_still_applies_to_both_bodies(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/pools"
    assert (await client.post(base, json={"name": "p", "quorum_pct": 51}, headers=h)).status_code == 422
    assert (await client.post(base, data={"name": "p", "quorum_pct": "51"}, headers=h)).status_code == 422
    assert (await client.post(base, json={}, headers=h)).status_code == 422
