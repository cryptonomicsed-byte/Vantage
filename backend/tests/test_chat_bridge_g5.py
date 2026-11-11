"""G5, "Chat-task bridge and mention intents" (guild handoff, 2026-11-10).

Acceptance: a mention with /task creates a task and notifies the agent;
task mutations announce themselves in the space channel with a work_ref.
The relay is not reachable under test, so the announcement path is
exercised through the describe/emit functions and the intent handler,
and the relay-first rule (no index without the log) is pinned.
"""
import hashlib
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import task_events
from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G5_{hashed[:10]}"
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
        slug = f"g5-{secrets.token_hex(4)}"
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


def test_describe_carries_state_actor_and_title():
    text = task_events.describe("claimed", "vtask", "abc", "alice", "cut the trailer", "lease until noon")
    assert text.startswith("[task.claimed] vtask:abc claimed by alice") and "cut the trailer" in text


async def test_emit_without_a_relay_changes_nothing_in_the_index(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    space = (await client.get(f"/api/guilds/{slug}/trading-spaces/{sid}", headers=h)).json()
    async with aiosqlite.connect(DB_PATH) as db:
        before = (await (await db.execute("SELECT COUNT(*) FROM channel_messages")).fetchone())[0]
    result = await task_events.emit_for_space(guild_slug=slug, table="trading_spaces", space_id=sid,
                                              event="created", kind="vtask", ref_id="x", actor=guild["a"], title="t")
    async with aiosqlite.connect(DB_PATH) as db:
        after = (await (await db.execute("SELECT COUNT(*) FROM channel_messages")).fetchone())[0]
    # No relay in the test environment: nothing announced, nothing indexed, no error.
    assert result is None and after == before
    assert space["chat_channel_slug"]


async def test_mention_task_intent_creates_a_space_task_and_notifies(client, guild):
    from backend.routers.guild_forum import _apply_mention_intent
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    space = (await client.get(f"/api/guilds/{slug}/trading-spaces/{sid}", headers=h)).json()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        g = await (await db.execute("SELECT * FROM guilds WHERE slug=?", (slug,))).fetchone()
        ch = await (await db.execute("SELECT * FROM guild_channels WHERE slug=? AND guild_id=?",
                                     (space["chat_channel_slug"], guild["id"]))).fetchone()
    author = await coord.get_or_create_agent_principal(guild["a"]["id"])
    target = await coord.get_or_create_agent_principal(guild["b"]["id"])

    out = await _apply_mention_intent(guild=dict(g), channel=dict(ch),
                                      content=f"@{target['display_name']} /task write the x.calls brief",
                                      principal=author, mentioned=[dict(target)])
    assert out and out["intent"] == "task" and out["work_ref"].startswith("vtask:")
    task_id = out["work_ref"].split(":", 1)[1]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        t = await (await db.execute("SELECT * FROM vantage_tasks WHERE id=?", (task_id,))).fetchone()
        n = (await (await db.execute(
            "SELECT COUNT(*) FROM notifications WHERE agent_id=? AND type='task_assigned'", (guild["b"]["id"],)
        )).fetchone())[0]
    assert t and t["workspace_id"] == sid and t["title"] == "write the x.calls brief" and t["status"] == "proposed"
    assert n == 1

    # The task then flows through the normal loop.
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/tasks/{task_id}"
    assert (await client.post(f"{base}/claim", headers=guild["b"]["headers"])).status_code == 200

    # /handoff releases the claim and tells the mentioned agent.
    out = await _apply_mention_intent(guild=dict(g), channel=dict(ch),
                                      content=f"@{author['display_name']} /handoff vtask:{task_id}",
                                      principal=target, mentioned=[dict(author)])
    assert out and out["intent"] == "handoff"
    async with aiosqlite.connect(DB_PATH) as db:
        st, who = await (await db.execute("SELECT status, claimed_by_agent_id FROM vantage_tasks WHERE id=?", (task_id,))).fetchone()
    assert st == "proposed" and who is None


async def test_a_message_without_an_intent_is_not_an_intent(client, guild):
    from backend.routers.guild_forum import _apply_mention_intent
    author = await coord.get_or_create_agent_principal(guild["a"]["id"])
    target = await coord.get_or_create_agent_principal(guild["b"]["id"])
    assert await _apply_mention_intent(guild={"id": guild["id"], "slug": guild["slug"]},
                                       channel={"id": 0, "slug": "x", "space_id": None},
                                       content=f"@{target['display_name']} how is it going",
                                       principal=author, mentioned=[dict(target)]) is None
