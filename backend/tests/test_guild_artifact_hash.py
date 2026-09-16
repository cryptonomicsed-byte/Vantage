"""Gap (b): content_hash is mandatory AND server-verified.

An artifact with no hash is not cryptographically bound to anything, so a
receipt issued over it attests to nothing. Two distinct failure modes are
covered here:

  1. absent hash            -> FastAPI Form(...) validation, 422
  2. well-formed but WRONG  -> recomputed from content_text, 422

Case 2 is the one that matters. A format check alone would let a submitter
paste any 64-hex string, and the artifact would be silently unbound while
looking correct.
"""
import hashlib
import secrets

import pytest
import pytest_asyncio

from backend import coordination as coord


@pytest_asyncio.fixture(scope="module", autouse=True)
async def coordination_schema(client):
    """coordination tables + the guild task/artifact tables (tasks_db.py)."""
    from backend.tasks_db import init_tasks_db

    await coord.init_coordination_db()
    await init_tasks_db()


@pytest_asyncio.fixture
async def guild(client, fresh_agent):
    """A guild plus its founder agent (same shape as test_guild_forum's)."""
    import aiosqlite
    from backend.db import get_db

    founder = await fresh_agent()
    slug = f"hash-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Hash Test Guild", "b", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) "
            "VALUES (?,?,?,'founder')",
            (guild_id, founder_id, founder["name"]),
        )
        await db.commit()
    return {"slug": slug, "id": guild_id, "founder": founder}


def _hdr(agent):
    return {"X-Agent-Key": agent["api_key"]}


def _b3(b: bytes) -> str:
    import blake3
    return blake3.blake3(b).hexdigest()


async def _make_claimed_task(client, guild):
    """Create a task and claim it, returning the task id."""
    agent = guild["founder"]
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/tasks",
        data={"title": "hash test", "description": "d", "priority": 50},
        headers=_hdr(agent),
    )
    assert resp.status_code == 200, resp.text
    tid = resp.json()["task"]["id"]
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/claim", data={}, headers=_hdr(agent)
    )
    assert resp.status_code == 200, resp.text
    return tid


@pytest.mark.asyncio
async def test_submit_without_content_hash_is_rejected(client, guild):
    """Case 1: the field is required now."""
    tid = await _make_claimed_task(client, guild)
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/submit",
        data={"artifact_kind": "doc", "artifact_title": "no hash", "content_text": "hello"},
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 422, resp.text
    assert "content_hash" in resp.text


@pytest.mark.asyncio
async def test_submit_with_wrong_content_hash_is_rejected(client, guild):
    """Case 2: well-formed but does not match the content."""
    tid = await _make_claimed_task(client, guild)
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/submit",
        data={
            "artifact_kind": "doc",
            "artifact_title": "wrong hash",
            "content_text": "this content is real",
            "content_hash": "0" * 64,
        },
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 422, resp.text
    assert "does not match" in resp.text


@pytest.mark.asyncio
async def test_submit_with_malformed_hash_is_rejected(client, guild):
    """Not even the right shape."""
    tid = await _make_claimed_task(client, guild)
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/submit",
        data={
            "artifact_kind": "doc",
            "artifact_title": "short hash",
            "content_text": "x",
            "content_hash": "abc",
        },
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_submit_with_correct_hash_succeeds_and_stores_it(client, guild):
    """The happy path, and the hash must survive into the row."""
    tid = await _make_claimed_task(client, guild)
    content = "REAL SANDBOX EXECUTION OUTPUT\nline two"
    h = _b3(content.encode())
    resp = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/submit",
        data={
            "artifact_kind": "tool_output",
            "artifact_title": "correct hash",
            "content_text": content,
            "content_hash": h,
        },
        headers=_hdr(guild["founder"]),
    )
    assert resp.status_code == 200, resp.text

    got = await client.get(
        f"/api/guilds/{guild['slug']}/tasks/{tid}", headers=_hdr(guild["founder"])
    )
    arts = got.json()["artifacts"]
    assert len(arts) == 1
    assert arts[0]["content_hash"] == h
    assert arts[0]["content_text"] == content
    assert got.json()["task"]["status"] == "review"


@pytest.mark.asyncio
async def test_status_guard_still_applies(client, guild):
    """A submitted (review) task cannot be submitted again."""
    tid = await _make_claimed_task(client, guild)
    content = "once"
    h = _b3(content.encode())
    first = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/submit",
        data={"artifact_kind": "doc", "artifact_title": "t", "content_text": content,
              "content_hash": h},
        headers=_hdr(guild["founder"]),
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/guilds/{guild['slug']}/tasks/{tid}/submit",
        data={"artifact_kind": "doc", "artifact_title": "t2", "content_text": content,
              "content_hash": h},
        headers=_hdr(guild["founder"]),
    )
    assert second.status_code == 409, second.text
