"""Guild Lounge — P1..P7 gates (feature/guild-lounge).

Off-deployment harness: backend.main cannot import on this host (solders is a
Rust wheel with no arm64 build), so these tests drive a MINIMAL FastAPI app
containing exactly the guild-forum router (auth/current_principal), the chat
palette router, and the two lounge routers. Everything the lounge touches —
db.py, coordination, coordination_join, task_router, tasks_db, the slash
executor, connectors — is the real production code.

coincurve is provided by scripts/coincurve_shim.py (pure-python BIP-340,
verified against the official spec vector) copied onto PYTHONPATH as
coincurve.py by shimpath/. Any crypto result that matters in production must
be re-verified against the real coincurve on the deployment box.
"""
import sys
from pathlib import Path

_SHIM = Path(__file__).resolve().parent.parent.parent / "shimpath"
if str(_SHIM) not in sys.path:
    sys.path.insert(0, str(_SHIM))

import json  # noqa: E402
import secrets  # noqa: E402

import aiosqlite  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from backend import coordination as coord  # noqa: E402
from backend import coordination_join as join_mod  # noqa: E402
from backend.slash_executor import init_lounge_db  # noqa: E402
from backend.tasks_db import init_tasks_db  # noqa: E402


async def _insert_guild(founder_id: int, founder_name: str, slug: str) -> int:
    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, founder_id, founder_name, guild_api_key) "
            "VALUES (?,?,?,?,?)",
            (slug, slug, founder_id, founder_name, "vantage_guild_" + secrets.token_hex(8)),
        )
        await db.commit()
        return cur.lastrowid


async def _agent_id(name: str) -> int:
    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (name,))
        row = await cur.fetchone()
        return dict(row)["id"]


async def _membership(guild_id: int, agent_id: int, name: str, role: str = "member"):
    principal = await coord.principal_for_agent_dict({"id": agent_id, "name": name})
    await coord.add_membership(guild_id, principal, role=role)
    return principal


@pytest_asyncio.fixture(scope="module")
async def client():
    """Minimal app: guild-forum (auth deps) + chat palette + lounge routers +
    agents rooms + a mock MCP server (in-process ASGI) for skill tests."""
    from fastapi import APIRouter as MockRouter
    from fastapi import FastAPI, Request

    from backend.db import init_agents_db
    from backend.capability_registry import _ensure_registry_table
    from backend.agents import router as agents_router  # rooms/scratchpad endpoints
    from backend.routers.guild_forum import router as gf_router, chat_router
    from backend.routers.lounge import router as lounge_router, invites_router
    from backend import lounge_mcp

    await init_agents_db()
    await coord.init_coordination_db()
    await join_mod.init_join_db()
    await init_lounge_db()
    await init_tasks_db()
    await _ensure_registry_table()
    await lounge_mcp.init_mcp_db()

    mock = MockRouter()

    @mock.post("/mcp")
    async def mock_mcp(request: Request):
        """Minimal Streamable HTTP MCP: initialize + tools/call, echoing the
        injected credentials header to prove credential injection happened."""
        body = await request.json()
        method = body.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": body.get("id"),
                    "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "serverInfo": {"name": "mock-mcp", "version": "1.0"}}}
        if method == "tools/call":
            params = body.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            auth = request.headers.get("x-api-key", "(no x-api-key header)")
            return {"jsonrpc": "2.0", "id": body.get("id"),
                    "result": {"content": [{"type": "text",
                                            "text": f"mock-{name}:{json.dumps(args)} auth={auth}"}]}}
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32601, "message": "unknown method"}}

    app = FastAPI()
    app.include_router(mock)
    app.include_router(agents_router)
    app.include_router(gf_router)
    app.include_router(chat_router)
    app.include_router(lounge_router)
    app.include_router(invites_router)

    # The MCP client talks to the mock server through this same app.
    lounge_mcp.set_test_transport(ASGITransport(app=app))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def lounge_member(client, fresh_agent):
    """A fresh hosted agent that is a guild member with its own guild + lounge.
    Returns dict with agent info, slug, guild_id, headers."""
    agent = await fresh_agent()
    agent_id = await _agent_id(agent["name"])
    slug = f"lz-{secrets.token_hex(5)}"
    guild_id = await _insert_guild(agent_id, agent["name"], slug)
    await _membership(guild_id, agent_id, agent["name"])
    headers = {"X-Agent-Key": agent["api_key"]}
    return {"agent": agent, "agent_id": agent_id, "slug": slug,
            "guild_id": guild_id, "headers": headers}


async def test_lounge_idempotent(client, lounge_member):
    """P1 gate: two calls return the same room id; lounge is permanent."""
    h = lounge_member["headers"]
    r1 = await client.get(f"/api/guilds/{lounge_member['slug']}/lounge", headers=h)
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    room1 = d1["lounge"]["id"]
    assert d1["permanent"] is True
    assert d1["expires_at"] is None

    r2 = await client.get(f"/api/guilds/{lounge_member['slug']}/lounge", headers=h)
    assert r2.json()["lounge"]["id"] == room1


async def test_lounge_scratchpad(client, lounge_member):
    """P1 gate: scratchpad + WS channel still work on a lounge."""
    h = lounge_member["headers"]
    room_id = (await client.get(f"/api/guilds/{lounge_member['slug']}/lounge", headers=h)).json()["lounge"]["id"]
    r = await client.put(f"/api/agents/rooms/{room_id}/scratchpad/video:script", headers=h,
                         json={"value": "episode 12 outline"})
    assert r.status_code == 200, r.text
    room = (await client.get(f"/api/agents/rooms/{room_id}", headers=h)).json()
    assert any(e["key"] == "video:script" and e["value"] == "episode 12 outline"
               for e in room["scratchpad"])


async def test_slash_status_executes(client, lounge_member):
    """P3 gate: the executor runs a command instead of posting it as chat."""
    h = lounge_member["headers"]
    r = await client.post(f"/api/guilds/{lounge_member['slug']}/lounge/commands",
                          headers=h, json={"content": "/status"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["executed"] is True, d
    assert "Lounge:" in d["output"]


async def test_slash_mode_scope_enforced(client, lounge_member):
    """P2/P3 gate: a member without the video scope gets a soft denial with an
    invite hint; with the scope, the mode activates."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/video"})
    d = r.json()
    assert d["executed"] is False
    assert d.get("denied") == "video"
    assert "join-request" in d.get("invite_hint", "")

    from backend.capability_registry import register_capabilities
    await register_capabilities(lounge_member["agent_id"], ["video", "video:edit"],
                                tools=[], runtime="test", agent_name=lounge_member["agent"]["name"])
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/video"})
    d = r.json()
    assert d["executed"] is True, d
    assert "video" in d.get("active_modes", [])

    # A video-scoped agent cannot open trading (scope, not just membership).
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/trade"})
    d = r.json()
    assert d["executed"] is False and d.get("denied") == "trade"


async def test_task_assign_and_reassign(client, lounge_member, fresh_agent):
    """P4 gate: /task writes a delegation; /reassign closes the old and opens
    a new one; /tasks lists them."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    target = await fresh_agent()
    target_id = await _agent_id(target["name"])
    await _membership(lounge_member["guild_id"], target_id, target["name"])

    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": f"/task @{target['name']} build the demo"})
    d = r.json()
    assert d["executed"] is True, d

    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM guild_task_delegations WHERE task_id=?",
                               (d["task_id"],))
        rows = [dict(r) for r in await cur.fetchall()]
    assert rows and rows[0]["to_agent_name"] == target["name"]

    # reassign to a third member
    third = await fresh_agent()
    third_id = await _agent_id(third["name"])
    await _membership(lounge_member["guild_id"], third_id, third["name"])
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": f"/reassign @{target['name']} @{third['name']}"})
    d = r.json()
    assert d["executed"] is True, d
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM guild_task_delegations WHERE task_id=? ORDER BY created_at",
                               (d["task_id"],))
        rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 2
    assert rows[0]["status"] == "cancelled"
    assert rows[1]["to_agent_name"] == third["name"]

    r = await client.get(f"/api/guilds/{slug}/lounge/tasks", headers=h)
    assert r.json()["executed"] is True


async def test_auto_assign_delegation(client, lounge_member, fresh_agent):
    """P4 gate: /auto creates a task, auto-assigns via task_router, writes a
    delegation row and claims the task."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    worker = await fresh_agent()
    worker_id = await _agent_id(worker["name"])
    await _membership(lounge_member["guild_id"], worker_id, worker["name"])
    from backend.capability_registry import register_capabilities
    await register_capabilities(worker_id, ["video", "video:editing"], tools=[], runtime="test",
                                agent_name=worker["name"])

    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/auto task cut episode 12 tags: video:editing"})
    d = r.json()
    assert d["executed"] is True, d
    assert d.get("assigned") == worker["name"]

    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM guild_tasks WHERE id=?", (d["task_id"],))
        task = dict(await cur.fetchone())
        cur = await db.execute("SELECT * FROM guild_task_delegations WHERE task_id=?",
                               (d["task_id"],))
        deleg = [dict(r) for r in await cur.fetchall()]
    assert task["status"] == "claimed"
    assert task["claimed_by_id"] == worker_id
    assert deleg and deleg[0]["to_agent_id"] == worker_id


async def test_envelopes_offline_delivery(client, lounge_member, fresh_agent):
    """P5 gate: a handoff envelope is queued, delivered on the recipient's
    next GET, and a broadcast envelope reaches everyone."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]

    r = await client.post(f"/api/guilds/{slug}/lounge/messages", headers=h,
                          json={"content": "you take the voice track", "msg_type": "handoff",
                                "to": "someone-else"})
    assert r.status_code == 200, r.text

    # Addressed to someone else: not delivered to us.
    r = await client.get(f"/api/guilds/{slug}/lounge/messages", headers=h)
    assert r.json()["count"] == 0

    # Broadcast envelope (no recipient): delivered on next poll.
    await client.post(f"/api/guilds/{slug}/lounge/messages", headers=h,
                      json={"content": "status update for everyone", "msg_type": "status"})
    r = await client.get(f"/api/guilds/{slug}/lounge/messages", headers=h)
    d = r.json()
    assert d["count"] == 1
    assert d["messages"][0]["msg_type"] == "status"

    # Delivered envelopes are not re-delivered.
    r = await client.get(f"/api/guilds/{slug}/lounge/messages", headers=h)
    assert r.json()["count"] == 0


async def test_invite_single_use_front_door(client, lounge_member):
    """P2 gate: mint → accept seats a hosted agent with a key, membership,
    lounge seat and registered capabilities; replay fails."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    r = await client.post(f"/api/guilds/{slug}/lounge/invite", headers=h, json={"scopes": ["video"]})
    d = r.json()
    assert d["executed"] is True, d
    token = d["token"]

    accept = await client.post(f"/api/invites/{token}/accept",
                               json={"requestType": "agent", "agentName": f"Invited_{secrets.token_hex(4)}",
                                     "capabilities": ["video"]})
    assert accept.status_code == 200, accept.text
    a = accept.json()
    assert a["api_key"].startswith("vantage_")
    assert a["gitea"].get("gitea") == "not configured"
    assert a["lounge"] and a["lounge"]["ws_channel"].startswith("room:")

    # Replay must fail (single-use).
    replay = await client.post(f"/api/invites/{token}/accept",
                               json={"requestType": "agent", "agentName": "Replay"})
    assert replay.status_code in (404, 400), replay.text

    # Capabilities registered on the new agent.
    from backend.capability_registry import get_agent_capabilities
    new_id = await _agent_id(a["agent"])
    caps = await get_agent_capabilities(new_id)
    assert "video" in caps.get("capabilities", [])


async def test_palette_lists_commands(client, lounge_member):
    """P3 gate: the registry-derived palette surfaces the new commands."""
    r = await client.get("/api/chat/commands", headers=lounge_member["headers"])
    assert r.status_code == 200, r.text
    commands = {c["command"] for c in r.json()["commands"]}
    for expected in ("/status", "/video", "/code", "/trade", "/invite", "/task",
                     "/auto", "/tasks", "/reassign", "/handoff", "/lounge",
                     "/connectors", "/skills"):
        assert expected in commands, f"{expected} missing from palette"


async def test_non_member_soft_error(client, lounge_member, fresh_agent):
    """P3 gate: a non-member gets a soft error with the join hint, not a 403
    wall, on the lounge command surface."""
    outsider = await fresh_agent()
    slug = lounge_member["slug"]
    h = {"X-Agent-Key": outsider["api_key"]}
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/status"})
    d = r.json()
    assert d["executed"] is False
    assert d.get("membership") is False
    assert "join-request" in d.get("invite_hint", "")


async def test_native_fallback_code(client, lounge_member):
    """Hybrid model: with no skill registered, /code runs the native pipeline
    (routers/code.py Gitea/Strix surface) — native=True, nothing removed."""
    h = lounge_member["headers"]
    from backend.capability_registry import register_capabilities
    await register_capabilities(lounge_member["agent_id"], ["code"], tools=[],
                                runtime="test", agent_name=lounge_member["agent"]["name"])
    r = await client.post(f"/api/guilds/{lounge_member['slug']}/lounge/commands",
                          headers=h, json={"content": "/code"})
    d = r.json()
    assert d["executed"] is True, d
    assert d.get("native") is True
    assert "code" in d.get("active_modes", [])


async def test_skill_override_and_creds_encrypted(client, lounge_member):
    """Hybrid model: a registered skill overrides the native fallback, runs MCP
    tools, injects sealed credentials as headers, and never stores plaintext."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    from backend.capability_registry import register_capabilities
    await register_capabilities(lounge_member["agent_id"], ["video"], tools=[],
                                runtime="test", agent_name=lounge_member["agent"]["name"])

    # Register a guild-shared MCP connector with credentials.
    r = await client.post(f"/api/guilds/{slug}/lounge/connectors", headers=h,
                          json={"name": "guild-renderer", "server_url": "http://test/mcp",
                                "capability_tag": "video",
                                "credentials": {"X-Api-Key": "sekret-value"}})
    assert r.status_code == 200, r.text
    cid = r.json()["connector"]["id"]

    # Credentials are sealed at rest — no plaintext anywhere in the row.
    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT creds_encrypted FROM mcp_connectors WHERE id=?", (cid,))
        blob = dict(await cur.fetchone())["creds_encrypted"]
    assert "sekret-value" not in blob and blob

    # Register a skill for video mode referencing the connector.
    r = await client.post(f"/api/guilds/{slug}/lounge/skills", headers=h,
                          json={"mode": "video", "name": "render-episode",
                                "steps": [{"connector_id": cid, "tool": "generate",
                                           "arguments": {"prompt": "episode 12"}}]})
    assert r.status_code == 200, r.text

    # /video now runs the skill (not the native AgentTV fallback).
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/video"})
    d = r.json()
    assert d["executed"] is True, d
    assert d.get("skill") == "render-episode"
    assert "mock-generate" in d["output"]
    assert "auth=sekret-value" in d["output"], "credentials were not injected at call time"


async def test_skill_scope_requires_capability(client, lounge_member):
    """Hybrid model: a skill with a capability tag runs only for callers with
    that capability; everyone else gets the native fallback."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    from backend.capability_registry import register_capabilities
    await register_capabilities(lounge_member["agent_id"], ["video"], tools=[],
                                runtime="test", agent_name=lounge_member["agent"]["name"])

    r = await client.post(f"/api/guilds/{slug}/lounge/connectors", headers=h,
                          json={"name": "analytics", "server_url": "http://test/mcp",
                                "capability_tag": "analytics"})
    cid = r.json()["connector"]["id"]
    r = await client.post(f"/api/guilds/{slug}/lounge/skills", headers=h,
                          json={"mode": "video", "name": "scoped-skill",
                                "capability_tag": "analytics",
                                "steps": [{"connector_id": cid, "tool": "analyze",
                                           "arguments": {}}]})
    assert r.status_code == 200, r.text

    # Member has video (mode gate passes) but NOT analytics (skill gate fails)
    # → native fallback, not the skill, not an error.
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/video"})
    d = r.json()
    assert d["executed"] is True, d
    assert d.get("skill") is None
    assert d.get("native") is True

    # Grant analytics → the skill runs now.
    await register_capabilities(lounge_member["agent_id"], ["video", "analytics"], tools=[],
                                runtime="test", agent_name=lounge_member["agent"]["name"])
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/video"})
    d = r.json()
    assert d["executed"] is True, d
    assert d.get("skill") == "scoped-skill"


async def test_slash_skill_registration(client, lounge_member):
    """Skill creation via slash: /skill <mode> <name> <connector_id> [tool]."""
    h = lounge_member["headers"]
    slug = lounge_member["slug"]
    r = await client.post(f"/api/guilds/{slug}/lounge/connectors", headers=h,
                          json={"name": "writer", "server_url": "http://test/mcp"})
    cid = r.json()["connector"]["id"]
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": f"/skill social social-writer {cid} draft"})
    d = r.json()
    assert d["executed"] is True, d
    # /social now invokes the skill.
    r = await client.post(f"/api/guilds/{slug}/lounge/commands", headers=h,
                          json={"content": "/social"})
    d = r.json()
    assert d["executed"] is True
    assert d.get("skill") == "social-writer"
    assert "mock-draft" in d["output"]
