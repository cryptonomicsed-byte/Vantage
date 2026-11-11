"""G4, "Space provisioning through coordination" (guild handoff, 2026-11-10).

Acceptance: creating a space returns a provisioned channel; the channel row
carries the space it backs; spaces that predate this get a channel on
startup; the provision route is idempotent and gated.
"""
import hashlib
import secrets
import time
import uuid

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH
from backend.space_provisioning import backfill_space_channels, chat_slug_for

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G4_{hashed[:10]}"
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
        slug = f"g4-{secrets.token_hex(4)}"
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


async def _channel(guild_id, slug):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM guild_channels WHERE guild_id=? AND slug=?", (guild_id, slug))).fetchone()
    return dict(row) if row else None


@pytest.mark.parametrize("kind", ["trading", "content"])
async def test_creating_a_space_provisions_its_channel(client, guild, kind):
    r = await client.post(f"/api/guilds/{guild['slug']}/{kind}-spaces", json={"name": f"{kind} room"},
                          headers=guild["a"]["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["id"]
    assert body["chat_channel_slug"] == chat_slug_for(kind, sid)
    assert "relay_provisioned" in body  # True on a live relay; False (with a note) without one
    ch = await _channel(guild["id"], body["chat_channel_slug"])
    assert ch and ch["space_id"] == sid and ch["collab_type"] == kind and ch["channel_kind"] == "forum"
    space = (await client.get(f"/api/guilds/{guild['slug']}/{kind}-spaces/{sid}", headers=guild["a"]["headers"])).json()
    assert space["chat_channel_slug"] == body["chat_channel_slug"]


async def test_backfill_gives_old_spaces_a_channel(client, guild):
    sid = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trading_spaces (id, guild_slug, name, chain, created_by_agent_id, created_ts) VALUES (?,?,?,?,?,?)",
            (sid, guild["slug"], "Legacy", "solana", guild["a"]["id"], int(time.time())))
        await db.commit()
    fixed = await backfill_space_channels()
    assert fixed >= 1
    async with aiosqlite.connect(DB_PATH) as db:
        slug = (await (await db.execute("SELECT chat_channel_slug FROM trading_spaces WHERE id=?", (sid,))).fetchone())[0]
    assert slug == chat_slug_for("trading", sid)
    ch = await _channel(guild["id"], slug)
    assert ch and ch["space_id"] == sid
    # A second run finds nothing to do for this space.
    assert await backfill_space_channels() == 0 or (await _channel(guild["id"], slug))["id"] == ch["id"]


async def test_provision_route_is_gated_and_idempotent(client, guild):
    r = await client.post(f"/api/guilds/{guild['slug']}/content-spaces", json={"name": "C"}, headers=guild["a"]["headers"])
    sid = r.json()["id"]
    base = f"/api/guilds/{guild['slug']}/content-spaces/{sid}/provision"
    assert (await client.post(base, headers=guild["b"]["headers"])).status_code == 403
    first = await client.post(base, headers=guild["a"]["headers"])
    again = await client.post(base, headers=guild["founder"]["headers"])
    assert first.status_code == 200 and again.status_code == 200
    assert first.json()["channel_id"] == again.json()["channel_id"]
    assert first.json()["chat_channel_slug"] == chat_slug_for("content", sid)
