"""Inbound telemetry buffer for active body sessions.

Ported from sovereign-stack/sovereign-node/src/telemetry_store.rs (P2 migration).
StampFly (or any VCP body) streams telemetry frames; this store buffers them
per session and can close a session into a receipt with a trajectory hash.
"""

from __future__ import annotations

import hashlib
import struct
import threading
import time
import uuid
from typing import Optional


class TelemetryStore:
    """Thread-safe buffer for per-session telemetry frames."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, list[dict]] = {}

    def push(self, session_id: str, frame_dict: dict) -> None:
        """Append a telemetry frame to the named session buffer."""
        with self._lock:
            self._sessions.setdefault(session_id, []).append(frame_dict)

    def frames(self, session_id: str) -> list:
        """Return all buffered frames for a session (copy)."""
        with self._lock:
            return list(self._sessions.get(session_id, []))

    def close_session(
        self,
        session_id: str,
        agent_id: str,
        body_id: str,
        sim_proof_id: Optional[str],
        witness_ids: list,
        mission_success: bool,
    ) -> dict:
        """Close the session: compute trajectory hash and produce a receipt dict.

        Frames are removed from the buffer.  Returns the receipt dict.
        """
        with self._lock:
            frames = self._sessions.pop(session_id, [])

        trajectory_hash = _hash_trajectory(frames)
        duration_ms = _trajectory_duration_ms(frames)

        max_altitude = max((f.get("altitude_m", 0.0) for f in frames), default=0.0)
        battery_start = frames[0].get("battery_pct", 100.0) if frames else 100.0
        battery_end = frames[-1].get("battery_pct", battery_start) if frames else battery_start
        battery_consumed = max(battery_start - battery_end, 0.0)

        now_ms = int(time.time() * 1000)

        return {
            "receipt_id": f"rcpt:flight:{uuid.uuid4()}",
            "session_id": session_id,
            "agent_id": agent_id,
            "body_id": body_id,
            "sim_proof_id": sim_proof_id,
            "trajectory_hash": trajectory_hash,
            "telemetry_count": len(frames),
            "duration_ms": duration_ms,
            "max_altitude_m": max_altitude,
            "battery_consumed_pct": battery_consumed,
            "mission_success": mission_success,
            "witness_ids": list(witness_ids),
            "timestamp": now_ms,
            "signature": "",
        }


def _hash_trajectory(frames: list) -> str:
    """SHA-256 over the positional/temporal fields of each frame (deterministic)."""
    hasher = hashlib.sha256()
    for f in frames:
        ts = f.get("timestamp_ms", 0)
        hasher.update(struct.pack("<Q", int(ts)))
        for v in f.get("position", [0.0, 0.0, 0.0]):
            hasher.update(struct.pack("<d", float(v)))
        for v in f.get("orientation", [0.0, 0.0, 0.0, 1.0]):
            hasher.update(struct.pack("<d", float(v)))
        hasher.update(struct.pack("<d", float(f.get("altitude_m", 0.0))))
        hasher.update(struct.pack("<d", float(f.get("velocity_ms", 0.0))))
    return hasher.hexdigest()


def _trajectory_duration_ms(frames: list) -> int:
    if len(frames) < 2:
        return 0
    first_ts = frames[0].get("timestamp_ms", 0)
    last_ts = frames[-1].get("timestamp_ms", 0)
    return max(int(last_ts) - int(first_ts), 0)


# Module-level singleton
_store = TelemetryStore()


def get_store() -> TelemetryStore:
    """Return the module-level TelemetryStore singleton."""
    return _store
