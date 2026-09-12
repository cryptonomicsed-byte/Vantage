"""Rolling Merkle tree over completed receipts.
Leaves sorted lexicographically for determinism.
Ported from sovereign-node receipt_merkle.rs."""

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import ReceiptRecord


@dataclass
class ReceiptMerkleRoot:
    root: str
    count: int
    computed_at: int
    algo: str = "sha256-binary-merkle"


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _combine(leaves: list[bytes]) -> bytes:
    if len(leaves) == 1:
        return leaves[0]
    mid = _next_power_of_two(len(leaves)) // 2
    left = _combine(leaves[:min(mid, len(leaves))])
    right = _combine(leaves[mid:]) if mid < len(leaves) else left
    return _sha256(left + right)


def _next_power_of_two(n: int) -> int:
    if n == 0:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def build_receipt_tree(records: list) -> ReceiptMerkleRoot:
    ids = sorted(r.receipt_id for r in records)
    if not ids:
        root_bytes = _sha256(b"empty")
    else:
        leaves = [_sha256(id_.encode()) for id_ in ids]
        root_bytes = _combine(leaves)
    return ReceiptMerkleRoot(
        root=f"sha256:{root_bytes.hex()}",
        count=len(records),
        computed_at=int(time.time() * 1000),
    )


def verify_receipt_in_root(receipt_id: str, all_records: list, expected_root: str) -> bool:
    current = build_receipt_tree(all_records)
    if current.root != expected_root:
        return False
    return any(r.receipt_id == receipt_id for r in all_records)
