"""P1-12: trading-space claims are leases, taken in one atomic UPDATE.

Acceptance: of two simultaneous claims one wins and the other gets 409; an
expired claim can be taken by another member; heartbeat by a non-holder is
403. Plus renew, the 1440-minute clamp and pre-lease claims.
"""
import asyncio
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"Lease_{hashed[:10]}"
    cur = await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "t"))
    aid = cur.lastrowid
    if guild_id is not None:
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'member')",
                         (guild_id, aid, name))
    return {"id": aid, "headers": {"X-Agent-Key": raw}}


@pytest_asyncio.fixture
async def space(client):
    async with aiosqlite.connect(DB_PATH) as db:
        owner = await _agent(db)
        slug = f"lease-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", owner["id"], "owner", f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, owner["id"], "owner"))
        a = await _agent(db, gid)
        b = await _agent(db, gid)
        await db.commit()
    r = await client.post(f"/api/guilds/{slug}/trading-spaces", data={"name": "S"}, headers=owner["headers"])
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    r = await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks", data={"title": "x.calls"},
                          headers=owner["headers"])
    assert r.status_code == 200, r.text
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/tasks/{r.json()['id']}"
    return {"base": base, "task_id": r.json()["id"], "a": a, "b": b, "owner": owner}


async def _set(task_id, sql, *args):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, (*args, task_id))
        await db.commit()


async def test_two_simultaneous_claims_one_wins_one_409(client, space):
    ra, rb = await asyncio.gather(
        client.post(space["base"] + "/claim", headers=space["a"]["headers"]),
        client.post(space["base"] + "/claim", headers=space["b"]["headers"]))
    assert sorted([ra.status_code, rb.status_code]) == [200, 409], (ra.text, rb.text)
    winner = ra if ra.status_code == 200 else rb
    assert winner.json()["status"] == "claimed" and winner.json()["claim_expires_at"]


async def test_same_agent_renews_by_claiming_again(client, space):
    assert (await client.post(space["base"] + "/claim", headers=space["a"]["headers"])).status_code == 200
    assert (await client.post(space["base"] + "/claim", headers=space["a"]["headers"])).status_code == 200
    assert (await client.post(space["base"] + "/claim", headers=space["b"]["headers"])).status_code == 409


async def test_an_expired_claim_can_be_taken_by_another_member(client, space):
    assert (await client.post(space["base"] + "/claim", headers=space["a"]["headers"])).status_code == 200
    await _set(space["task_id"], "UPDATE vantage_tasks SET claim_expires_at=datetime('now','-1 minutes') WHERE id=?")
    r = await client.post(space["base"] + "/claim", headers=space["b"]["headers"])
    assert r.status_code == 200, r.text


async def test_a_pre_lease_claim_expires_after_the_default_lease(client, space):
    await _set(space["task_id"], "UPDATE vantage_tasks SET status='claimed', claimed_by_agent_id=?, "
               "claim_expires_at=NULL, updated_ts=unixepoch() WHERE id=?", space["a"]["id"])
    assert (await client.post(space["base"] + "/claim", headers=space["b"]["headers"])).status_code == 409
    await _set(space["task_id"], "UPDATE vantage_tasks SET updated_ts=unixepoch()-3600 WHERE id=?")
    assert (await client.post(space["base"] + "/claim", headers=space["b"]["headers"])).status_code == 200


async def test_heartbeat_by_a_non_holder_is_403_and_holder_renews(client, space):
    assert (await client.post(space["base"] + "/claim", headers=space["a"]["headers"])).status_code == 200
    assert (await client.post(space["base"] + "/heartbeat", headers=space["b"]["headers"])).status_code == 403
    r = await client.post(space["base"] + "/heartbeat?lease_minutes=60", headers=space["a"]["headers"])
    assert r.status_code == 200 and r.json()["claim_expires_at"]


async def test_lease_is_clamped_to_1440_minutes(client, space):
    assert (await client.post(space["base"] + "/claim?lease_minutes=99999",
                              headers=space["a"]["headers"])).status_code == 200
    async with aiosqlite.connect(DB_PATH) as db:
        mins = (await (await db.execute(
            "SELECT (julianday(claim_expires_at) - julianday('now')) * 1440 FROM vantage_tasks WHERE id=?",
            (space["task_id"],))).fetchone())[0]
    assert 1430 < mins <= 1440


async def test_non_member_still_cannot_claim(client, space, fresh_agent):
    outsider = await fresh_agent()
    r = await client.post(space["base"] + "/claim", headers={"X-Agent-Key": outsider["api_key"]})
    assert r.status_code == 403
