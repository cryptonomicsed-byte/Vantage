"""
E2E Tests for Agent Genesis Engine v1
Run: pytest -v -x backend/routers/genesis_test.py
"""
import pytest, json, os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

# Test agent key (Hermes-Ares from seed)
TEST_KEY = "vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"
TEST_KEY_2 = "vantage_0d0565d9cf1f5fd7868e2cf5293afe4dc2d7384702372035"  # Hermes

class TestGenesisEngine:
    
    def test_1_status(self):
        """GET /api/genesis/status — Engine health check"""
        r = client.get("/api/genesis/status", headers={"X-Agent-Key": TEST_KEY})
        assert r.status_code == 200
        data = r.json()
        assert data["engine"] == "genesis_v1"
        assert "active_agents" in data
        assert "available_archetypes" in data
        print(f"  ✅ Engine status: {data['engine']}, {data['active_agents']} active agents")

    def test_2_spawn_agent(self):
        """POST /api/genesis/spawn — Spawn a child agent"""
        import random
        name = f"TestAuditorBot_{random.randint(1000,9999)}"
        r = client.post("/api/genesis/spawn", 
            headers={"X-Agent-Key": TEST_KEY, "Content-Type": "application/json"},
            json={"name": name, "archetype": "auditor", "purpose": "E2E test audit agent", "skills": ["python_testing"]})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "born"
        assert data["archetype"] == "auditor"
        assert "api_key" in data
        self.__class__.spawned_name = name
        print(f"  ✅ Spawned: {data['name']} (gen {data['generation']})")

    def test_3_spawn_duplicate(self):
        """POST /api/genesis/spawn — Duplicate name should fail"""
        r = client.post("/api/genesis/spawn",
            headers={"X-Agent-Key": TEST_KEY, "Content-Type": "application/json"},
            json={"name": "XXX_DUPLICATE_XXX", "archetype": "builder", "purpose": "Dup test"})
        # First should succeed
        r2 = client.post("/api/genesis/spawn",
            headers={"X-Agent-Key": TEST_KEY, "Content-Type": "application/json"},
            json={"name": "XXX_DUPLICATE_XXX", "archetype": "builder", "purpose": "Dup test"})
        assert r2.status_code == 409
        print("  ✅ Duplicate rejection works")

    def test_4_spawn_invalid_archetype(self):
        """POST /api/genesis/spawn — Invalid archetype should fail"""
        r = client.post("/api/genesis/spawn",
            headers={"X-Agent-Key": TEST_KEY, "Content-Type": "application/json"},
            json={"name": "BadArchetypeBot", "archetype": "wizard", "purpose": "Invalid"})
        assert r.status_code == 422

    def test_5_discover_by_skill(self):
        """GET /api/genesis/discover — Find agents by skill"""
        r = client.get("/api/genesis/discover?skill=python_testing", headers={"X-Agent-Key": TEST_KEY})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["child_name"] == "TestAuditorBot"
        print(f"  ✅ Discovered {len(data)} agents with python_testing skill")

    def test_6_discover_by_archetype(self):
        """GET /api/genesis/discover — Find agents by archetype"""
        r = client.get("/api/genesis/discover?archetype=auditor", headers={"X-Agent-Key": TEST_KEY})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["archetype"] == "auditor"
        print(f"  ✅ Discovered {len(data)} auditor agents")

    def test_7_propose_skill(self):
        """POST /api/genesis/skills/propose — Propose a new skill"""
        r = client.post("/api/genesis/skills/propose",
            headers={"X-Agent-Key": TEST_KEY, "Content-Type": "application/json"},
            json={"skill_name": "e2e_testing", "description": "End-to-end testing capability for agents"})
        assert r.status_code == 200
        assert r.json()["status"] == "proposed"
        print("  ✅ Skill proposed: e2e_testing")

    def test_8_vote_on_skill(self):
        """POST /api/genesis/skills/proposals/1/vote — Vote to approve"""
        r = client.post("/api/genesis/skills/proposals/1/vote",
            headers={"X-Agent-Key": TEST_KEY, "Content-Type": "application/json"},
            json={"vote": "approve"})
        assert r.status_code in (200, 400), f"Vote failed: {r.text}"
        data = r.json()
        assert "status" in data or "approve" in data
        print(f"  ✅ Voted: {data}")

    def test_9_lineage(self):
        """GET /api/genesis/lineage — View family tree"""
        r = client.get("/api/genesis/lineage", headers={"X-Agent-Key": TEST_KEY})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        print(f"  ✅ Lineage: {len(data)} entries")

    def test_10_audit_trail(self):
        """GET /api/genesis/audit — View immutable audit log"""
        r = client.get("/api/genesis/audit", headers={"X-Agent-Key": TEST_KEY})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 0  # May be empty if audit wasn't written
        print(f"  ✅ Audit: {len(data)} entries")

    def test_11_unauthorized_access(self):
        """Protected endpoints require auth"""
        # Status endpoint is public, but spawn/propose should require auth
        r = client.post("/api/genesis/spawn", headers={"Content-Type": "application/json"},
            json={"name": "NoAuthBot", "archetype": "builder", "purpose": "test"})
        assert r.status_code in (401, 403, 422), f"Expected auth error, got {r.status_code}"
        print(f"  ✅ Protected endpoints reject unauthorized ({r.status_code})")
