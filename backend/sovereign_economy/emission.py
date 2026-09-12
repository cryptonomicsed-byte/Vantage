"""Emission receipt chain. Every Àṣẹ distribution produces an EmissionReceipt.
Receipts form a per-pool hash chain. Ported from emission_receipt_store.rs."""

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional


class DistributionPool(str, Enum):
    Simulation  = "Simulation"
    Research    = "Research"
    Governance  = "Governance"
    Reserve     = "Reserve"
    Grants      = "Grants"
    Ubi         = "Ubi"
    LotteryBurn = "LotteryBurn"
    Sabbath     = "Sabbath"


@dataclass
class EmissionReceipt:
    receipt_id: str
    emission_number: int
    timestamp_sec: int
    pool: DistributionPool
    amount_mist: int
    candidate_set_hash: str
    scoring_method: str
    distribution_reason: str
    selected_recipient: Optional[str] = None
    proof_id: Optional[str] = None
    previous_emission: Optional[str] = None
    osovm_signature: str = "stub:unsigned"


def _compute_receipt_id(emission_number: int, pool: DistributionPool, timestamp_sec: int) -> str:
    h = hashlib.sha256()
    h.update(emission_number.to_bytes(8, "little"))
    h.update(pool.value.encode())
    h.update(timestamp_sec.to_bytes(8, "little"))
    return f"emit:{h.hexdigest()}"


class EmissionReceiptStore:
    def __init__(self):
        self._receipts: list[EmissionReceipt] = []
        self._by_id: dict[str, int] = {}
        self._chain_head: dict[str, str] = {}
        self._emission_number: int = 0
        self._lock = Lock()

    def record(
        self,
        pool: DistributionPool,
        amount_mist: int,
        candidate_set_hash: str,
        scoring_method: str,
        distribution_reason: str,
        selected_recipient: Optional[str] = None,
        proof_id: Optional[str] = None,
    ) -> EmissionReceipt:
        with self._lock:
            self._emission_number += 1
            now = int(time.time())
            receipt_id = _compute_receipt_id(self._emission_number, pool, now)
            previous = self._chain_head.get(pool.value)

            receipt = EmissionReceipt(
                receipt_id=receipt_id,
                emission_number=self._emission_number,
                timestamp_sec=now,
                pool=pool,
                amount_mist=amount_mist,
                candidate_set_hash=candidate_set_hash,
                scoring_method=scoring_method,
                distribution_reason=distribution_reason,
                selected_recipient=selected_recipient,
                proof_id=proof_id,
                previous_emission=previous,
            )
            self._chain_head[pool.value] = receipt_id
            idx = len(self._receipts)
            self._by_id[receipt_id] = idx
            self._receipts.append(receipt)
            return receipt

    def get(self, receipt_id: str) -> Optional[EmissionReceipt]:
        with self._lock:
            idx = self._by_id.get(receipt_id)
            return self._receipts[idx] if idx is not None else None

    def by_pool(self, pool: DistributionPool) -> list[EmissionReceipt]:
        with self._lock:
            return [r for r in self._receipts if r.pool == pool]

    def all(self) -> list[EmissionReceipt]:
        with self._lock:
            return list(self._receipts)

    def emission_number(self) -> int:
        with self._lock:
            return self._emission_number

    def chain_head(self, pool: DistributionPool) -> Optional[str]:
        with self._lock:
            return self._chain_head.get(pool.value)
