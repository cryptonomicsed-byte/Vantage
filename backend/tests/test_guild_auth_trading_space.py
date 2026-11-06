"""Regression tests for guild-membership authorization on trading space mutations.

Before the Phase-0 fix, every POST on /trading-spaces/{id}/tasks, /pools,
etc. checked only agent auth — guild membership was never verified.  An agent
with a valid API key could create tasks, claim them, and commit to pools in
any guild's trading space regardless of membership.

These tests pin the 403-on-non-member behaviour so it cannot silently regress.
"""
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def guild_and_space(client, fresh_agent):
    """Create a guild, trading space, and return owner credentials + IDs."""
    owner = await fresh_agent()
    headers = {"X-Agent-Key": owner["api_key"]}

    # Create guild
    r = await client.post("/api/guilds", json={"name": f"TradingGuild_{owner['name'][:6]}", "description": "test"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    slug = r.json()["slug"]

    # Create trading space
    r = await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "Alpha Space", "description": "test"}, headers=headers)
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
        json={"title": "Sneak task", "description": "should be blocked"},
        headers=outsider["headers"],
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


async def test_owner_can_create_task(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        json={"title": "Owner task", "description": "should succeed"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), f"Owner should create task: {r.text}"


# ── claim_task requires membership ───────────────────────────────────────────

async def test_non_member_cannot_claim_task(client, guild_and_space, outsider, fresh_agent):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]

    # Owner creates a task
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        json={"title": "Claimable task", "description": "open"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()["id"]

    # Outsider tries to claim it
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks/{task_id}/claim",
        headers=outsider["headers"],
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


# ── accept_task: owner check + self-accept block ──────────────────────────────

async def test_task_owner_cannot_accept_their_own_task(client, guild_and_space):
    """An agent who created a task should not be able to accept it themselves —
    that defeats the purpose of a two-party work contract."""
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks",
        json={"title": "Self-accept attempt", "description": "open"},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()["id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/tasks/{task_id}/accept",
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (403, 422), (
        f"Expected 403/422 for self-accept, got {r.status_code}: {r.text}"
    )


# ── create_pool requires membership ──────────────────────────────────────────

async def test_non_member_cannot_create_pool(client, guild_and_space, outsider):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        json={"name": "Sneak pool", "target_usd": 1000, "quorum_pct": 0.6},
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
        json={"name": "Bad quorum", "target_usd": 1000, "quorum_pct": 51},
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
        json={"name": "Zero quorum", "target_usd": 1000, "quorum_pct": 0},
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
        json={"name": "Valid pool", "target_usd": 1000, "quorum_pct": 0.6},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), (
        f"Valid quorum_pct=0.6 should succeed: {r.text}"
    )


# ── commit_to_pool: amount must be positive ───────────────────────────────────

async def test_commit_zero_amount_is_rejected(client, guild_and_space):
    slug = guild_and_space["slug"]
    space_id = guild_and_space["space_id"]

    # Create a pool first
    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools",
        json={"name": "Commit test pool", "target_usd": 1000, "quorum_pct": 0.5},
        headers=guild_and_space["headers"],
    )
    assert r.status_code in (200, 201), r.text
    pool_id = r.json()["id"]

    r = await client.post(
        f"/api/guilds/{slug}/trading-spaces/{space_id}/pools/{pool_id}/commit",
        json={"amount_usd": 0},
        headers=guild_and_space["headers"],
    )
    assert r.status_code == 422, (
        f"Expected 422 for amount_usd=0, got {r.status_code}: {r.text}"
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
