"""P1-13: submit requires membership, a matching space and guild, status
'claimed' and an unexpired lease.

Acceptance: a removed member cannot submit; a submit after lease expiry is
409; the happy path is unchanged.
"""
import aiosqlite
import pytest

from backend.db import DB_PATH
from backend.tests.test_trading_space_lease import space, _set  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio


async def _claim_and(client, space, who="a"):
    r = await client.post(space["base"] + "/claim", headers=space[who]["headers"])
    assert r.status_code == 200, r.text


async def _submit(client, space, who="a"):
    return await client.post(space["base"] + "/submit", data={"title": "intel", "content_text": "{}"},
                             headers=space[who]["headers"])


async def _artifacts(task_id):
    async with aiosqlite.connect(DB_PATH) as db:
        return (await (await db.execute("SELECT COUNT(*) FROM artifacts WHERE task_id=?", (task_id,))).fetchone())[0]


async def test_happy_path_is_unchanged(client, space):
    await _claim_and(client, space)
    r = await _submit(client, space)
    assert r.status_code == 200 and set(r.json()) == {"ok", "artifact_id"} and r.json()["ok"] is True
    async with aiosqlite.connect(DB_PATH) as db:
        st = (await (await db.execute("SELECT status FROM vantage_tasks WHERE id=?", (space["task_id"],))).fetchone())[0]
    assert st == "review" and await _artifacts(space["task_id"]) == 1


async def test_a_removed_member_cannot_submit(client, space):
    await _claim_and(client, space)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guild_members WHERE agent_id=?", (space["a"]["id"],))
        await db.commit()
    r = await _submit(client, space)
    assert r.status_code == 403 and await _artifacts(space["task_id"]) == 0


async def test_submit_after_lease_expiry_is_409(client, space):
    await _claim_and(client, space)
    await _set(space["task_id"], "UPDATE vantage_tasks SET claim_expires_at=datetime('now','-1 minutes') WHERE id=?")
    r = await _submit(client, space)
    assert r.status_code == 409 and "expired" in r.text and await _artifacts(space["task_id"]) == 0


async def test_submit_twice_is_409_not_a_second_artifact(client, space):
    await _claim_and(client, space)
    assert (await _submit(client, space)).status_code == 200
    r = await _submit(client, space)
    assert r.status_code == 409 and await _artifacts(space["task_id"]) == 1


async def test_another_guilds_path_cannot_reach_the_task(client, space):
    await _claim_and(client, space)
    wrong = space["base"].replace("/api/guilds/", "/api/guilds/x", 1)
    r = await client.post(wrong + "/submit", data={"title": "t"}, headers=space["a"]["headers"])
    assert r.status_code in (403, 404) and await _artifacts(space["task_id"]) == 0


async def test_non_claimer_is_still_403(client, space):
    await _claim_and(client, space)
    assert (await _submit(client, space, "b")).status_code == 403
