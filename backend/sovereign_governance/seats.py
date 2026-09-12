"""Sovereign Seat store — 1440 permanent governance/economic offices.
1440 seats = minutes/day = emission ticks/day (intentional resonance).
Ported from sovereign-node sovereign_seat_store.rs."""

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

SOVEREIGN_SEAT_COUNT = 1440


@dataclass
class SovereignWallet:
    seat_index: int
    current_steward: str
    steward_since_ms: int
    revoked: bool = False
    revocation_reason: Optional[str] = None
    council_queue_position: Optional[int] = None
    active_council_seat: Optional[int] = None
    reached_first_steward: bool = False
    inheritance_chain: list = field(default_factory=list)


class SovereignSeatStore:
    def __init__(self):
        self._seats: dict[int, SovereignWallet] = {}
        self._lock = Lock()

    def claim(self, seat_index: int, steward_did: str) -> SovereignWallet:
        if seat_index >= SOVEREIGN_SEAT_COUNT:
            raise ValueError(f"seat_index {seat_index} out of range (max {SOVEREIGN_SEAT_COUNT-1})")
        with self._lock:
            existing = self._seats.get(seat_index)
            if existing and not existing.revoked:
                raise ValueError(f"seat {seat_index} already claimed by {existing.current_steward}")
            now = int(time.time() * 1000)
            wallet = SovereignWallet(
                seat_index=seat_index,
                current_steward=steward_did,
                steward_since_ms=now,
                inheritance_chain=[(steward_did, now, 0)],
            )
            self._seats[seat_index] = wallet
            return wallet

    def revoke(self, seat_index: int, reason: str) -> SovereignWallet:
        with self._lock:
            seat = self._seats.get(seat_index)
            if seat is None:
                raise ValueError(f"seat {seat_index} not claimed")
            if seat.revoked:
                raise ValueError(f"seat {seat_index} already revoked")
            now = int(time.time() * 1000)
            if seat.inheritance_chain:
                entry = list(seat.inheritance_chain[-1])
                entry[2] = now
                seat.inheritance_chain[-1] = tuple(entry)
            seat.revoked = True
            seat.revocation_reason = reason
            return seat

    def enqueue_for_council(self, seat_index: int, position: int) -> SovereignWallet:
        with self._lock:
            seat = self._seats.get(seat_index)
            if seat is None:
                raise ValueError(f"seat {seat_index} not claimed")
            if seat.revoked:
                raise ValueError(f"seat {seat_index} is revoked — cannot queue")
            seat.council_queue_position = position
            return seat

    def get(self, seat_index: int) -> Optional[SovereignWallet]:
        with self._lock:
            return self._seats.get(seat_index)

    def all_claimed(self) -> list[SovereignWallet]:
        with self._lock:
            return sorted(self._seats.values(), key=lambda s: s.seat_index)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._seats.values() if not s.revoked)

    def vacant_count(self) -> int:
        with self._lock:
            return SOVEREIGN_SEAT_COUNT - len(self._seats)
