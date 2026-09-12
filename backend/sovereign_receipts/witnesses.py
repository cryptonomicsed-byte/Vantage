"""Peer witness registry — trusted simulation witnesses for co-signing receipts.
Ported from sovereign-node witness_registry.rs."""

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class WitnessPeer:
    did: str
    public_key: str
    private_key: Optional[str] = None  # only set for locally-held witnesses


@dataclass
class WitnessAttestation:
    witness_id: str
    merkle_commitment: str
    timestamp: int
    signature: str


class WitnessRegistry:
    def __init__(self):
        self._peers: list[WitnessPeer] = []
        self._lock = Lock()

    def register(self, peer: WitnessPeer) -> None:
        with self._lock:
            if not any(p.did == peer.did for p in self._peers):
                self._peers.append(peer)

    def count(self) -> int:
        with self._lock:
            return len(self._peers)

    def all_peers(self) -> list[WitnessPeer]:
        with self._lock:
            return list(self._peers)

    def local_signers(self) -> list[WitnessPeer]:
        with self._lock:
            return [p for p in self._peers if p.private_key is not None]

    def get_witnesses_for_proof(self, min_count: int = 2) -> list[tuple[str, str]]:
        """Return (did, private_key) pairs for proof signing.
        Pads with ephemeral stubs if fewer than min_count local signers.
        """
        result = [(w.did, w.private_key) for w in self.local_signers()]

        needed = max(0, min_count - len(result))
        if needed > 0:
            import secrets
            for i in range(needed):
                stub_key = secrets.token_hex(32)
                result.append((f"did:witness:stub:{i+1:02d}", stub_key))

        return result
