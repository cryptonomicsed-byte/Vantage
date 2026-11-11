"""G2, "Space membership and roles" (guild handoff, 2026-11-10).

Acceptance: the claimant can never accept; a reviewer must hold `review`
on the space; observers can read but not claim. Roles come from the
workspace_memberships row on the space's backing channel, with the space
owner as lead and guild staff as maintainer.
"""
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import workspace_roles as wsr
from backend.db import DB_PATH
from backend.guild_access import effective_space_role

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G2_{hashed[:10]}"
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
        slug = f"g2-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        owner = await _agent(db, gid)
        worker = await _agent(db, gid)
        reviewer = await _agent(db, gid)
        observer = await _agent(db, gid)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "owner": owner, "worker": worker,
            "reviewer": reviewer, "observer": observer}


async def _space_with_roles(client, guild):
    slug = guild["slug"]
    r = await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=guild["owner"]["headers"])
    sid = r.json()["id"]
    space = (await client.get(f"/api/guilds/{slug}/trading-spaces/{sid}", headers=guild["owner"]["headers"])).json()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        ch = await (await db.execute("SELECT id FROM guild_channels WHERE space_id=?", (sid,))).fetchone()
    channel_id = ch["id"]
    for who, role in (("reviewer", "operator"), ("observer", "observer")):
        principal = await coord.get_or_create_agent_principal(guild[who]["id"])
        await wsr.set_role(channel_id=channel_id, principal_id=principal["id"], role=role)
    return sid, space


async def test_effective_roles_resolve_from_the_space_channel(client, guild):
    sid, space = await _space_with_roles(client, guild)
    slug = guild["slug"]
    assert await effective_space_role(slug, space, guild["owner"]) == "lead"
    assert await effective_space_role(slug, space, guild["founder"]) == "maintainer"
    assert await effective_space_role(slug, space, guild["reviewer"]) == "operator"
    assert await effective_space_role(slug, space, guild["observer"]) == "observer"
    assert await effective_space_role(slug, space, guild["worker"]) == "contributor"


async def test_observer_reads_but_cannot_claim_or_post(client, guild):
    sid, _ = await _space_with_roles(client, guild)
    base = f"/api/guilds/{guild['slug']}/trading-spaces/{sid}/tasks"
    tid = (await client.post(base, json={"title": "t"}, headers=guild["owner"]["headers"])).json()["id"]
    obs = guild["observer"]["headers"]
    assert (await client.get(base, headers=obs)).status_code == 200
    assert (await client.post(f"{base}/{tid}/claim", headers=obs)).status_code == 403
    assert (await client.post(base, json={"title": "nope"}, headers=obs)).status_code == 403


async def test_reviewer_role_accepts_and_contributor_cannot(client, guild):
    sid, _ = await _space_with_roles(client, guild)
    base = f"/api/guilds/{guild['slug']}/trading-spaces/{sid}/tasks"
    tid = (await client.post(base, json={"title": "t"}, headers=guild["owner"]["headers"])).json()["id"]
    w = guild["worker"]["headers"]
    assert (await client.post(f"{base}/{tid}/claim", headers=w)).status_code == 200
    assert (await client.post(f"{base}/{tid}/submit", json={"title": "v1"}, headers=w)).status_code == 200
    # The claimant never reviews; a plain contributor cannot; the operator can.
    assert (await client.post(f"{base}/{tid}/accept", headers=w)).status_code == 403
    async with aiosqlite.connect(DB_PATH) as db:
        other = await _agent(db, guild["id"])
        await db.commit()
    assert (await client.post(f"{base}/{tid}/accept", headers=other["headers"])).status_code == 403
    r = await client.post(f"{base}/{tid}/accept", headers=guild["reviewer"]["headers"])
    assert r.status_code == 200, r.text


async def test_workspace_tasks_follow_the_same_rules(client, guild):
    slug = guild["slug"]
    # A workspace channel, created directly (creating through the forum
    # route needs a relay for the relay step; the row is what the roles read).
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO guild_channels (guild_id, slug, name, channel_kind) VALUES (?,?,?,'workspace')",
            (guild["id"], "eng", "Engineering"))
        ws_id = cur.lastrowid
        await db.commit()
    base = f"/api/guilds/{slug}/workspaces/{ws_id}/tasks"
    o, w, f = guild["owner"]["headers"], guild["worker"]["headers"], guild["founder"]["headers"]
    r = await client.post(base, json={"title": "ship it"}, headers=o)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert (await client.post(f"{base}/{tid}/claim", headers=w)).status_code == 200
    assert (await client.post(f"{base}/{tid}/claim", headers=o)).status_code == 409
    assert (await client.post(f"{base}/{tid}/submit", json={"artifact_id": "x"}, headers=o)).status_code == 403
    assert (await client.post(f"{base}/{tid}/submit", json={"artifact_id": "x"}, headers=w)).status_code == 200
    assert (await client.post(f"{base}/{tid}/accept", headers=w)).status_code == 403   # claimant
    assert (await client.post(f"{base}/{tid}/accept", headers=o)).status_code == 403   # contributor
    assert (await client.post(f"{base}/{tid}/accept", headers=f)).status_code == 200   # staff
    listed = (await client.get(base, headers=w)).json()
    assert listed["total"] == 1 and listed["items"][0]["status_canonical"] == "accepted"
