"""Vantage → Proof: attested execution becomes reputation.

Proof (``/opt/ares/Proof/relay/proof_relay.py``) is a Nostr relay library for
attested work: it accepts a kind-30009 event, writes an ``attestations`` row,
and folds a quality score into a per-pubkey ``reputations`` tier. It shipped with
no caller in this stack — every confirmed order here was already a verifiable
unit of work, and nothing turned those into reputation.

What is attested, and what "quality" means here:

  quality 1.0  order confirmed with a real on-chain transaction hash
  quality 0.5  order confirmed as a *paper* fill (tx_hash starts ``paper:``)
  quality 0.0  never attested — failed/cancelled orders are not work

The P&L is deliberately not the score. Reputation is for *verifiable work*, and
a profitable paper fill is still a simulation. The economic weight stays inside
Proof's own reward map (``work_type``), which this bridge does not modify —
it only supplies ``workflow_complete`` with a quality multiplier.

Idempotent by construction: the event id is a hash of ``vantage:order:<id>``, so
re-running the attestation cannot double-count an order.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

PROOF_REPO = os.environ.get("PROOF_REPO", "/opt/ares/Proof")
PROOF_RELAY_DIR = os.path.join(PROOF_REPO, "relay")
PROOF_DB = os.environ.get("PROOF_DB", os.path.join(PROOF_RELAY_DIR, "proof_relay.db"))
COMMUNITY = "vantage"


def _relay_cls():
    """Import ProofRelay from the Proof checkout without installing it."""
    if PROOF_RELAY_DIR not in sys.path:
        sys.path.insert(0, PROOF_RELAY_DIR)
    from proof_relay import ProofRelay  # type: ignore

    return ProofRelay


def _agent_pubkey(agent_id: Any) -> str:
    """Stable 64-hex identity for a Vantage agent.

    Proof keys reputation by Nostr pubkey. Vantage agents have no signing key of
    their own here, so the identity is the hash of the agent id — stable across
    runs, distinct per agent, and explicitly *not* a claim of key ownership.
    """
    return hashlib.sha256(f"vantage-agent:{agent_id}".encode()).hexdigest()


def build_attestation(order: Dict[str, Any]) -> Dict[str, Any]:
    """Build the kind-30009 event for one confirmed order."""
    oid = order["id"]
    tx = (order.get("tx_hash") or "").strip()
    is_paper = tx.startswith("paper:") or not tx
    quality = 0.5 if is_paper else 1.0

    ref = f"vantage:order:{oid}"
    event_id = hashlib.sha256(ref.encode()).hexdigest()
    created_at = int(time.time())

    content = {
        "work_type": "workflow_complete",
        "quality_score": quality,
        "work_url": f"/api/trading/orders/{oid}",
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "chain": order.get("chain"),
        "quantity": order.get("filled_quantity") or order.get("quantity"),
        "fill_price": order.get("avg_fill_price") or order.get("price"),
        "tx_hash": tx,
        "paper": is_paper,
        "receipt_ref": ref,
    }
    tags = [
        ["community", COMMUNITY],
        ["channel", "trading"],
        ["work_repo", "cryptonomicsed-byte/Vantage"],
        ["work_type", "workflow_complete"],
        ["t", "vantage-order"],
        ["t", "paper" if is_paper else "onchain"],
    ]
    return {
        "id": event_id,
        "pubkey": _agent_pubkey(order.get("agent_id")),
        "created_at": created_at,
        "kind": 30009,
        "tags": tags,
        "content": json.dumps(content),
        # Self-attested: the relay records attester_pubkey == pubkey. This is a
        # claim by the executing agent about its own work, verified only by the
        # tx hash in the payload. Third-party attestation is a separate design.
        "sig": "",
    }


def attest_orders(orders: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, Any]:
    """Store attestations for the given orders. Returns a per-order report."""
    if dry_run:
        return {"dry_run": True, "would_attest": len(orders),
                "events": [build_attestation(o)["id"] for o in orders]}

    ProofRelay = _relay_cls()
    relay = ProofRelay(db_path=PROOF_DB)
    attested, skipped, failed = [], [], []
    try:
        seen = {r[0] for r in relay.conn.execute("SELECT id FROM nostr_events").fetchall()}
        for o in orders:
            evt = build_attestation(o)
            if evt["id"] in seen:
                skipped.append(o["id"])
                continue
            ok = relay.store_event(evt)
            (attested if ok else failed).append(o["id"])
    finally:
        relay.close()

    return {"attested": len(attested), "skipped": len(skipped), "failed": len(failed),
            "order_ids": {"attested": attested, "skipped": skipped, "failed": failed},
            "proof_db": PROOF_DB}


def leaderboard(limit: int = 20) -> Dict[str, Any]:
    """Read reputation back out of Proof (read-only, opens the same DB)."""
    if not os.path.exists(PROOF_DB):
        return {"available": False, "reason": "no proof_relay.db yet — attest something first"}
    ProofRelay = _relay_cls()
    relay = ProofRelay(db_path=PROOF_DB)
    try:
        rows = relay.get_leaderboard(limit)
        # The leaderboard hides tier='unverified' by design, so an empty board
        # next to real attestations is normal, not a broken wire. Report the
        # underlying counts so those two cases are distinguishable.
        totals = {r[0]: r[1] for r in relay.conn.execute(
            "SELECT tier, COUNT(*) FROM reputations GROUP BY tier").fetchall()}
        attestations = relay.conn.execute(
            "SELECT COUNT(*) FROM attestations").fetchone()[0]
        return {"available": True, "leaderboard": rows, "proof_db": PROOF_DB,
                "reputations_by_tier": totals, "attestations": attestations,
                "note": "leaderboard lists only verified+ tiers; unverified agents "
                        "still have a score readable by pubkey",
                "token": relay.get_token_stats()}
    finally:
        relay.close()


def reputation_for(pubkey: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(PROOF_DB):
        return None
    ProofRelay = _relay_cls()
    relay = ProofRelay(db_path=PROOF_DB)
    try:
        return relay.get_reputation(pubkey)
    finally:
        relay.close()
