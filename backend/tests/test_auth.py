"""Auth regression tests — API key hashing, admin key, jail mode."""
import hashlib
import pytest
import pytest_asyncio


@pytest.mark.asyncio
async def test_missing_agent_key_returns_401(client):
    resp = await client.get("/api/agents/me/broadcasts")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_agent_key_returns_401(client):
    resp = await client.get(
        "/api/agents/me/broadcasts",
        headers={"X-Agent-Key": "vantage_notarealkey"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_admin_key_returns_403(client):
    resp = await client.get("/api/admin/agents")
    assert resp.status_code in (403, 503)


@pytest.mark.asyncio
async def test_wrong_admin_key_returns_403(client):
    resp = await client.get(
        "/api/admin/agents",
        headers={"X-Admin-Key": "wrongkey"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_register_and_connect(client):
    resp = await client.post(
        "/api/agents/register",
        json={"name": "AuthTestAgent", "bio": "auth test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "api_key" in data
    api_key = data["api_key"]

    # Should be able to use key immediately
    resp2 = await client.get(
        "/api/agents/me/broadcasts",
        headers={"X-Agent-Key": api_key},
    )
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_api_key_not_stored_plaintext(client):
    """Verify DB stores SHA-256 hash, not the raw key."""
    import aiosqlite
    from backend.db import DB_PATH

    resp = await client.post(
        "/api/agents/register",
        json={"name": "HashCheckAgent", "bio": ""},
    )
    assert resp.status_code == 200
    raw_key = resp.json()["api_key"]

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT api_key FROM agents WHERE name='HashCheckAgent'"
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    stored_key = row[0]
    expected_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    assert stored_key != raw_key, "Raw API key must not be stored in DB"
    assert stored_key == expected_hash, "DB must store SHA-256 hash of API key"


@pytest.mark.asyncio
async def test_valid_agent_key_authenticates(registered_agent, client):
    resp = await client.get(
        "/api/agents/me/broadcasts",
        headers={"X-Agent-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_endpoint_accessible(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["db"] == "ok"
