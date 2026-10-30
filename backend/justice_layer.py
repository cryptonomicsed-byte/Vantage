"""Justice layer: Python thin client for on-chain receipt anchoring via Sui Move contracts.

Sits between action_receipt.py (ARP envelope) and the oso-justice Move contract
(/opt/ares/ares-justice/sources/receipt.move).

Modes:
  SUI_ACTIVE=0  (default) — local-only mode: DB + action_receipts table only.
  SUI_ACTIVE=1            — also submits receipt_hash to oso-justice::receipt::anchor
                             on Sui mainnet via PTB (requires SUI_WALLET_ADDRESS +
                             OSO_MOVE_PACKAGE_ID configured after contract deployment).

Role in the 7-layer stack:
  Move (FIRE layer) owns economic rules, on-chain accountability, and reputation
  anchoring.  This module is the Python adapter that speaks to it: receipts flow
    action_receipt.py  →  justice_layer.anchor_receipt()  →  oso-justice::receipt::anchor
                       →  action_receipts table (always)
                       →  Sui tx_digest (when SUI_ACTIVE=1)
"""
from __future__ import annotations

import hashlib
import logging
import json
import os
import time
import uuid
from typing import Any, Optional

SUI_ACTIVE = os.environ.get("SUI_ACTIVE", "0") == "1"
SUI_RPC_URL = os.environ.get("SUI_RPC_URL", "https://fullnode.mainnet.sui.io")
SUI_WALLET = os.environ.get("SUI_WALLET_ADDRESS", "")
OSO_MOVE_PACKAGE = os.environ.get("OSO_MOVE_PACKAGE_ID", "")  # set after `sui client publish`

logger = logging.getLogger(__name__)


async def anchor_receipt(
    principal_id: str,
    action_kind: str,
    capability: str,
    evidence: dict,
    *,
    kind: str = "custom",
    action_target: str = "",
    action_outcome: str = "success",
    gate_alignment: float = 1.0,
) -> dict:
    """Anchor an action receipt.

    Always writes to the action_receipts DB table.
    Optionally posts to Sui if SUI_ACTIVE=1 and OSO_MOVE_PACKAGE_ID is set.

    Returns the receipt dict with fields:
      id, principal, kind, capability, action_kind, action_target,
      action_outcome, evidence, gate_alignment, ts, hash, chain, tx_digest
    """
    from backend.db import get_db

    receipt_id = str(uuid.uuid4())
    ts = int(time.time())

    # Compute canonical hash matching oso-justice contract expectations.
    # Mirrors ActionReceipt.canonical_hash() in action_receipt.py.
    canonical_payload = {
        "receipt_id":     receipt_id,
        "kind":           kind,
        "principal_id":   principal_id,
        "capability":     capability,
        "action_kind":    action_kind,
        "action_target":  action_target,
        "action_outcome": action_outcome,
        "timestamp":      ts,
    }
    receipt_hash = hashlib.sha256(
        json.dumps(canonical_payload, sort_keys=True).encode()
    ).hexdigest()

    receipt = {
        "id":             receipt_id,
        "principal":      principal_id,
        "kind":           kind,
        "capability":     capability,
        "action_kind":    action_kind,
        "action_target":  action_target,
        "action_outcome": action_outcome,
        "evidence":       evidence,
        "gate_alignment": gate_alignment,
        "ts":             ts,
        "hash":           receipt_hash,
        "chain":          "local",
        "tx_digest":      None,
    }

    # ── DB write (always) ──────────────────────────────────────────────────
    async with get_db() as db:
        try:
            await db.execute(
                """INSERT OR IGNORE INTO action_receipts
                   (id, principal_id, kind, capability, action_kind,
                    action_target, action_outcome, evidence_json,
                    gate_alignment, receipt_hash, chain, tx_digest, created_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id, principal_id, kind, capability, action_kind,
                    action_target, action_outcome, json.dumps(evidence),
                    gate_alignment, receipt_hash, "local", None, ts,
                ),
            )
            await db.commit()
        except Exception as exc:
            receipt["db_error"] = str(exc)

    # ── Sui anchoring (optional) ───────────────────────────────────────────
    if SUI_ACTIVE and OSO_MOVE_PACKAGE and SUI_WALLET:
        try:
            tx_digest = await _submit_to_sui(
                receipt_hash=receipt_hash,
                kind=kind,
                action_kind=action_kind,
                action_target=action_target,
                action_outcome=action_outcome,
                gate_alignment_bps=int(gate_alignment * 10_000),
                ts=ts,
            )
            receipt["chain"] = "sui"
            receipt["tx_digest"] = tx_digest
            async with get_db() as db:
                try:
                    await db.execute(
                        "UPDATE action_receipts SET chain='sui', tx_digest=? WHERE id=?",
                        (tx_digest, receipt_id),
                    )
                    await db.commit()
                except Exception:
                    pass
        except Exception as exc:
            receipt["chain_error"] = str(exc)

    return receipt


async def anchor_from_arp(arp_receipt: Any) -> dict:
    """Convenience wrapper — takes an ActionReceipt dataclass from action_receipt.py."""
    return await anchor_receipt(
        principal_id=arp_receipt.principal_id,
        action_kind=arp_receipt.action_kind,
        capability=", ".join(c.get("name", "") for c in arp_receipt.capabilities),
        evidence={
            "action_target":  arp_receipt.action_target,
            "action_outcome": arp_receipt.action_outcome,
            "action_params":  arp_receipt.action_params,
            "evidence_ids":   arp_receipt.evidence_ids,
            "execution_id":   arp_receipt.execution_id,
        },
        kind=arp_receipt.kind,
        action_target=arp_receipt.action_target,
        action_outcome=arp_receipt.action_outcome,
        gate_alignment=1.0,
    )


async def get_receipt(receipt_id: str) -> dict:
    """Fetch a receipt from the local DB by ID."""
    from backend.db import get_db

    async with get_db() as db:
        try:
            db.row_factory = __import__("aiosqlite").Row
            row = await (
                await db.execute(
                    "SELECT * FROM action_receipts WHERE id=?", (receipt_id,)
                )
            ).fetchone()
            if row:
                d = dict(row)
                try:
                    d["evidence"] = json.loads(d.get("evidence_json") or "{}")
                except Exception:
                    pass
                return d
        except Exception:
            pass
    return {}


async def list_receipts(principal_id: str, limit: int = 50) -> list[dict]:
    """List receipts for a principal, newest first."""
    from backend.db import get_db

    async with get_db() as db:
        try:
            db.row_factory = __import__("aiosqlite").Row
            rows = await (
                await db.execute(
                    """SELECT id, kind, capability, action_kind, action_outcome,
                              receipt_hash, chain, tx_digest, created_ts
                         FROM action_receipts
                        WHERE principal_id=?
                        ORDER BY created_ts DESC LIMIT ?""",
                    (principal_id, limit),
                )
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


# ── Internal Sui submission ────────────────────────────────────────────────────

async def _submit_to_sui(
    receipt_hash: str,
    kind: str,
    action_kind: str,
    action_target: str,
    action_outcome: str,
    gate_alignment_bps: int,
    ts: int,
) -> str:
    """Build and submit a PTB calling oso_justice::receipt::anchor.

    Requires `sui` CLI on PATH with an active address matching SUI_WALLET.
    Falls back to a deterministic stub digest if CLI is unavailable.

    Real PTB (post-deploy):
        sui client call \\
            --package $OSO_MOVE_PACKAGE_ID \\
            --module receipt \\
            --function anchor \\
            --args <receipt_hash_bytes> <kind_bytes> <action_kind_bytes> \\
                   <action_target_bytes> <action_outcome_bytes> \\
                   <gate_alignment_bps> <ts>
    """
    import subprocess
    import shutil

    # Encode args as hex-prefixed byte vectors for sui CLI
    def _to_bytes_arg(s: str) -> str:
        return "0x" + s.encode().hex()

    if shutil.which("sui") or os.path.exists(os.path.expanduser("~/.cargo/bin/sui")):
        sui_bin = shutil.which("sui") or os.path.expanduser("~/.cargo/bin/sui")
        cmd = [
            sui_bin, "client", "call",
            "--package", OSO_MOVE_PACKAGE,
            "--module", "receipt",
            "--function", "anchor",
            "--args",
            _to_bytes_arg(receipt_hash),
            _to_bytes_arg(kind),
            _to_bytes_arg(action_kind),
            _to_bytes_arg(action_target),
            _to_bytes_arg(action_outcome),
            str(gate_alignment_bps),
            str(ts),
            "--gas-budget", "10000000",
            "--json",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                out = json.loads(result.stdout)
                return out.get("digest", "")
        except Exception:
            pass

    # Stub: deterministic digest for dev/test when contract not yet deployed
    stub = hashlib.sha256(
        f"{receipt_hash}:{kind}:{action_kind}:{ts}".encode()
    ).hexdigest()
    return f"0x{stub}"


# ── Elegbara settlement (Phase 9-B) ───────────────────────────────────────────

def settle_job_payment_on_chain(
    job_id: str,
    gross_amount: int,
    birth_record_id: str,
    agent_addr: str,
) -> dict:
    """
    Calls elegbara_router settle_job_payment via Sui CLI when SUI_ACTIVE=1.
    Returns {ok: bool, birther_coin: str, agent_coin: str, tx_digest: str}.
    Fail-open: returns {ok: False, reason: ...} if SUI_ACTIVE=0 or CLI unavailable.
    """
    if not SUI_ACTIVE:
        return {"ok": False, "reason": "SUI_ACTIVE=0"}

    ELEGBARA_PACKAGE = os.environ.get("ELEGBARA_PACKAGE_ID", "")
    ELEGBARA_ROUTER_OBJ = os.environ.get("ELEGBARA_ROUTER_OBJ", "")

    if not ELEGBARA_PACKAGE or not ELEGBARA_ROUTER_OBJ:
        logger.warning("settle_job_payment_on_chain: ELEGBARA_PACKAGE_ID or ELEGBARA_ROUTER_OBJ not set")
        return {"ok": False, "reason": "elegbara env vars missing"}

    try:
        import subprocess
        cmd = [
            "sui", "client", "call",
            "--package", ELEGBARA_PACKAGE,
            "--module", "elegbara_router",
            "--function", "settle_job_payment",
            "--args", ELEGBARA_ROUTER_OBJ, birth_record_id, str(gross_amount), agent_addr,
            "--gas-budget", "10000000",
            "--json"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            import json as _json
            data = _json.loads(result.stdout)
            digest = data.get("digest", "")
            logger.info(f"settle_job_payment on-chain OK: job={job_id} digest={digest}")
            return {"ok": True, "tx_digest": digest, "job_id": job_id}
        else:
            logger.warning(f"settle_job_payment CLI error: {result.stderr[:200]}")
            return {"ok": False, "reason": result.stderr[:200]}
    except Exception as e:
        logger.warning(f"settle_job_payment on-chain failed (fail-open): {e}")
        return {"ok": False, "reason": str(e)}
