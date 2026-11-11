"""G10 (content workspace completion) and G11 (guild control center).

G10: PATCH /content-spaces/{id} persists the provider; PATCH /assets/{id}
runs the publish machine with a reviewer gate. G11: one call returns every
panel the command center needs.
"""
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G10_{hashed[:10]}"
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
        slug = f"g10-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        a = await _agent(db, gid)
        b = await _agent(db, gid)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "a": a, "b": b}


async def test_providers_tab_persists_the_provider(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    base = f"/api/guilds/{slug}/content-spaces"
    sid = (await client.post(base, json={"name": "C"}, headers=h)).json()["id"]
    # The UI posts a form; the owner may bind; an unknown provider is refused.
    r = await client.patch(f"{base}/{sid}", data={"mcp_provider": "higgsfield"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get(f"{base}/{sid}", headers=h)).json()["mcp_provider"] == "higgsfield"
    assert (await client.patch(f"{base}/{sid}", json={"mcp_provider": "nope"}, headers=h)).status_code == 422
    assert (await client.patch(f"{base}/{sid}", json={"mcp_provider": "heygen"}, headers=guild["b"]["headers"])).status_code == 403
    assert (await client.post(base, json={"name": "X", "mcp_provider": "nope"}, headers=h)).status_code == 422


async def test_publish_tab_runs_the_asset_status_machine(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    base = f"/api/guilds/{slug}/content-spaces"
    sid = (await client.post(base, json={"name": "C"}, headers=h)).json()["id"]
    pid = (await client.post(f"{base}/{sid}/projects", json={"title": "p"}, headers=h)).json()["id"]
    aid = (await client.post(f"{base}/{sid}/assets", json={"project_id": pid, "title": "clip"}, headers=h)).json()["id"]
    assets = f"{base}/{sid}/assets/{aid}"
    # A contributor may edit the title but not publish.
    assert (await client.patch(assets, json={"title": "clip v2"}, headers=guild["b"]["headers"])).status_code == 200
    assert (await client.patch(assets, json={"status": "published"}, headers=guild["b"]["headers"])).status_code == 403
    # Scheduling needs a time; the owner (lead) may schedule and publish.
    assert (await client.patch(assets, json={"status": "scheduled"}, headers=h)).status_code == 422
    r = await client.patch(assets, data={"status": "scheduled", "scheduled_at": "2026-12-01T10:00:00Z"}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.patch(assets, data={"status": "published"}, headers=h)
    assert r.status_code == 200 and r.json()["published_by_agent_id"] == guild["a"]["id"]
    # Published is final.
    assert (await client.patch(assets, json={"status": "draft"}, headers=h)).status_code == 409
    rows = (await client.get(f"{base}/{sid}/assets", headers=h)).json()
    assert rows[0]["status"] == "published" and rows[0]["published_ts"]


async def test_control_center_returns_every_panel(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    tid = (await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks", json={"title": "t"}, headers=h)).json()["id"]
    await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks/{tid}/claim", headers=guild["b"]["headers"])
    await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks/{tid}/submit", json={"title": "v"}, headers=guild["b"]["headers"])
    r = await client.get(f"/api/guilds/{slug}/control-center", headers=h)
    assert r.status_code == 200, r.text
    cc = r.json()
    for panel in ("workspaces", "members", "tasks", "mention_inbox", "repos", "receipts", "health", "approvals"):
        assert panel in cc, panel
    assert cc["guild"]["slug"] == slug and cc["guild"]["your_role"] == "member"
    assert any(s["id"] == sid for s in cc["workspaces"]["trading"])
    assert cc["members"]["count"] == 3
    assert cc["tasks"]["totals"].get("review") == 1
    assert any(t["id"] == tid for t in cc["approvals"]["work_in_review"])
    assert cc["health"]["transports"] and "outbox_pending" in cc["health"]
    assert cc["repos"]["open_prs"] is None  # not measured yet, said so
    # A non-member gets nothing.
    async with aiosqlite.connect(DB_PATH) as db:
        outsider = await _agent(db)
        await db.commit()
    assert (await client.get(f"/api/guilds/{slug}/control-center", headers=outsider["headers"])).status_code == 403
