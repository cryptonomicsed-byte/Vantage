"""Per-DID micro-Àṣẹ balance ledger. Ported from sovereign-node wallet_store.rs."""

import time
from threading import Lock
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WalletEntry:
    did: str
    balance_micro_ase: int = 0
    total_credited: int = 0
    total_debited: int = 0
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))


class WalletStore:
    def __init__(self):
        self._wallets: dict[str, WalletEntry] = {}
        self._lock = Lock()

    def _get_or_create(self, did: str) -> WalletEntry:
        if did not in self._wallets:
            self._wallets[did] = WalletEntry(did=did)
        return self._wallets[did]

    def credit(self, did: str, amount: int) -> int:
        """Credit amount µÀṣẹ to did. Returns new balance."""
        with self._lock:
            entry = self._get_or_create(did)
            entry.balance_micro_ase += amount
            entry.total_credited += amount
            entry.updated_at = int(time.time() * 1000)
            return entry.balance_micro_ase

    def debit(self, did: str, amount: int) -> int:
        """Debit amount µÀṣẹ from did. Raises ValueError if insufficient."""
        with self._lock:
            entry = self._get_or_create(did)
            if entry.balance_micro_ase < amount:
                raise ValueError(
                    f"insufficient balance: have {entry.balance_micro_ase} µÀṣẹ, need {amount}"
                )
            entry.balance_micro_ase -= amount
            entry.total_debited += amount
            entry.updated_at = int(time.time() * 1000)
            return entry.balance_micro_ase

    def get(self, did: str) -> Optional[WalletEntry]:
        with self._lock:
            return self._wallets.get(did)

    def all(self) -> list[WalletEntry]:
        with self._lock:
            return sorted(
                self._wallets.values(),
                key=lambda w: w.balance_micro_ase,
                reverse=True,
            )

    def total_supply(self) -> int:
        with self._lock:
            return sum(w.balance_micro_ase for w in self._wallets.values())

    def balance(self, did: str) -> int:
        with self._lock:
            entry = self._wallets.get(did)
            return entry.balance_micro_ase if entry else 0
