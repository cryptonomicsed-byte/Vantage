"""G0, "Secrets and auth holes" (guild handoff, 2026-11-10).

Acceptance: a non-member gets 403 on every space read and mutation; delete
requires the owner or guild staff; the claimant can never accept their own
work; the MCP tool router applies the same membership and lease rules; no
secret literal remains in the code router.
"""
import hashlib
import json
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G0_{hashed[:10]}"
    cur = await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "t"))
    aid = cur.lastrowid
    if guild_id is not None:
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,?)",
                         (guild_id, aid, name, role))
    return {"id": aid, "name": name, "headers": {"X-Agent-Key": raw}}


@pytest_asyncio.fixture
async def guild(client):
    """A guild with a founder, a staff admin, two plain members and an outsider."""
    async with aiosqlite.connect(DB_PATH) as db:
        founder = await _agent(db)
        slug = f"g0-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        admin = await _agent(db, gid, "admin")
        a = await _agent(db, gid)
        b = await _agent(db, gid)
        outsider = await _agent(db)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "admin": admin, "a": a, "b": b, "outsider": outsider}


async def _trading_space(client, guild, owner="a"):
    r = await client.post(f"/api/guilds/{guild['slug']}/trading-spaces", data={"name": "S"},
                          headers=guild[owner]["headers"])
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _content_space(client, guild, owner="a"):
    r = await client.post(f"/api/guilds/{guild['slug']}/content-spaces", data={"name": "C"},
                          headers=guild[owner]["headers"])
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── trading space: reads and mutations need membership ────────────────────────

async def test_non_member_gets_403_on_every_trading_space_surface(client, guild):
    slug = guild["slug"]
    sid = await _trading_space(client, guild)
    h = guild["outsider"]["headers"]
    base = f"/api/guilds/{slug}/trading-spaces"
    checks = [
        client.get(base, headers=h),
        client.post(base, data={"name": "X"}, headers=h),
        client.get(f"{base}/{sid}", headers=h),
        client.delete(f"{base}/{sid}", headers=h),
        client.get(f"{base}/{sid}/tasks", headers=h),
        client.get(f"{base}/{sid}/pools", headers=h),
        client.get(f"{base}/{sid}/memory", headers=h),
        client.put(f"{base}/{sid}/memory/k", data={"value": "v"}, headers=h),
        client.get(f"{base}/{sid}/artifacts", headers=h),
        client.get(f"{base}/{sid}/receipts", headers=h),
        client.post(f"{base}/{sid}/publish/status", json={"status": "x"}, headers=h),
    ]
    for coro in checks:
        r = await coro
        assert r.status_code == 403, f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"


async def test_trading_space_delete_needs_owner_or_staff(client, guild):
    slug = guild["slug"]
    sid = await _trading_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/trading-spaces/{sid}"
    assert (await client.delete(base, headers=guild["b"]["headers"])).status_code == 403
    assert (await client.delete(base, headers=guild["a"]["headers"])).status_code == 200
    sid2 = await _trading_space(client, guild, owner="a")
    assert (await client.delete(f"/api/guilds/{slug}/trading-spaces/{sid2}",
                                headers=guild["admin"]["headers"])).status_code == 200


async def test_trading_space_owner_cannot_accept_work_they_claimed(client, guild):
    """The self-accept hole: the old guard only ran for non-owners."""
    slug = guild["slug"]
    sid = await _trading_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/tasks"
    r = await client.post(base, data={"title": "t"}, headers=guild["a"]["headers"])
    tid = r.json()["id"]
    assert (await client.post(f"{base}/{tid}/claim", headers=guild["a"]["headers"])).status_code == 200
    assert (await client.post(f"{base}/{tid}/submit", data={"title": "x"}, headers=guild["a"]["headers"])).status_code == 200
    r = await client.post(f"{base}/{tid}/accept", headers=guild["a"]["headers"])
    assert r.status_code == 403 and "claimant" in r.text
    # Guild staff who did not claim it may accept.
    assert (await client.post(f"{base}/{tid}/accept", headers=guild["admin"]["headers"])).status_code == 200


async def test_trading_space_accept_needs_review_state_and_a_reviewer(client, guild):
    slug = guild["slug"]
    sid = await _trading_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/tasks"
    tid = (await client.post(base, data={"title": "t"}, headers=guild["a"]["headers"])).json()["id"]
    assert (await client.post(f"{base}/{tid}/claim", headers=guild["b"]["headers"])).status_code == 200
    # Not yet submitted: nothing to accept.
    assert (await client.post(f"{base}/{tid}/accept", headers=guild["a"]["headers"])).status_code == 409
    assert (await client.post(f"{base}/{tid}/submit", data={"title": "x"}, headers=guild["b"]["headers"])).status_code == 200
    # A plain member who is neither owner nor staff cannot review.
    async with aiosqlite.connect(DB_PATH) as db:
        c = await _agent(db, guild["id"])
        await db.commit()
    assert (await client.post(f"{base}/{tid}/accept", headers=c["headers"])).status_code == 403
    assert (await client.post(f"{base}/{tid}/accept", headers=guild["a"]["headers"])).status_code == 200


async def test_trading_space_publish_claim_rejects_unknown_task(client, guild):
    slug = guild["slug"]
    sid = await _trading_space(client, guild)
    r = await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/publish/claim",
                          json={"task_id": "zz-nonexistent"}, headers=guild["a"]["headers"])
    assert r.status_code == 404


# ── content space ─────────────────────────────────────────────────────────────

async def test_non_member_gets_403_on_every_content_space_surface(client, guild):
    slug = guild["slug"]
    sid = await _content_space(client, guild)
    h = guild["outsider"]["headers"]
    base = f"/api/guilds/{slug}/content-spaces"
    checks = [
        client.get(base, headers=h),
        client.post(base, data={"name": "X"}, headers=h),
        client.get(f"{base}/{sid}", headers=h),
        client.delete(f"{base}/{sid}", headers=h),
        client.get(f"{base}/{sid}/projects", headers=h),
        client.post(f"{base}/{sid}/projects", data={"title": "p"}, headers=h),
        client.get(f"{base}/{sid}/tasks", headers=h),
        client.post(f"{base}/{sid}/tasks", data={"title": "t"}, headers=h),
        client.get(f"{base}/{sid}/assets", headers=h),
        client.get(f"{base}/{sid}/memory", headers=h),
        client.get(f"{base}/{sid}/artifacts", headers=h),
        client.get(f"{base}/{sid}/receipts", headers=h),
    ]
    for coro in checks:
        r = await coro
        assert r.status_code == 403, f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"


async def test_content_space_delete_needs_owner_or_staff(client, guild):
    slug = guild["slug"]
    sid = await _content_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/content-spaces/{sid}"
    assert (await client.delete(base, headers=guild["b"]["headers"])).status_code == 403
    assert (await client.delete(base, headers=guild["founder"]["headers"])).status_code == 200


async def test_content_task_loop_has_leases_and_separation_of_duties(client, guild):
    slug = guild["slug"]
    sid = await _content_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/content-spaces/{sid}/tasks"
    r = await client.post(base, data={"title": "cut a trailer"}, headers=guild["a"]["headers"])
    assert r.status_code == 200 and r.json()["status"] == "brief_submitted"
    tid = r.json()["id"]

    # The board gets an envelope, not a bare array.
    body = (await client.get(base, headers=guild["a"]["headers"])).json()
    assert set(body) == {"tasks", "total"} and body["total"] == 1

    # b claims; a cannot steal it while the lease is live.
    r = await client.post(f"{base}/{tid}/claim", headers=guild["b"]["headers"])
    assert r.status_code == 200 and r.json()["status"] == "in_production" and r.json()["claim_expires_at"]
    assert (await client.post(f"{base}/{tid}/claim", headers=guild["a"]["headers"])).status_code == 409

    # Only the claimer submits; nothing to accept before that.
    assert (await client.post(f"{base}/{tid}/submit", data={"title": "v1"}, headers=guild["a"]["headers"])).status_code == 403
    assert (await client.post(f"{base}/{tid}/accept", headers=guild["a"]["headers"])).status_code == 409
    assert (await client.post(f"{base}/{tid}/submit", data={"title": "v1"}, headers=guild["b"]["headers"])).status_code == 200

    # The claimant cannot accept; a plain member cannot accept; the owner can.
    assert (await client.post(f"{base}/{tid}/accept", headers=guild["b"]["headers"])).status_code == 403
    async with aiosqlite.connect(DB_PATH) as db:
        c = await _agent(db, guild["id"])
        await db.commit()
    assert (await client.post(f"{base}/{tid}/accept", headers=c["headers"])).status_code == 403
    r = await client.post(f"{base}/{tid}/accept", headers=guild["a"]["headers"])
    assert r.status_code == 200 and r.json()["status"] == "published"


async def test_content_submit_after_lease_expiry_is_409(client, guild):
    slug = guild["slug"]
    sid = await _content_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/content-spaces/{sid}/tasks"
    tid = (await client.post(base, data={"title": "t"}, headers=guild["a"]["headers"])).json()["id"]
    assert (await client.post(f"{base}/{tid}/claim", headers=guild["b"]["headers"])).status_code == 200
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE vantage_tasks SET claim_expires_at=datetime('now','-1 minutes') WHERE id=?", (tid,))
        await db.commit()
    r = await client.post(f"{base}/{tid}/submit", data={"title": "late"}, headers=guild["b"]["headers"])
    assert r.status_code == 409 and "expired" in r.text


# ── MCP tool router ───────────────────────────────────────────────────────────

async def _call(client, headers, name, **arguments):
    r = await client.post("/api/mcp/tools/call", json={"name": name, "arguments": arguments}, headers=headers)
    return r


async def test_mcp_claim_and_submit_follow_the_space_rules(client, guild):
    slug = guild["slug"]
    sid = await _trading_space(client, guild, owner="a")
    base = f"/api/guilds/{slug}/trading-spaces/{sid}/tasks"
    tid = (await client.post(base, data={"title": "t"}, headers=guild["a"]["headers"])).json()["id"]

    # Non-member: 403, and the task is untouched.
    assert (await _call(client, guild["outsider"]["headers"], "vantage_claim_task", task_id=tid)).status_code == 403
    # Member b claims; member a cannot take it over; a cannot submit against it.
    r = await _call(client, guild["b"]["headers"], "vantage_claim_task", task_id=tid)
    assert r.status_code == 200, r.text
    task = json.loads(r.json()["content"][0]["text"])
    assert task["status"] == "claimed" and task["claimed_by_agent_id"] == guild["b"]["id"] and task["claim_expires_at"]
    assert (await _call(client, guild["a"]["headers"], "vantage_claim_task", task_id=tid)).status_code == 409
    assert (await _call(client, guild["a"]["headers"], "vantage_submit_artifact", task_id=tid, title="x")).status_code == 403
    assert (await _call(client, guild["outsider"]["headers"], "vantage_submit_artifact", task_id=tid, title="x")).status_code == 403
    r = await _call(client, guild["b"]["headers"], "vantage_submit_artifact", task_id=tid, title="x")
    assert r.status_code == 200, r.text
    artifact = json.loads(r.json()["content"][0]["text"])
    assert artifact["status"] == "submitted" and artifact["task_id"] == tid
    # Submitting twice is 409, not a second artifact.
    assert (await _call(client, guild["b"]["headers"], "vantage_submit_artifact", task_id=tid, title="x")).status_code == 409


# ── secrets ───────────────────────────────────────────────────────────────────

def test_no_webhook_secret_literal_in_code_router():
    from pathlib import Path
    from backend.config import settings
    src = (Path(__file__).parent.parent / "routers" / "code.py").read_text()
    assert "vantage-stix-webhook" not in src
    assert hasattr(settings, "STIX_WEBHOOK_SECRET")
