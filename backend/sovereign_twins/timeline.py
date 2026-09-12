"""Disk-backed store for TwinTimelines. Ported from sovereign-node timeline_store.rs."""

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from threading import Lock
from typing import Optional


@dataclass
class TwinTimelineEntry:
    entry_id: str
    twin_id: str
    receipt_id: str
    kind: str  # "capture" | "scene" | "simulation"
    timestamp: int
    quality_score: float = 0.0
    modalities: list = field(default_factory=list)
    odu_tile: Optional[str] = None


@dataclass
class TwinTimeline:
    timeline_id: str
    entries: list = field(default_factory=list)
    version: int = 0

    def append(self, entry: TwinTimelineEntry) -> None:
        self.entries.append(entry)
        self.version += 1


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\-_]", "_", s)


class TimelineStore:
    def __init__(self, data_dir: str):
        self._dir = os.path.join(data_dir, "timelines")
        os.makedirs(self._dir, exist_ok=True)
        self._lock = Lock()
        self._cache: dict[str, TwinTimeline] = self._load_all()

    def _load_all(self) -> dict[str, TwinTimeline]:
        timelines = {}
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                tl = TwinTimeline(
                    timeline_id=data["timeline_id"],
                    entries=[TwinTimelineEntry(**e) for e in data.get("entries", [])],
                    version=data.get("version", 0),
                )
                timelines[tl.timeline_id] = tl
            except Exception:
                pass
        return timelines

    def _persist(self, tl: TwinTimeline) -> None:
        safe = _sanitize(tl.timeline_id)
        path = os.path.join(self._dir, f"{safe}.json")
        data = {
            "timeline_id": tl.timeline_id,
            "version": tl.version,
            "entries": [asdict(e) for e in tl.entries],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def append(self, timeline_id: str, entry: TwinTimelineEntry) -> None:
        with self._lock:
            if timeline_id not in self._cache:
                self._cache[timeline_id] = TwinTimeline(timeline_id=timeline_id)
            tl = self._cache[timeline_id]
            tl.append(entry)
            self._persist(tl)

    def get(self, timeline_id: str) -> Optional[TwinTimeline]:
        with self._lock:
            return self._cache.get(timeline_id)

    def all(self) -> list[TwinTimeline]:
        with self._lock:
            return list(self._cache.values())

    @classmethod
    def in_memory(cls) -> "TimelineStore":
        import tempfile
        return cls(tempfile.mkdtemp())
