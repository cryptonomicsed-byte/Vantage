"""Council of 12 store — seats, sectors, staggered quarterly rotation.
Ported from sovereign-node council_store.rs."""

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

COUNCIL_SEAT_COUNT = 12
SECTOR_COUNT = 24
TERM_DURATION_MS = 91 * 24 * 60 * 60 * 1000  # 91 days

SECTOR_DOMAINS = [
    "Simulation Infrastructure",    "Physical Reality Anchoring",
    "Digital Twin Provenance",       "VCP Device Governance",
    "Àṣẹ Monetary Policy",           "Emissions & Distribution",
    "Sovereign Identity",            "Privacy & Confidentiality",
    "Agent Lifecycle",               "Agent Ethics & Alignment",
    "Physical World Economy",        "Spatial Tile Registry",
    "DIP Protocol Standards",        "Node Federation",
    "Security & Zero Trust",         "Legal & Licensing",
    "Research & R&D Funding",        "Education & Onboarding",
    "Community & Governance Health", "Constitutional Amendments",
    "Emergency & Incident Response", "International Expansion",
    "Environmental Stewardship",     "Cultural & Heritage Preservation",
]


@dataclass
class CouncilSeat:
    seat_index: int
    councilor_did: str
    sectors: list[int]
    term_start_ms: int
    term_end_ms: int
    terms_served: int = 0
    is_first_steward: bool = False

    def rotation_cohort(self) -> int:
        return self.seat_index // 3


@dataclass
class Sector:
    sector_index: int
    domain: str
    council_seat: int
    pools: list = field(default_factory=list)


class CouncilStore:
    def __init__(self):
        self._seats: dict[int, CouncilSeat] = {}
        self._sectors: dict[int, Sector] = {}
        self._lock = Lock()
        self._initialized = False

    def initialize(self) -> None:
        """Initialize all 12 seats. Idempotent — no-op if already initialized."""
        with self._lock:
            if self._initialized:
                return
            now = int(time.time() * 1000)
            term_end = now + TERM_DURATION_MS

            for i in range(COUNCIL_SEAT_COUNT):
                sectors = [i * 2, i * 2 + 1]
                self._seats[i] = CouncilSeat(
                    seat_index=i,
                    councilor_did=f"did:genesis:seat:{i}",
                    sectors=sectors,
                    term_start_ms=now,
                    term_end_ms=term_end,
                    is_first_steward=(i == 0),
                )

            for idx in range(SECTOR_COUNT):
                domain = SECTOR_DOMAINS[idx] if idx < len(SECTOR_DOMAINS) else f"Sector {idx}"
                self._sectors[idx] = Sector(
                    sector_index=idx,
                    domain=domain,
                    council_seat=idx // 2,
                )

            self._initialized = True

    def seat(self, seat_index: int) -> Optional[CouncilSeat]:
        with self._lock:
            return self._seats.get(seat_index)

    def all_seats(self) -> list[CouncilSeat]:
        with self._lock:
            return sorted(self._seats.values(), key=lambda s: s.seat_index)

    def all_sectors(self) -> list[Sector]:
        with self._lock:
            return sorted(self._sectors.values(), key=lambda s: s.sector_index)

    def rotate(self, seat_index: int, new_councilor_did: str) -> CouncilSeat:
        with self._lock:
            seat = self._seats.get(seat_index)
            if seat is None:
                raise ValueError(f"seat {seat_index} not found")
            now = int(time.time() * 1000)
            if seat.term_end_ms > now:
                raise ValueError(
                    f"seat {seat_index} term has not ended yet "
                    f"(ends at {seat.term_end_ms}ms, now {now}ms)"
                )
            seat.terms_served += 1
            seat.councilor_did = new_councilor_did
            seat.term_start_ms = now
            seat.term_end_ms = now + TERM_DURATION_MS
            if seat_index == 0 and seat.terms_served >= 3:
                seat.is_first_steward = True
            return seat

    def force_rotate(self, seat_index: int, new_councilor_did: str) -> CouncilSeat:
        with self._lock:
            seat = self._seats.get(seat_index)
            if seat is None:
                raise ValueError(f"seat {seat_index} not found")
            now = int(time.time() * 1000)
            seat.terms_served += 1
            seat.councilor_did = new_councilor_did
            seat.term_start_ms = now
            seat.term_end_ms = now + TERM_DURATION_MS
            return seat

    def rotation_eligible(self) -> list[CouncilSeat]:
        now = int(time.time() * 1000)
        with self._lock:
            return [s for s in self._seats.values() if s.term_end_ms <= now]
