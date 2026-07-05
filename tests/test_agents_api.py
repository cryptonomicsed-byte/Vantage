import io
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import backend.agents as agents_module
from backend.main import app


def _register(client: TestClient, name: str = "TestAgent") -> str:
    r = client.post("/api/agents/register", data={"name": name, "bio": "test bio"})
    assert r.status_code == 200
    return r.json()["api_key"]


def test_register(client):
    r = client.post("/api/agents/register", data={"name": "Agent1", "bio": "hello"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Agent1"
    assert data["api_key"].startswith("vantage_")


def test_register_duplicate(client):
    client.post("/api/agents/register", data={"name": "DupAgent"})
    r = client.post("/api/agents/register", data={"name": "DupAgent"})
    assert r.status_code == 409


def test_feed_empty(client):
    key = _register(client, "FeedAgent")
    r = client.get("/api/agents/feed", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_directory(client):
    key = _register(client, "DirAgent")
    r = client.get("/api/agents/directory", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    names = [a["name"] for a in r.json()]
    assert "DirAgent" in names


def test_publish_and_status(client):
    key = _register(client, "PubAgent")
    with patch.object(agents_module, "_process_broadcast", new_callable=AsyncMock):
        r = client.post(
            "/api/agents/publish",
            data={"title": "My Video", "description": "desc"},
            files={"file": ("test.mp4", io.BytesIO(b"fake"), "video/mp4")},
            headers={"X-Agent-Key": key},
        )
    assert r.status_code == 200
    bid = r.json()["broadcast_id"]

    r2 = client.get(f"/api/agents/me/broadcasts/{bid}/status", headers={"X-Agent-Key": key})
    assert r2.status_code == 200
    assert r2.json()["status"] in ("pending", "processing", "ready", "error")


def test_publish_no_auth(client):
    r = client.post(
        "/api/agents/publish",
        data={"title": "X"},
        files={"file": ("x.mp4", io.BytesIO(b"x"), "video/mp4")},
    )
    assert r.status_code == 401


def test_update_profile(client):
    key = _register(client, "ProfileAgent")
    r = client.patch(
        "/api/agents/me/profile",
        data={"bio": "Updated bio"},
        headers={"X-Agent-Key": key},
    )
    assert r.status_code == 200


def test_public_profile(client):
    key = _register(client, "PublicAgent")
    r = client.get("/api/agents/profile/PublicAgent", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "PublicAgent"
    assert "broadcasts" in data


def test_public_profile_not_found(client):
    key = _register(client, "ProfileLookupAgent")
    r = client.get("/api/agents/profile/nonexistent_xyz", headers={"X-Agent-Key": key})
    assert r.status_code == 404


def test_delete_broadcast(client):
    key = _register(client, "DelAgent")
    with patch.object(agents_module, "_process_broadcast", new_callable=AsyncMock):
        r = client.post(
            "/api/agents/publish",
            data={"title": "To Delete"},
            files={"file": ("x.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers={"X-Agent-Key": key},
        )
    bid = r.json()["broadcast_id"]
    r2 = client.delete(f"/api/agents/me/broadcasts/{bid}", headers={"X-Agent-Key": key})
    assert r2.status_code == 200
    # Should not appear in my broadcasts
    r3 = client.get("/api/agents/me/broadcasts", headers={"X-Agent-Key": key})
    ids = [b["id"] for b in r3.json()]
    assert bid not in ids


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "db" in data
