"""Regression tests for guild-membership authorization on trading space mutations.

Before the Phase-0 fix, every POST on /trading-spaces/{id}/tasks, /pools,
etc. checked only agent auth — guild membership was never verified.  An agent
with a valid API key could create tasks, claim them, and commit to pools in
any guild's trading space regardless of membership.

These tests pin the 403-on-non-member behaviour so it cannot silently regress.
"""
import hashlib
import secrets
import uuid

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def guild_and_space(client):
    """Create a guild+trading-space via direct DB insert to bypass rate limits."""
    # Create owner agent (same approach as fresh_agent fixture)
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    agent_name = f"TSAgent_{hashed[:10]}"
    slug = f"ts-{hashed[:8]}"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)",
            (agent_name, hashed, "test"),
        )
        agent_db_id = cur.lastrowid
        guild_key = f"vantage_guild_{secrets.token_hex(24)}"
        cur2 = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, f"TradingGuild_{slug}", "test", agent_db_id, agent_name, guild_key),
        )
        guild_id = cur2.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, agent_db_id, agent_name),
        )
        await db.commit()

    owner = {"name": agent_name, "api_key": raw}
    headers = {"X-Agent-Key": owner["api_key"]}

    # Create trading space via API (not rate-limited)
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces",
        data={"name": "Alpha Space"},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    space_id = r.json()["id"]

    return {"owner": owner, "slug": slug, "space_id": space_id, "headers": headers}


@pytest_asyncio.fixture
async def outsider(fresh_agent):
    """An agent that is not a member of any guild."""
    agent = await fresh_agent()
    return {"agent": agent, "headers": {"X-Agent-Key": agent["api_key"]}}


# ── create_task requires membership ──────────────────────────────────────────

async def test_non_member_cannot_create_task(client, guild_and_space, outsider):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        data={"title": "Sneak task", "description": "should be blocked"},
        headers=outsider["headers"],
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


async def test_owner_can_create_task(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        data={"title": "Owner task", "description": "should succeed"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), f"Owner should create task: {r.text}"


# ── claim_task requires membership ───────────────────────────────────────────

async def test_non_member_cannot_claim_task(client, guild_and_space, outsider, fresh_agent):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        data={"title": "Claimable task", "description": "open"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()["id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks/{task_id}/claim",
        headers=outsider["headers"],
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


# ── accept_task: owner check + self-accept block ──────────────────────────────

async def test_non_owner_cannot_accept_task(client, guild_and_space, outsider):
    """Only the space owner (reviewer) can accept tasks — a non-member outsider
    must be blocked regardless of whether they claim to be the submitter."""
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        data={"title": "Accept-auth check", "description": "open"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()["id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks/{task_id}/accept",
        headers=outsider["headers"],
    )
    assert r.status_code == 403, (
        f"Expected 403 for non-owner accept attempt, got {r.status_code}: {r.text}"
    )


# ── create_pool requires membership ──────────────────────────────────────────

async def test_non_member_cannot_create_pool(client, guild_and_space, outsider):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        data={"name": "Sneak pool", "target_amount": "1000", "quorum_pct": "0.6"},
        headers=outsider["headers"],
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


# ── create_pool: quorum_pct must be a fraction (0, 1] ────────────────────────

async def test_pool_quorum_pct_above_1_is_rejected(client, guild_and_space):
    """quorum_pct is stored as a fraction. A value > 1 (e.g. 51 meaning 51%)
    is a contract violation — no valid fraction exceeds 1."""
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        data={"name": "Bad quorum", "target_amount": "1000", "quorum_pct": "51"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code == 422, (
        f"Expected 422 for quorum_pct=51 (should be fraction), got {r.status_code}: {r.text}"
    )


async def test_pool_quorum_pct_zero_is_rejected(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        data={"name": "Zero quorum", "target_amount": "1000", "quorum_pct": "0"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code == 422, (
        f"Expected 422 for quorum_pct=0, got {r.status_code}: {r.text}"
    )


async def test_pool_quorum_pct_valid_fraction_is_accepted(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        data={"name": "Valid pool", "target_amount": "1000", "quorum_pct": "0.6"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), (
        f"Valid quorum_pct=0.6 should succeed: {r.text}"
    )


# ── commit_to_pool: amount must be positive ───────────────────────────────────

async def test_commit_zero_amount_is_rejected(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        data={"name": "Commit test pool", "target_amount": "1000", "quorum_pct": "0.5"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), r.text
    pool_id = r.json()["id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools/{pool_id}/commit",
        data={"amount": "0"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code == 422, (
        f"Expected 422 for amount=0, got {r.status_code}: {r.text}"
    )


# ── tasks and pools responses are enveloped ───────────────────────────────────

async def test_list_tasks_returns_envelope(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.get(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        headers=guild_and_space["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tasks" in body, f"Response is not enveloped: {list(body.keys())}"
    assert "total" in body, f"'total' missing from envelope: {list(body.keys())}"
    assert isinstance(body["tasks"], list)


async def test_list_pools_returns_envelope(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.get(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        headers=guild_and_space["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "pools" in body, f"Response is not enveloped: {list(body.keys())}"
    assert "total" in body, f"'total' missing from envelope: {list(body.keys())}"
    assert isinstance(body["pools"], list)
