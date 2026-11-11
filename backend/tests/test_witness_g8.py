"""G8, "Review, witness and receipts" (guild handoff, 2026-11-10).

Acceptance: witnesses are guild members; rounds expire; approval waits for
the witness round; the execution-receipts list is reachable on its own path.
"""
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH
from backend.witness_store import expire_stale_rounds, open_witness_round, select_witness_pool

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G8_{hashed[:10]}"
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
        slug = f"g8-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        members = [await _agent(db, gid) for _ in range(4)]
        outsider = await _agent(db)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "members": members, "outsider": outsider}


async def test_witness_pool_is_drawn_from_the_guild(client, guild):
    submitter = guild["members"][0]
    pool = await select_witness_pool(submitter["id"], 3, guild_slug=guild["slug"])
    member_ids = {m["id"] for m in guild["members"]} | {guild["founder"]["id"]}
    assert pool and set(pool) <= member_ids and submitter["id"] not in pool
    # The same draw without a guild may pick anyone; the scoped one never
    # picks the outsider however many times it runs.
    for _ in range(10):
        assert guild["outsider"]["id"] not in await select_witness_pool(submitter["id"], 3, guild_slug=guild["slug"])


async def test_rounds_expire_and_witnesses_are_notified(client, guild):
    submitter = guild["members"][0]
    r = await open_witness_round("job", 999_001, submitter["id"], guild_slug=guild["slug"], ttl_seconds=1)
    assert r["guild_slug"] == guild["slug"] and r["witnesses"]
    async with aiosqlite.connect(DB_PATH) as db:
        n = (await (await db.execute(
            "SELECT COUNT(*) FROM notifications WHERE type='witness_assigned' AND subject_id=?", (r["round_id"],)
        )).fetchone())[0]
        await db.execute("UPDATE witness_rounds SET expires_at=unixepoch()-1 WHERE id=?", (r["round_id"],))
        await db.commit()
    assert n == len(r["witnesses"])
    assert await expire_stale_rounds() >= 1
    async with aiosqlite.connect(DB_PATH) as db:
        st = (await (await db.execute("SELECT status FROM witness_rounds WHERE id=?", (r["round_id"],))).fetchone())[0]
    assert st == "expired"
    listed = await client.get("/api/witness/rounds", params={"status": "expired"}, headers=submitter["headers"])
    assert listed.status_code == 200 and any(x["id"] == r["round_id"] for x in listed.json()["items"])


async def test_job_approval_waits_for_the_witness_round(client, guild):
    poster, worker = guild["founder"], guild["members"][0]
    r = await client.post("/api/jobs", json={"guild_slug": guild["slug"], "title": "j",
                                              "tasks": [{"title": "t1"}]}, headers=poster["headers"])
    assert r.status_code in (200, 201), r.text
    job = r.json()
    job_id, task_id = job["id"], job["tasks"][0]["id"]
    assert (await client.post(f"/api/jobs/{job_id}/tasks/{task_id}/claim", headers=worker["headers"])).status_code == 200
    s = await client.post(f"/api/jobs/{job_id}/tasks/{task_id}/submit",
                          json={"result_description": "done"}, headers=worker["headers"])
    assert s.status_code == 200, s.text
    rnd = s.json().get("witness_round")
    assert rnd and rnd["guild_slug"] == guild["slug"]
    # Open round: the poster cannot approve yet.
    a = await client.post(f"/api/jobs/{job_id}/tasks/{task_id}/approve", headers=poster["headers"])
    assert a.status_code == 409 and "witness" in a.text.lower()
    # Once the round expires without quorum, the poster may approve.
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE witness_rounds SET expires_at=unixepoch()-1 WHERE id=?", (rnd["round_id"],))
        await db.commit()
    a = await client.post(f"/api/jobs/{job_id}/tasks/{task_id}/approve", headers=poster["headers"])
    assert a.status_code == 200, a.text


async def test_execution_receipts_have_their_own_path(client, guild):
    h = guild["founder"]["headers"]
    r = await client.get(f"/api/guilds/{guild['slug']}/execution-receipts", headers=h)
    assert r.status_code == 200, r.text
    chain = await client.get(f"/api/guilds/{guild['slug']}/receipts", headers=h)
    assert chain.status_code in (200, 403)  # the forum's receipt-chain route, unchanged
