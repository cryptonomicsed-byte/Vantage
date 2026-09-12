"""Action Receipt Protocol v1 — canonical envelope for all ecosystem receipts.

ARP v1 wraps the 6 existing receipt formats into one consistent structure that
implements the 5-primitive chain: Principal → Capability → Action → Evidence → Receipt.

This module does NOT replace existing receipt stores.  Each subsystem continues
to write its native format.  ARP wraps are used for:
  - Cross-repo event correlation (link UCX compute receipt to VCP session receipt)
  - Zàngbétò anchoring (one hash to anchor per operation)
  - Twelve Thrones evaluation requests
  - Federation via DIP (external peers receive ARP, not internal schemas)

Kinds (mirrors arp-types/src/receipt.rs ReceiptKind):
  compute, vcp_session, emission, twin_capture, twin_scene,
  simulation, governance, economic, agent_lifecycle, witness,
  mesh_event, custom
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# ── canonical envelope ────────────────────────────────────────────────────────

@dataclass
class ActionReceipt:
    receipt_id:    str
    kind:          str
    kind_ext:      Optional[str]

    # 5-primitive identity chain
    principal_id:  str
    principal_kind: str   # human | agent | daemon | contract | federated
    agent_id:      str
    session_id:    Optional[str]
    agent_tier:    Optional[str]
    capabilities:  list[dict]

    # what happened
    action_kind:   str     # verb: "submit_job", "join_mesh", "emit_ase", …
    action_target: str     # entity acted upon
    action_outcome: str    # "success" | "failure" | "partial" | "revoked"
    action_params: dict

    # evidence
    evidence_ids:         list[str]
    witness_attestations: list[dict]
    throne_evaluations:   list[dict]

    # settlement
    consensus_receipt:    Optional[dict]
    physical_attestation: Optional[dict]
    zangbeto_anchor:      Optional[str]
    nostr_event_id:       Optional[str]

    # chain
    timestamp:      int   # Unix seconds
    execution_id:   Optional[str]
    previous_hash:  Optional[str]

    signature:      str   # Ed25519 hex — populated by signing layer

    def canonical_hash(self) -> str:
        """SHA-256 over stable fields (matches arp-types receipt.rs hash())."""
        data = json.dumps({
            "receipt_id":    self.receipt_id,
            "kind":          self.kind,
            "principal_id":  self.principal_id,
            "agent_id":      self.agent_id,
            "action_kind":   self.action_kind,
            "action_target": self.action_target,
            "timestamp":     self.timestamp,
            "previous_hash": self.previous_hash,
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "receipt_id":    self.receipt_id,
            "kind":          self.kind,
            "kind_ext":      self.kind_ext,
            "principal": {
                "principal_id":   self.principal_id,
                "kind":           self.principal_kind,
                "agent_id":       self.agent_id,
                "session_id":     self.session_id,
                "agent_tier":     self.agent_tier,
                "capabilities":   self.capabilities,
            },
            "action": {
                "kind":    self.action_kind,
                "target":  self.action_target,
                "outcome": self.action_outcome,
                "params":  self.action_params,
            },
            "evidence_ids":          self.evidence_ids,
            "witness_attestations":  self.witness_attestations,
            "throne_evaluations":    self.throne_evaluations,
            "consensus_receipt":     self.consensus_receipt,
            "physical_attestation":  self.physical_attestation,
            "zangbeto_anchor":       self.zangbeto_anchor,
            "nostr_event_id":        self.nostr_event_id,
            "timestamp":             self.timestamp,
            "execution_id":          self.execution_id,
            "previous_hash":         self.previous_hash,
            "signature":             self.signature,
        }


# ── factory ───────────────────────────────────────────────────────────────────

def new_receipt(
    kind: str,
    action_kind: str,
    action_target: str,
    action_outcome: str,
    agent_id: str,
    *,
    principal_id: Optional[str] = None,
    principal_kind: str = "agent",
    session_id: Optional[str] = None,
    agent_tier: Optional[str] = None,
    capabilities: Optional[list] = None,
    action_params: Optional[dict] = None,
    evidence_ids: Optional[list] = None,
    previous_hash: Optional[str] = None,
    execution_id: Optional[str] = None,
    kind_ext: Optional[str] = None,
) -> ActionReceipt:
    """Create a new unsigned ActionReceipt.  Caller fills `signature` after."""
    return ActionReceipt(
        receipt_id    = str(uuid.uuid4()),
        kind          = kind,
        kind_ext      = kind_ext,
        principal_id  = principal_id or agent_id,
        principal_kind = principal_kind,
        agent_id      = agent_id,
        session_id    = session_id,
        agent_tier    = agent_tier,
        capabilities  = capabilities or [],
        action_kind   = action_kind,
        action_target = action_target,
        action_outcome = action_outcome,
        action_params = action_params or {},
        evidence_ids  = evidence_ids or [],
        witness_attestations = [],
        throne_evaluations   = [],
        consensus_receipt    = None,
        physical_attestation = None,
        zangbeto_anchor      = None,
        nostr_event_id       = None,
        timestamp     = int(time.time()),
        execution_id  = execution_id,
        previous_hash = previous_hash,
        signature     = "",
    )


# ── converters from existing formats ─────────────────────────────────────────

def from_runtime_receipt(native: dict, agent_id: str) -> ActionReceipt:
    """Wrap an Omo-Koda2 kernel receipt (receipts.py format)."""
    return new_receipt(
        kind          = "custom",
        kind_ext      = native.get("action", "kernel_action"),
        action_kind   = str(native.get("action", "unknown")),
        action_target = str(native.get("work_ref", "")),
        action_outcome = "success",
        agent_id      = agent_id,
        action_params = {"payload": native.get("payload"), "merkle_root": native.get("merkle_root")},
        previous_hash = native.get("previous_hash"),
        execution_id  = native.get("receipt_id"),
    )


def from_emission_receipt(emission: dict) -> ActionReceipt:
    """Wrap an OSOVM ASE emission receipt."""
    return new_receipt(
        kind          = "emission",
        action_kind   = "emit_ase",
        action_target = emission.get("pool", "unknown"),
        action_outcome = "success",
        agent_id      = "osovm",
        principal_kind = "contract",
        action_params = {
            "amount_mist":    emission.get("amount_mist"),
            "emission_number": emission.get("emission_number"),
            "distribution_reason": emission.get("distribution_reason"),
        },
        previous_hash = emission.get("previous_emission"),
        execution_id  = emission.get("receipt_id"),
    )


def from_twin_receipt(twin: dict, agent_id: str) -> ActionReceipt:
    """Wrap a twin-protocol receipt (kind 31020/31030)."""
    kind_int = twin.get("kind", 0)
    kind_str = "twin_capture" if kind_int == 31020 else "twin_scene"
    return new_receipt(
        kind          = kind_str,
        action_kind   = "capture" if kind_int == 31020 else "scene",
        action_target = str(twin.get("twin_id", "")),
        action_outcome = twin.get("outcome", "success"),
        agent_id      = agent_id,
        action_params = {
            "f1_score":    twin.get("f1_score"),
            "merkle_root": twin.get("merkle_root"),
        },
        execution_id  = twin.get("receipt_id"),
    )


def from_vcp_flight_receipt(flight: dict) -> ActionReceipt:
    """Wrap a VCP FlightReceipt (body_store.py format)."""
    return new_receipt(
        kind          = "vcp_session",
        action_kind   = "vcp_session",
        action_target = str(flight.get("body_id", "")),
        action_outcome = "success" if flight.get("mission_success") else "partial",
        agent_id      = str(flight.get("agent_id", "")),
        action_params = {
            "session_id":       flight.get("session_id"),
            "trajectory_hash":  flight.get("trajectory_hash"),
            "duration_ms":      flight.get("duration_ms"),
            "telemetry_count":  flight.get("telemetry_count"),
        },
        execution_id  = flight.get("receipt_id"),
    )


def from_ucx_compute_receipt(ucx: dict, agent_id: str) -> ActionReceipt:
    """Wrap a UCX ComputeReceipt (from the HTTP broker /receipt endpoint)."""
    return new_receipt(
        kind          = "compute",
        action_kind   = "submit_job",
        action_target = str(ucx.get("job_id", "")),
        action_outcome = "success",
        agent_id      = agent_id,
        action_params = {
            "provider_id":   ucx.get("provider_id"),
            "resources":     ucx.get("resources"),
            "billing":       ucx.get("billing"),
            "receipt_hash":  ucx.get("receipt_hash"),
        },
        execution_id  = ucx.get("job_id"),
    )


def from_trade_order(order: dict, agent_id: str, outcome: str = "pending") -> ActionReceipt:
    """Wrap a trading order as an ARP economic receipt (P0-7)."""
    return new_receipt(
        kind          = "economic",
        action_kind   = f"trade_{order.get('side', 'unknown').lower()}",
        action_target = str(order.get("symbol", "")),
        action_outcome = outcome,
        agent_id      = str(agent_id),
        action_params = {
            "order_id":    order.get("id"),
            "side":        order.get("side"),
            "quantity":    order.get("quantity"),
            "price":       order.get("price") or order.get("avg_fill_price"),
            "chain":       order.get("chain"),
            "order_type":  order.get("order_type"),
            "strategy_id": order.get("strategy_id"),
            "tx_hash":     order.get("tx_hash"),
        },
        execution_id  = str(order.get("id", "")),
    )
