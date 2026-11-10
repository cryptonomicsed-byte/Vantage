"""G12, "Lounge, presence, federation, NIP-29" (guild handoff, 2026-11-10).

Acceptance: lounge create returns 200 for a member; a declared presence
older than 24 h reads as offline and is not routable.
"""
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import presence
from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G12_{hashed[:10]}"
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
        slug = f"g12-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        a = await _agent(db, gid)
        outsider = await _agent(db)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "a": a, "outsider": outsider}


async def test_lounge_create_is_200_for_a_member_and_403_otherwise(client, guild):
    base = f"/api/guilds/{guild['slug']}/lounge"
    r = await client.post(base, json={"name": f"room-{secrets.token_hex(2)}"}, headers=guild["a"]["headers"])
    assert r.status_code == 200, r.text
    r = await client.post(base, json={"name": "nope"}, headers=guild["outsider"]["headers"])
    assert r.status_code == 403, r.text
    listed = await client.get(base, headers=guild["a"]["headers"])
    assert listed.status_code == 200


async def test_presence_older_than_a_day_reads_offline(client, guild):
    principal = await coord.get_or_create_agent_principal(guild["a"]["id"])
    await coord.add_membership(guild["id"], principal)
    await presence.set_state(principal_id=principal["id"], state="available")
    fresh = await presence.get_state(principal["id"])
    assert fresh["state"] == "available" and fresh["routable"] and not fresh["stale"]
    assert any(r["principal_id"] == principal["id"] for r in await presence.routable_in_guild(guild["id"]))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE principal_presence SET updated_at=datetime('now','-2 days') WHERE principal_id=?",
                         (principal["id"],))
        await db.commit()
    old = await presence.get_state(principal["id"])
    assert old["state"] == "offline" and not old["routable"] and old["stale"]
    assert not any(r["principal_id"] == principal["id"] for r in await presence.routable_in_guild(guild["id"]))
