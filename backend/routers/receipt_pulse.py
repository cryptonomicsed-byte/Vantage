"""Latest receipt surface — what the organism last proved.

Reads the newest OKF receipt from /opt/ares/security_receipts and exposes only
what is safe for a UI: id, merkle root, chain position, when, how many findings,
and whether its Bitcoin timestamp is still pending.

Deliberately NOT returned: evidence_dir, target, raw paths, signature material.
The header is a public surface; the receipt file stays on the box.

State is derived from files, not from a database:
    <id>.json              the receipt
    <id>.merkle.txt.ots    OpenTimestamps proof exists -> "pending"
    <id>.time.json         a Bitcoin branch exists -> "attested" / "verified"
                           ("verified" only when the merkle root was matched
                            against a real block header and its PoW checked)
"""
import json
import sys
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, "/opt/ares")

router = APIRouter(prefix="/api/receipts", tags=["receipts"])

ROOT = Path("/opt/ares/security_receipts")
_CACHE = {"at": 0.0, "payload": None}
_TTL = 20


def _summarize(p: Path) -> dict:
    d = json.loads(p.read_text())
    diags = d.get("diagnostics") or []
    cvss = [x.get("cvss") for x in diags if isinstance(x.get("cvss"), (int, float))]
    ots_state, ots_block, ots_verified = "none", None, False
    extra = {}
    attest = Path(str(p)[:-5] + ".attest.json")
    side = Path(str(p)[:-5] + ".time.json")
    if attest.exists():
        # The signed attestation is the artifact: it binds this receipt's
        # merkle root to a block whose header and PoW were checked.
        try:
            ad = json.loads(attest.read_text())
            btc = ad.get("bitcoin") or {}
            ots_block = btc.get("height")
            ots_verified = bool(ad.get("checks")) and all(ad["checks"].values())
            ots_state = "verified" if ots_verified else "attested"
            extra["attestation_id"] = (ad.get("attestation_id") or "")[:16]
            extra["block_hash"] = (btc.get("block_hash") or "")[:16]
        except Exception:  # noqa: BLE001
            ots_state = "unreadable"
    elif side.exists():
        # Sidecar without an attestation: a height we parsed but never bound.
        try:
            sd = json.loads(side.read_text())
            btc = sd.get("bitcoin") or {}
            ots_block = btc.get("height")
            ots_state = "attested" if ots_block else "pending"
        except Exception:  # noqa: BLE001
            ots_state = "unreadable"
    elif list(p.parent.glob(p.stem + "*.ots")):
        ots_state = "pending"
    return {
        "receipt_id": d.get("receipt_id", "")[:12],
        "merkle_root": (d.get("merkle_root") or "")[:16],
        "chain_index": d.get("chain_index"),
        "created_at": d.get("created_at"),
        "repo": d.get("repo"),
        "findings": len(diags),
        "max_cvss": max(cvss) if cvss else None,
        "ots": ots_state,
        "ots_block": ots_block,
        "ots_verified": ots_verified,
        **extra,
        "prev_receipt": (d.get("prev_receipt") or None),
    }


@router.get("/latest")
def latest():
    import time
    if _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return _CACHE["payload"]
    files = [f for f in ROOT.glob("*/*.json")
             if not f.name.endswith((".time.json", ".attest.json"))]
    if not files:
        payload = {"ok": False, "error": "no receipts yet"}
    else:
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        try:
            payload = {"ok": True, "latest": _summarize(files[0]),
                       "total": len(files), "recent": [_summarize(f) for f in files[:5]]}
        except Exception as e:  # noqa: BLE001
            payload = {"ok": False, "error": str(e)[:160]}
    payload["at"] = int(time.time())
    _CACHE.update(at=time.time(), payload=payload)
    return payload
