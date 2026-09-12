"""Tests for the twin-protocol receipt indexer.

Validates kind enforcement, F1 quality gate, duplicate idempotency,
filtering, and the full field set for each receipt kind.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _register(client: TestClient, name: str = "TwinIndexAgent") -> str:
    r = client.post("/api/agents/register", data={"name": name, "bio": "twin test"})
    assert r.status_code == 200
    return r.json()["api_key"]


# ── receipt factories ────────────────────────────────────────────────────────

def _capture_receipt(**overrides) -> dict:
    """Minimal valid CaptureReceipt (kind 31020)."""
    r = {
        "kind":        31020,
        "receipt_id":  "rcpt:31020:cap-test-001",
        "f1_score":    0.85,
        "coverage_pct": 92.0,
        "twin_id":     "twin:sha256:" + "a" * 64,
        "agent_id":    "did:vantage:agent:test-001",
        "merkle_root": "b" * 64,
        "identity":    {"agent_id": "did:vantage:agent:test-001", "principal_id": "did:p:1"},
        "outcome":     "success",
    }
    r.update(overrides)
    return r


def _scene_receipt(**overrides) -> dict:
    """Minimal valid SceneReceipt (kind 31030)."""
    r = {
        "kind":       31030,
        "receipt_id": "rcpt:31030:scene-test-001",
        "twin_id":    "twin:sha256:" + "c" * 64,
        "f1_score":   0.80,
        "agent_id":   "did:vantage:agent:scene-001",
        "merkle_root": "d" * 64,
        "outcome":    "success",
    }
    r.update(overrides)
    return r


def _obs_receipt(**overrides) -> dict:
    """Minimal valid FirmwareObservationReceipt (kind 31040)."""
    r = {
        "kind":       31040,
        "receipt_id": "rcpt:31040:obs-test-001",
        "witness_id": "did:vantage:witness:lora:abc",
        "merkle_root": "sha256:" + "e" * 64,
        "outcome":    "validated",
        "timestamp":  1700000000.0,
        "signature":  "stub-sig",
    }
    r.update(overrides)
    return r


def _sim_receipt(**overrides) -> dict:
    """Minimal valid SimulationReceipt (kind 31050)."""
    r = {
        "kind":          31050,
        "receipt_id":    "rcpt:31050:sim-test-001",
        "agent_id":      "did:vantage:agent:sim-001",
        "trajectory_hash": "f" * 64,
        "merkle_root":   "e" * 64,
        "outcome":       "success",
    }
    r.update(overrides)
    return r


# ── happy path ───────────────────────────────────────────────────────────────


def test_ingest_capture_receipt(client):
    key = _register(client, "TwinCap")
    r = client.post("/api/twin-receipts/ingest",
                    json=_capture_receipt(receipt_id="rcpt:31020:cap-001"),
                    headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "accepted"
    assert data["kind"] == 31020
    assert data["kind_name"] == "capture"
    assert data["receipt_id"] == "rcpt:31020:cap-001"


def test_ingest_scene_receipt(client):
    key = _register(client, "TwinScene")
    r = client.post("/api/twin-receipts/ingest",
                    json=_scene_receipt(receipt_id="rcpt:31030:scene-001"),
                    headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == 31030


def test_ingest_observation_receipt(client):
    key = _register(client, "TwinObs")
    r = client.post("/api/twin-receipts/ingest",
                    json=_obs_receipt(receipt_id="rcpt:31040:obs-001"),
                    headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == 31040


def test_ingest_simulation_receipt(client):
    key = _register(client, "TwinSim")
    r = client.post("/api/twin-receipts/ingest",
                    json=_sim_receipt(receipt_id="rcpt:31050:sim-001"),
                    headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == 31050


def test_duplicate_is_idempotent(client):
    key = _register(client, "TwinDup")
    env = _capture_receipt(receipt_id="rcpt:31020:dup-001")
    r1 = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    r2 = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_get_single_receipt(client):
    key = _register(client, "TwinGet")
    env = _capture_receipt(receipt_id="rcpt:31020:get-001")
    client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    r = client.get("/api/twin-receipts/rcpt:31020:get-001", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["receipt_id"] == "rcpt:31020:get-001"
    assert data["kind"] == 31020
    assert "receipt" in data  # raw_json decoded


def test_get_nonexistent_returns_404(client):
    key = _register(client, "TwinMiss")
    r = client.get("/api/twin-receipts/no-such-receipt", headers={"X-Agent-Key": key})
    assert r.status_code == 404


def test_list_returns_receipts(client):
    key = _register(client, "TwinList")
    client.post("/api/twin-receipts/ingest",
                json=_capture_receipt(receipt_id="rcpt:31020:list-001"),
                headers={"X-Agent-Key": key})
    r = client.get("/api/twin-receipts", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert "receipts" in data
    assert "total" in data
    ids = [x["receipt_id"] for x in data["receipts"]]
    assert "rcpt:31020:list-001" in ids


def test_list_filter_by_kind(client):
    key = _register(client, "TwinKindFilter")
    client.post("/api/twin-receipts/ingest",
                json=_capture_receipt(receipt_id="rcpt:31020:kf-cap"),
                headers={"X-Agent-Key": key})
    client.post("/api/twin-receipts/ingest",
                json=_obs_receipt(receipt_id="rcpt:31040:kf-obs"),
                headers={"X-Agent-Key": key})
    r = client.get("/api/twin-receipts?kind=31040", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    kinds = {x["kind"] for x in r.json()["receipts"]}
    assert 31040 in kinds
    assert 31020 not in kinds


def test_list_filter_by_f1_min(client):
    key = _register(client, "TwinF1Filter")
    client.post("/api/twin-receipts/ingest",
                json=_capture_receipt(receipt_id="rcpt:31020:f1-high", f1_score=0.95),
                headers={"X-Agent-Key": key})
    client.post("/api/twin-receipts/ingest",
                json=_capture_receipt(receipt_id="rcpt:31020:f1-low", f1_score=0.78),
                headers={"X-Agent-Key": key})
    r = client.get("/api/twin-receipts?f1_min=0.90", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    scores = [x["f1_score"] for x in r.json()["receipts"] if x["f1_score"] is not None]
    assert all(s >= 0.90 for s in scores), f"unexpected low scores: {scores}"


def test_list_filter_by_twin_id(client):
    key = _register(client, "TwinTwinFilter")
    tid = "twin:sha256:" + "abcd" * 16
    client.post("/api/twin-receipts/ingest",
                json=_capture_receipt(receipt_id="rcpt:31020:tid-001", twin_id=tid),
                headers={"X-Agent-Key": key})
    r = client.get(f"/api/twin-receipts?twin_id={tid}", headers={"X-Agent-Key": key})
    assert r.status_code == 200
    for row in r.json()["receipts"]:
        assert row["twin_id"] == tid


# ── F1 quality gate ───────────────────────────────────────────────────────────


def test_capture_below_f1_gate_rejected(client):
    key = _register(client, "TwinLowF1Cap")
    env = _capture_receipt(receipt_id="rcpt:31020:lowf1", f1_score=0.5)
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "F1 quality gate" in r.json()["detail"]["error"]


def test_scene_below_f1_gate_rejected(client):
    key = _register(client, "TwinLowF1Scene")
    env = _scene_receipt(receipt_id="rcpt:31030:lowf1", f1_score=0.600)
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422


def test_capture_missing_f1_rejected(client):
    key = _register(client, "TwinNoF1")
    env = _capture_receipt(receipt_id="rcpt:31020:nof1")
    del env["f1_score"]
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "f1_score" in r.json()["detail"]["error"]


def test_observation_no_f1_gate(client):
    """Observation receipts (31040) don't require f1_score."""
    key = _register(client, "TwinObsNoGate")
    env = _obs_receipt(receipt_id="rcpt:31040:no-f1-gate")
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200


def test_simulation_no_f1_gate(client):
    """Simulation receipts (31050) don't require f1_score."""
    key = _register(client, "TwinSimNoGate")
    env = _sim_receipt(receipt_id="rcpt:31050:no-f1-gate")
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200


def test_f1_at_exactly_gate_accepted(client):
    """f1_score == 0.777 is exactly at the gate — must be accepted."""
    key = _register(client, "TwinF1Exact")
    env = _capture_receipt(receipt_id="rcpt:31020:f1-exact", f1_score=0.777)
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text


# ── rejection cases ───────────────────────────────────────────────────────────


def test_invalid_kind_rejected(client):
    key = _register(client, "TwinBadKind")
    r = client.post("/api/twin-receipts/ingest",
                    json={"kind": 99999, "receipt_id": "x"},
                    headers={"X-Agent-Key": key})
    assert r.status_code == 422


def test_kind_as_string_rejected(client):
    """Firmware bug: kind='observation' (string) instead of 31040 (int) must be caught."""
    key = _register(client, "TwinStrKind")
    env = _obs_receipt(receipt_id="rcpt:obs-str-kind")
    env["kind"] = "observation"
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422
    assert "31040" in r.json()["detail"]["error"]


def test_missing_receipt_id_rejected(client):
    key = _register(client, "TwinNoId")
    env = _capture_receipt()
    del env["receipt_id"]
    r = client.post("/api/twin-receipts/ingest", json=env, headers={"X-Agent-Key": key})
    assert r.status_code == 422


def test_list_invalid_kind_filter_rejected(client):
    key = _register(client, "TwinBadKindFilter")
    r = client.get("/api/twin-receipts?kind=12345", headers={"X-Agent-Key": key})
    assert r.status_code == 422
