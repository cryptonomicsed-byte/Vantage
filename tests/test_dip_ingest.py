"""Tests for the DIP envelope ingest router.

Validates that the router correctly enforces the canonical DipEnvelope
shape from the sovereign-stack dip crate, and rejects old/wrong formats.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _register(client: TestClient, name: str = "DipTestAgent") -> str:
    r = client.post("/api/agents/register", data={"name": name, "bio": "dip test"})
    assert r.status_code == 200
    return r.json()["api_key"]


def _make_envelope(**overrides) -> dict:
    """Minimal valid DipEnvelope matching the sovereign-stack dip crate."""
    env = {
        "version":     "dip/1",
        "message_id":  "test-dip-msg-001",
        "timestamp":   1700000000000,
        "ttl":         300,
        "origin":      {"network": "vantage", "address": "did:node:sender", "did": "did:node:sender"},
        "destination": {"network": "vantage", "address": "did:node:receiver", "did": "did:node:receiver"},
        "routing":     [],
        "kind":        "message",
        "payload":     {"hello": "world"},
        "identity": {
            "principal_id": "did:vantage:principal:abc",
            "agent_id":     "did:vantage:agent:abc",
            "session_id":   "sess:abc",
            "execution_id": "exec:abc",
            "receipt_id":   "rcpt:abc",
        },
        "merkle_root": "a" * 64,
        "signature":   "stub-sig",
    }
    env.update(overrides)
    return env


# ── happy path ────────────────────────────────────────────────────────────────


def test_ingest_valid_envelope(client):
    key = _register(client, "DipValid")
    env = _make_envelope(message_id="dip-valid-001")
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "accepted"
    assert data["message_id"] == "dip-valid-001"


def test_ingest_duplicate_is_idempotent(client):
    key = _register(client, "DipDup")
    env = _make_envelope(message_id="dip-dup-001")
    r1 = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    r2 = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r1.status_code == 200
    assert r2.status_code == 200  # duplicate is silently accepted


def test_ingest_with_routing_hops(client):
    key = _register(client, "DipHops")
    hop = {"via": "did:node:relay-01", "timestamp": 1700000001000}
    env = _make_envelope(message_id="dip-hops-001", routing=[hop, hop])
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200


def test_list_envelopes(client):
    key = _register(client, "DipList")
    env = _make_envelope(message_id="dip-list-001")
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    r = client.get("/api/dip/envelopes", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert "envelopes" in data
    assert "total" in data
    ids = [e["message_id"] for e in data["envelopes"]]
    assert "dip-list-001" in ids


def test_get_single_envelope(client):
    key = _register(client, "DipGet")
    env = _make_envelope(message_id="dip-get-001")
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    r = client.get("/api/dip/envelopes/dip-get-001", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    assert r.json()["message_id"] == "dip-get-001"


def test_ack_envelope(client):
    key = _register(client, "DipAck")
    env = _make_envelope(message_id="dip-ack-001")
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    r = client.post("/api/dip/ack/dip-ack-001", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["acknowledged_at"] is not None


def test_get_nonexistent_returns_404(client):
    key = _register(client, "DipMiss")
    r = client.get("/api/dip/envelopes/no-such-id", headers={"X-Agent-Key": key})
    assert r.status_code == 404


def test_list_filter_by_kind(client):
    key = _register(client, "DipFilter")
    client.post("/api/dip/inbound",
                json=_make_envelope(message_id="dip-filter-msg", kind="message"),
                headers={"X-Agent-Key": key})
    client.post("/api/dip/inbound",
                json=_make_envelope(message_id="dip-filter-rcpt", kind="receipt"),
                headers={"X-Agent-Key": key})
    r = client.get("/api/dip/envelopes?kind=receipt", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    kinds = {e["kind"] for e in data["envelopes"]}
    assert "receipt" in kinds
    assert "message" not in kinds


# ── rejection cases ───────────────────────────────────────────────────────────


def test_missing_fields_rejected(client):
    key = _register(client, "DipMissingFields")
    r = client.post("/api/dip/inbound",
                    json={"message_id": "x", "origin": "did:node:a", "destination": "did:node:b"},
                    headers={"X-Agent-Key": key})
    assert r.status_code == 422


def test_wrong_version_rejected(client):
    key = _register(client, "DipWrongVer")
    env = _make_envelope(message_id="dip-ver-001", version="dip/0")
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "version" in r.json()["detail"]["error"]


def test_old_hops_field_rejected(client):
    """The old format used 'hops' instead of 'routing' — must be rejected."""
    key = _register(client, "DipOldHops")
    env = _make_envelope(message_id="dip-oldhops-001")
    del env["routing"]
    env["hops"] = []
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "routing" in r.json()["detail"]["error"]


def test_flat_origin_rejected(client):
    """Old format had origin/destination as plain strings — must be rejected."""
    key = _register(client, "DipFlatOrigin")
    env = _make_envelope(message_id="dip-flatorigin-001")
    env["origin"] = "did:node:sender"  # should be an object
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "origin" in r.json()["detail"]["error"]


def test_bad_merkle_root_rejected(client):
    key = _register(client, "DipBadMerkle")
    env = _make_envelope(message_id="dip-merkle-001", merkle_root="not-hex")
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "merkle_root" in r.json()["detail"]["error"]


def test_missing_identity_fields_rejected(client):
    key = _register(client, "DipNoIdentity")
    env = _make_envelope(message_id="dip-noid-001")
    env["identity"] = {"principal_id": "did:x"}  # missing 4 fields
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "identity" in r.json()["detail"]["error"]


# ── Option<Did> = null (Nostr / Meshtastic addresses) ────────────────────────


def test_nostr_origin_with_null_did_accepted(client):
    """DipAddress.did is Option<String> in Rust — null is valid for Nostr addresses."""
    key = _register(client, "DipNostrNull")
    env = _make_envelope(message_id="dip-nostr-null-001")
    env["origin"] = {"network": "nostr", "address": "npub1testxyz", "did": None}
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"


def test_meshtastic_origin_without_did_accepted(client):
    """Meshtastic addresses never have a DID — omitting it entirely must be accepted."""
    key = _register(client, "DipMeshNull")
    env = _make_envelope(message_id="dip-mesh-null-001")
    env["origin"] = {"network": "meshtastic", "address": "!deadbeef"}
    # no `did` key at all
    r = client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text


def test_origin_null_did_stored_as_null(client):
    """Null DID must be stored as NULL in the DB, not the string 'None'."""
    key = _register(client, "DipNullStore")
    env = _make_envelope(message_id="dip-null-store-001")
    env["origin"] = {"network": "nostr", "address": "npub1store", "did": None}
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})

    r = client.get("/api/dip/envelopes/dip-null-store-001", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data.get("origin_did") != "None", "origin_did must not be the string 'None'"


# ── routing array preservation ────────────────────────────────────────────────


def test_routing_hops_stored_as_json_array(client):
    """The full routing Vec<DipHop> must be stored and returned, not just a count."""
    key = _register(client, "DipRouteStore")
    hop = {
        "node":       {"network": "vantage", "address": "did:node:relay", "did": "did:node:relay"},
        "adapter":    "vantage",
        "timestamp":  1700000001000,
        "latency_ms": 12,
    }
    env = _make_envelope(message_id="dip-route-store-001", routing=[hop])
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})

    r = client.get("/api/dip/envelopes/dip-route-store-001", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    routing = r.json().get("routing", [])
    assert isinstance(routing, list), "routing must be returned as a list"
    assert len(routing) == 1, f"expected 1 hop, got {len(routing)}"


# ── /api/dip/outbound — NAT traversal polling ────────────────────────────────


def test_outbound_returns_envelopes_for_dest_did(client):
    """Envelopes addressed to a DID must appear in the outbound queue for that DID."""
    key = _register(client, "DipOutbound")
    dest_did = "did:node:nat-node-001"
    env = _make_envelope(message_id="dip-outbound-001")
    env["destination"] = {"network": "vantage", "address": dest_did, "did": dest_did}
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})

    r = client.get(f"/api/dip/outbound?did={dest_did}", headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    envelopes = r.json()
    assert isinstance(envelopes, list)
    ids = [e["message_id"] for e in envelopes]
    assert "dip-outbound-001" in ids, f"expected dip-outbound-001 in {ids}"


def test_outbound_drain_marks_acknowledged(client):
    """Draining the outbound queue must mark envelopes as acknowledged (no duplicates)."""
    key = _register(client, "DipDrain")
    dest_did = "did:node:drain-node-001"
    env = _make_envelope(message_id="dip-drain-001")
    env["destination"] = {"network": "vantage", "address": dest_did, "did": dest_did}
    client.post("/api/dip/inbound", json=env, headers={"X-Agent-Key": key})

    # First poll — should get the envelope
    r1 = client.get(f"/api/dip/outbound?did={dest_did}", headers={"X-Agent-Key": key})
    assert len(r1.json()) == 1

    # Second poll — queue is now empty (acknowledged)
    r2 = client.get(f"/api/dip/outbound?did={dest_did}", headers={"X-Agent-Key": key})
    assert len(r2.json()) == 0, "second drain should return empty — envelopes already acknowledged"


def test_outbound_empty_for_unknown_did(client):
    """A DID with no queued envelopes must return an empty list, not 404."""
    key = _register(client, "DipOutEmpty")
    r = client.get("/api/dip/outbound?did=did:node:unknown-xyz", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    assert r.json() == []


def test_outbound_missing_did_param_rejected(client):
    """Calling /api/dip/outbound without ?did= must return 422."""
    key = _register(client, "DipOutNoDid")
    r = client.get("/api/dip/outbound", headers={"X-Agent-Key": key})
    assert r.status_code == 422
