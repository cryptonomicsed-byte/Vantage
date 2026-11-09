"""P4-14 runner for the vendored kanban whale_watcher (whale_watcher.py, unchanged).

WHALE_WATCHER_ENABLED off (default) -> exit 0 before importing web3.
ETH_WSS_URL must be set explicitly (free / self-hosted node). The vendored
module's keyed provider default is never used: this runner refuses to start
without ETH_WSS_URL, so no API key is needed or read.

Every WhaleAlert is appended as one JSON line to WHALE_WATCHER_OUT (default
$VANTAGE_DATA_DIR/whale_watcher.jsonl). Vantage reads that file read-only via
backend/quant_client.py (GET /api/intel/quant/whales). Nothing here posts to the
signal pool or places an order.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

MAX_FILE_BYTES = int(os.environ.get("WHALE_WATCHER_MAX_BYTES", str(50 * 1024 * 1024)))


def enabled() -> bool:
    return os.environ.get("WHALE_WATCHER_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def out_path() -> Path:
    default = Path(os.environ.get("VANTAGE_DATA_DIR", "/opt/ares/data")) / "whale_watcher.jsonl"
    return Path(os.environ.get("WHALE_WATCHER_OUT", str(default)))


def alert_to_record(alert: Any) -> Dict[str, Any]:
    d = dataclasses.asdict(alert) if dataclasses.is_dataclass(alert) else dict(alert)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    d["source"] = "kanban_whale_watcher"
    d["source_ref"] = f"tx:{d.get('tx_hash', '')}"
    return d


def append_alert(alert: Any, path: Path | None = None) -> Dict[str, Any]:
    p = path or out_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size > MAX_FILE_BYTES:
        p.replace(p.with_suffix(p.suffix + ".1"))  # single-generation rotation
    rec = alert_to_record(alert)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


async def _main() -> int:
    wss = os.environ.get("ETH_WSS_URL", "").strip()
    if not wss:
        print("whale_watcher: ETH_WSS_URL not set; refusing to start (no keyed default)", file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from whale_watcher import WhaleWatcherAgent  # needs web3, structlog, httpx

    agent = WhaleWatcherAgent(rpc_wss=wss)
    await agent.w3.provider.connect()

    async def on_alert(a):
        append_alert(a)

    tasks = [agent.stream_blocks(on_alert)]
    if os.environ.get("WHALE_WATCHER_PENDING", "0").strip().lower() in ("1", "true", "yes", "on"):
        tasks.append(agent.stream_pending(on_alert))
    await asyncio.gather(*tasks)
    return 0


def main() -> int:
    if not enabled():
        print("WHALE_WATCHER_ENABLED off; not starting")
        return 0
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
