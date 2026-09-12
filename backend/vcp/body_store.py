"""In-memory store for active BodySessions and completed FlightReceipts.

Ported from sovereign-stack/sovereign-node/src/body_store.rs (P2 migration).
Application logic belongs in Vantage; sovereign-node is a thin protocol daemon.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BodySession:
    session_id: str
    agent_id: str
    body_id: str
    mode: str = "human_supervised"
    capabilities: list = field(default_factory=list)
    sim_proof_id: Optional[str] = None
    agent_tier: Optional[str] = None
    created_at: Optional[int] = None


@dataclass
class FlightReceipt:
    receipt_id: str
    session_id: str
    agent_id: str
    body_id: str
    trajectory_hash: str
    telemetry_count: int = 0
    duration_ms: int = 0
    max_altitude_m: float = 0.0
    battery_consumed_pct: float = 0.0
    mission_success: bool = False
    witness_ids: list = field(default_factory=list)
    sim_proof_id: Optional[str] = None
    timestamp: int = 0
    signature: str = ""


class BodyStore:
    """Thread-safe in-memory store for BodySession and FlightReceipt objects."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._receipts: list[dict] = []

    def insert_session(self, session_dict: dict) -> None:
        """Insert or replace a session by its session_id."""
        session_id = session_dict["session_id"]
        with self._lock:
            self._sessions[session_id] = session_dict

    def get_session(self, session_id: str) -> Optional[dict]:
        """Return a copy of the session dict, or None if not found."""
        with self._lock:
            entry = self._sessions.get(session_id)
            return dict(entry) if entry is not None else None

    def all_sessions(self) -> list:
        """Return a snapshot of all sessions."""
        with self._lock:
            return [dict(s) for s in self._sessions.values()]

    def add_receipt(self, receipt_dict: dict) -> None:
        """Append a flight receipt."""
        with self._lock:
            self._receipts.append(receipt_dict)

    def receipts_for_body(self, body_id: str) -> list:
        """Return all receipts whose body_id matches."""
        with self._lock:
            return [dict(r) for r in self._receipts if r.get("body_id") == body_id]

    def all_receipts(self) -> list:
        """Return a snapshot of all receipts."""
        with self._lock:
            return [dict(r) for r in self._receipts]


# Module-level singleton
_store = BodyStore()


def get_store() -> BodyStore:
    """Return the module-level BodyStore singleton."""
    return _store
