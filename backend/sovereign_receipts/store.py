"""Receipt persistence — disk-backed store for capture/scene receipts.
Ported from sovereign-node receipt_store.rs."""

import json
import os
import re
import time
from dataclasses import dataclass, asdict, field
from threading import Lock
from typing import Optional


@dataclass
class ReceiptRecord:
    kind: int  # 31020 = CaptureReceipt, 31030 = SceneReceipt
    receipt_id: str
    twin_id: str
    device_id: str
    scene_receipt_id: str
    capture_receipt_id: str
    completed_at: int
    sui_object_id: Optional[str] = None
    dip_message_count: int = 0
    odu_tile: Optional[str] = None


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\-_]", "_", s)


class ReceiptStore:
    def __init__(self, data_dir: str):
        self._dir = os.path.join(data_dir, "receipts")
        os.makedirs(self._dir, exist_ok=True)
        self._lock = Lock()
        self._cache: list[ReceiptRecord] = self._load_all()

    def _load_all(self) -> list[ReceiptRecord]:
        records = []
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                records.append(ReceiptRecord(**data))
            except Exception:
                pass
        return records

    def save(self, record: ReceiptRecord) -> None:
        path = os.path.join(self._dir, f"{_sanitize(record.twin_id)}.json")
        with self._lock:
            with open(path, "w") as f:
                json.dump(asdict(record), f, indent=2)
            self._cache.append(record)

    def list(self) -> list[ReceiptRecord]:
        with self._lock:
            return sorted(self._cache, key=lambda r: r.completed_at, reverse=True)

    def get_by_twin(self, twin_id: str) -> Optional[ReceiptRecord]:
        with self._lock:
            return next((r for r in self._cache if r.twin_id == twin_id), None)

    def get_by_tile(self, tile_id: str) -> list[ReceiptRecord]:
        with self._lock:
            return [r for r in self._cache if r.odu_tile == tile_id]

    def count(self) -> int:
        with self._lock:
            return len(self._cache)

    @classmethod
    def in_memory(cls) -> "ReceiptStore":
        import tempfile
        return cls(tempfile.mkdtemp())
