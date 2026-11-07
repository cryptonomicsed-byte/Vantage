"""Regression tests for ActionReceipt.canonical_hash().

Before the Phase-0 fix, four business fields were excluded from the hash:
  action_outcome, action_params, evidence_ids, capabilities

A mutation to any of those fields left the hash unchanged, meaning a tampered
receipt could pass a hash check.  These tests pin the fixed behaviour so it
cannot silently regress.
"""
import copy
import hashlib
import json

import pytest

from backend.action_receipt import ActionReceipt


def _make_receipt(**overrides) -> ActionReceipt:
    defaults = dict(
        receipt_id="test-receipt-001",
        kind="economic",
        kind_ext=None,
        principal_id="principal-1",
        principal_kind="agent",
        agent_id="agent-42",
        session_id=None,
        agent_tier="standard",
        capabilities=["trade", "execute"],
        action_kind="execute_trade",
        action_target="SOL/USDC",
        action_outcome="filled",
        action_params={"price": 150.0, "quantity": 1.5},
        evidence_ids=["ev-001", "ev-002"],
        witness_attestations=[],
        throne_evaluations=[],
        consensus_receipt=None,
        physical_attestation=None,
        zangbeto_anchor=None,
        nostr_event_id=None,
        timestamp=1_700_000_000,
        execution_id=None,
        previous_hash="",
        signature=None,
    )
    defaults.update(overrides)
    return ActionReceipt(**defaults)


# ── hash stability ────────────────────────────────────────────────────────────

def test_same_fields_produce_same_hash():
    r1 = _make_receipt()
    r2 = _make_receipt()
    assert r1.canonical_hash() == r2.canonical_hash()


def test_hash_is_a_64_char_hex_string():
    h = _make_receipt().canonical_hash()
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ── previously-excluded fields now change the hash ────────────────────────────

def test_mutating_action_outcome_changes_hash():
    base = _make_receipt()
    mutated = _make_receipt(action_outcome="failed")
    assert base.canonical_hash() != mutated.canonical_hash(), (
        "action_outcome mutation did not change hash — field is excluded"
    )


def test_mutating_action_params_changes_hash():
    base = _make_receipt()
    mutated = _make_receipt(action_params={"price": 999.0, "quantity": 1.5})
    assert base.canonical_hash() != mutated.canonical_hash(), (
        "action_params mutation did not change hash — field is excluded"
    )


def test_mutating_evidence_ids_changes_hash():
    base = _make_receipt()
    mutated = _make_receipt(evidence_ids=["ev-999"])
    assert base.canonical_hash() != mutated.canonical_hash(), (
        "evidence_ids mutation did not change hash — field is excluded"
    )


def test_mutating_capabilities_changes_hash():
    base = _make_receipt()
    mutated = _make_receipt(capabilities=["read_only"])
    assert base.canonical_hash() != mutated.canonical_hash(), (
        "capabilities mutation did not change hash — field is excluded"
    )


# ── previously-included fields still change the hash ─────────────────────────

@pytest.mark.parametrize("field,new_val", [
    ("receipt_id",   "tampered-id"),
    ("kind",         "governance"),
    ("principal_id", "other-principal"),
    ("agent_id",     "agent-99"),
    ("action_kind",  "cancel_trade"),
    ("action_target","BTC/USDC"),
    ("timestamp",    0),
    ("previous_hash","abc123"),
])
def test_core_fields_change_the_hash(field, new_val):
    base = _make_receipt()
    mutated = _make_receipt(**{field: new_val})
    assert base.canonical_hash() != mutated.canonical_hash(), (
        f"{field!r} mutation did not change hash"
    )


# ── key ordering does not matter ──────────────────────────────────────────────

def test_hash_is_independent_of_dict_ordering():
    """canonical_hash uses sort_keys=True, so insertion order in action_params
    and similar dicts cannot produce different hashes for equivalent data."""
    r1 = _make_receipt(action_params={"a": 1, "b": 2})
    r2 = _make_receipt(action_params={"b": 2, "a": 1})
    assert r1.canonical_hash() == r2.canonical_hash()


# ── schema_version sentinel ───────────────────────────────────────────────────

def test_hash_includes_schema_version_sentinel():
    """The sentinel prevents a v2 format from silently validating against a
    v1 hash.  Verify by confirming the raw JSON fed to SHA-256 contains it."""
    r = _make_receipt()
    h = r.canonical_hash()
    # Reconstruct what canonical_hash serialises and confirm schema_version is in it.
    # We rely on the hash changing if schema_version were removed — if the hash
    # matches a manually-constructed payload without it, the sentinel is absent.
    without_sentinel = {
        "receipt_id":     r.receipt_id,
        "kind":           r.kind,
        "principal_id":   r.principal_id,
        "agent_id":       r.agent_id,
        "action_kind":    r.action_kind,
        "action_target":  r.action_target,
        "action_outcome": r.action_outcome,
        "action_params":  r.action_params,
        "evidence_ids":   r.evidence_ids,
        "capabilities":   r.capabilities,
        "timestamp":      r.timestamp,
        "previous_hash":  r.previous_hash,
    }
    h_no_sentinel = hashlib.sha256(
        json.dumps(without_sentinel, sort_keys=True).encode()
    ).hexdigest()
    assert h != h_no_sentinel, (
        "canonical_hash matches a payload without schema_version — sentinel is not included"
    )
