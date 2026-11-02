"""RECEIPT_SCHEMA_V1 — machine-readable declaration of the Action Receipt
Protocol (ARP) envelope.

Why this file exists: the schema was previously implicit. `action_receipt.py`
documents "Action Receipt Protocol v1" and `canonical_hash()` mirrors
`arp-types/src/receipt.rs`, but nothing published a field list that a
consumer could check a receipt against. Zangbeto needs exactly that: not the
mood preimage, only *whether the field is present*. This module is that
declaration.

Versioning policy (backward compatible by construction):
  · v1 fields are append-only;
  · new fields MUST be optional (default None) and MUST NOT enter
    canonical_hash() — otherwise every historical receipt hash breaks;
  · a field may be declared `required_when` a runtime flag, which is how
    `mood_commitment` goes from "ignored" to "mandatory" without a v2.
"""
from __future__ import annotations

from typing import Iterable, Optional

# ── field declaration ─────────────────────────────────────────────────────────
# kind: "required" | "optional"     type: python-ish name for humans/validators

_FIELDS = {
    # identity chain
    "receipt_id":            ("required", "str",   "uuid4, unique per receipt"),
    "kind":                  ("required", "str",   "compute|vcp_session|emission|twin_capture|..."),
    "kind_ext":              ("optional", "str",   None),
    "principal_id":          ("required", "str",   "who acted"),
    "principal_kind":        ("required", "str",   "human|agent|daemon|contract|federated"),
    "agent_id":              ("required", "str",   "which agent acted"),
    "session_id":            ("optional", "str",   None),
    "agent_tier":            ("optional", "str",   None),
    "capabilities":          ("required", "list",  "capabilities exercised"),
    # action
    "action_kind":           ("required", "str",   "verb"),
    "action_target":         ("required", "str",   "entity acted upon"),
    "action_outcome":        ("required", "str",   "success|failure|partial|revoked"),
    "action_params":         ("required", "dict",  None),
    # evidence
    "evidence_ids":          ("required", "list",  None),
    "witness_attestations":  ("required", "list",  None),
    "throne_evaluations":    ("required", "list",  None),
    # settlement
    "consensus_receipt":     ("optional", "dict",  None),
    "physical_attestation":  ("optional", "dict",  None),
    "zangbeto_anchor":       ("optional", "str",   None),
    "nostr_event_id":        ("optional", "str",   None),
    # chain
    "timestamp":             ("required", "int",   "unix seconds"),
    "execution_id":          ("optional", "str",   None),
    "previous_hash":         ("optional", "str",   "chain link"),
    "signature":             ("required", "str",   "ed25519 hex, filled by signing layer"),

    # ── v1 additive: mood commitment ────────────────────────────────────────
    "mood_commitment": (
        "optional", "str",
        "sha256(pack('<dd', valence, arousal) + nonce).hex(); "
        "nonce ephemeral and NOT stored; null when the mood engine is inactive. "
        "Proves consistency after the fact; cannot reconstruct state from receipt alone.",
    ),
}

#: Fields that participate in canonical_hash() — v1 additions MUST NOT appear here.
HASHED_FIELDS = (
    "receipt_id", "kind", "principal_id", "agent_id",
    "action_kind", "action_target", "timestamp", "previous_hash",
)

#: Shape rule for the mood commitment value.
MOOD_COMMITMENT = {
    "field": "mood_commitment",
    "format": "hex64",
    "nullable": True,
    "required_when": "VANTAGE_MOOD_RECEIPTS=1",
    "length": 64,
    "alphabet": "0123456789abcdef",
    "preimage": "sha256(struct.pack('<dd', valence, arousal) + nonce[16])",
    "nonce_stored": False,
}

RECEIPT_SCHEMA_V1 = {
    "name": "ActionReceiptProtocol",
    "schema": "RECEIPT_SCHEMA_V1",
    "version": 1,
    "implementation": "backend/action_receipt.py",
    "mirrors": "arp-types/src/receipt.rs (ReceiptKind + hash())",
    "fields": {k: {"required": v[0] == "required", "type": v[1], "doc": v[2]}
               for k, v in _FIELDS.items()},
    "hashed_fields": list(HASHED_FIELDS),
    "additive": {"mood_commitment": MOOD_COMMITMENT},
}

# ── validation helpers (what Zangbeto actually needs) ─────────────────────────

# ARP v1 nests identity under "principal" and the verb under "action", so a
# flat name check against to_dict() output can never succeed. Map each schema
# field to where it actually serializes.
_SERIALIZED_PATHS = {
    "principal_id":   ("principal", "principal_id"),
    "principal_kind": ("principal", "kind"),
    "agent_id":       ("principal", "agent_id"),
    "session_id":     ("principal", "session_id"),
    "agent_tier":     ("principal", "agent_tier"),
    "capabilities":   ("principal", "capabilities"),
    "action_kind":    ("action", "kind"),
    "action_target":  ("action", "target"),
    "action_outcome": ("action", "outcome"),
    "action_params":  ("action", "params"),
}


def _present(receipt, field: str) -> bool:
    """Resolve a schema field on either an ActionReceipt object or its dict."""
    if not isinstance(receipt, dict):
        return getattr(receipt, field, None) is not None or hasattr(receipt, field)
    path = _SERIALIZED_PATHS.get(field, (field,))
    cur = receipt
    for seg in path:
        if not isinstance(cur, dict) or seg not in cur:
            return False
        cur = cur[seg]
    return True


def is_v1_compatible(receipt) -> bool:
    """True when every required v1 field is present. Additive optional fields
    are never a compatibility break, which is the whole point of the policy.
    Accepts an ActionReceipt or its to_dict() form."""
    return all(_present(receipt, name) for name, (kind, _t, _d) in _FIELDS.items()
               if kind == "required")


def has_mood_commitment(receipt) -> bool:
    if isinstance(receipt, dict):
        v = receipt.get("mood_commitment")
    else:
        v = getattr(receipt, "mood_commitment", None)
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def audit_mood_coverage(receipts: Iterable[dict],
                        require_mood: Optional[bool] = None) -> dict:
    """Coverage report for the audit pipeline.

    require_mood=True is the post-flag-flip mode: a receipt with a null
    commitment is a VIOLATION (mood should have been active). When False the
    field is informative only. Zangbeto never verifies the preimage — it
    cannot, the nonce isn't stored — it only checks presence/shape.
    """
    total = present = null = malformed = 0
    violations = []
    for r in receipts:
        total += 1
        raw = r.get("mood_commitment", "<absent>")
        if raw is None or raw == "<absent>":
            null += 1
            if require_mood:
                violations.append({"receipt_id": r.get("receipt_id"),
                                   "reason": "mood_commitment absent while mood engine active"})
        elif has_mood_commitment(r):
            present += 1
        else:
            malformed += 1
            violations.append({"receipt_id": r.get("receipt_id"),
                               "reason": f"malformed mood_commitment: {str(raw)[:24]}"})
    return {
        "schema": "RECEIPT_SCHEMA_V1",
        "total": total, "with_commitment": present, "null": null,
        "malformed": malformed,
        "coverage_pct": round(100.0 * present / total, 2) if total else 0.0,
        "require_mood": bool(require_mood),
        "violations": violations,
    }
