"""Council of 12 — canonical implementation lives in routers/governance.py.

This module re-exports the shared constants and the in-memory CouncilSeat /
CouncilStore types (ported from sovereign-node council_store.rs) that are used
by tests and internal tooling.  For all HTTP-facing Council of 12 logic — seat
nomination, rotation, proposal voting, Bínò veto — use the DB-backed router in
``routers/governance.py``.

Import from here only when you need the lightweight in-memory representation
(e.g. simulation / unit-test scaffolding).  Production reads/writes go through
``routers.governance``.
"""

# ── Re-export canonical constants from the authoritative source ───────────────
# These must stay in sync with routers/governance.py.
# If you change them there, change them here too (and vice versa).

COUNCIL_SEAT_COUNT = 12   # matches routers/governance.COUNCIL_SIZE
SECTOR_COUNT = 24          # matches routers/governance.TOTAL_SECTORS
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

# ── In-memory types (Rust port — for simulation / unit tests only) ────────────
# For production council state, query routers.governance (DB-backed, async).

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


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
    """Lightweight in-memory council store used for simulation / testing.

    Production code MUST use ``routers.governance`` (DB-backed, async).
    This class exists for unit-test scaffolding and sovereign-node simulations
    that do not have a live database.
    """

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
        """Rotate a seat. NOTE: for production use routers.governance.rotate_council."""
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
