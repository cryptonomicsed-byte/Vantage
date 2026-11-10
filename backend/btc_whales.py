"""Largest confirmed BTC transactions per block (the Whales tab).

/api/intel/whales showed mempool.space /api/mempool/recent: the 10 newest
mempool transactions, typically 0.02-0.2 BTC -- not whales. There is no
free keyless "largest transactions" API reachable from this host
(Blockchair answers 430 to datacenter IPs), so this reads the newest block
from the Esplora API (mempool.space, blockstream.info as fallback), pages
through every transaction (25 per page, paced) and keeps the largest by
total output value. One scan per new block (~4,000 txs, ~160 requests at
WHALE_BLOCK_RPS, default 2/s), last WHALE_BLOCKS_KEEP blocks kept in memory.

Flag: WHALE_BLOCK_SCAN_ENABLED (default off). INTEL only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

ESPLORA = [u.strip() for u in os.environ.get(
    "WHALE_ESPLORA_URLS", "https://mempool.space/api,https://blockstream.info/api").split(",") if u.strip()]
RPS = float(os.environ.get("WHALE_BLOCK_RPS", "2"))
TOP_N = int(os.environ.get("WHALE_BLOCK_TOP", "25"))
KEEP = int(os.environ.get("WHALE_BLOCKS_KEEP", "6"))
POLL_S = float(os.environ.get("WHALE_BLOCK_POLL_S", "60"))

_blocks: List[Dict[str, Any]] = []        # newest first
_state: Dict[str, Any] = {"last_scan_at": None, "last_error": None, "scanning": None}


def enabled() -> bool:
    return os.environ.get("WHALE_BLOCK_SCAN_ENABLED", "0").lower() in ("1", "true", "yes", "on")


def summarize_tx(t: dict) -> Optional[dict]:
    """One Esplora tx -> whale row; None for coinbase (block reward, not a transfer)."""
    vin = t.get("vin") or []
    if any(v.get("is_coinbase") for v in vin):
        return None
    outs = [o for o in (t.get("vout") or []) if isinstance(o.get("value"), int)]
    total = sum(o["value"] for o in outs)
    biggest = max(outs, key=lambda o: o["value"], default=None)
    return {
        "txid": t.get("txid"),
        "value_btc": round(total / 1e8, 8),
        "largest_output_btc": round((biggest or {}).get("value", 0) / 1e8, 8),
        "largest_output_address": (biggest or {}).get("scriptpubkey_address"),
        "outputs": len(outs),
        "inputs": len(vin),
        "fee_sat": t.get("fee"),
        "vsize": round((t.get("weight") or 0) / 4) or None,
    }


def top_by_value(rows: List[dict], n: int = TOP_N) -> List[dict]:
    return sorted((r for r in rows if r), key=lambda r: -r["value_btc"])[:n]


async def _get(c: httpx.AsyncClient, path: str):
    last = None
    for base in ESPLORA:
        try:
            r = await c.get(base + path, timeout=15)
            if r.status_code == 200:
                return r.json()
            last = f"{base} HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = f"{base} {type(e).__name__}"
    raise RuntimeError(last or "no esplora endpoint")


async def scan_block(c: httpx.AsyncClient, block_hash: str) -> Dict[str, Any]:
    meta = await _get(c, f"/block/{block_hash}")
    n = int(meta.get("tx_count") or 0)
    rows: List[dict] = []
    for start in range(0, n, 25):
        page = await _get(c, f"/block/{block_hash}/txs/{start}")
        rows.extend(r for r in (summarize_tx(t) for t in page or []) if r)
        await asyncio.sleep(1.0 / max(RPS, 0.1))
    return {"height": meta.get("height"), "hash": block_hash, "time": meta.get("timestamp"),
            "tx_count": n, "scanned": len(rows), "top": top_by_value(rows)}


async def loop() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": "Vantage/1.0"}) as c:
        while True:
            try:
                tip = (await c.get(ESPLORA[0] + "/blocks/tip/hash", timeout=15)).text.strip()
                if tip and (not _blocks or _blocks[0]["hash"] != tip):
                    _state["scanning"] = tip
                    b = await scan_block(c, tip)
                    _blocks.insert(0, b)
                    del _blocks[KEEP:]
                    _state.update(last_scan_at=int(time.time()), last_error=None, scanning=None)
                    log.info("whale block %s: %d txs, top %.2f BTC", b["height"], b["tx_count"],
                             b["top"][0]["value_btc"] if b["top"] else 0)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                _state.update(last_error=str(e)[:200], scanning=None)
                log.debug("whale block scan failed: %s", e)
            await asyncio.sleep(POLL_S)


def snapshot(limit: int = 25, min_value_btc: Optional[float] = None) -> Dict[str, Any]:
    """Confirmed whales across the kept blocks, largest first."""
    rows = [{**r, "block_height": b["height"], "block_time": b["time"]} for b in _blocks for r in b["top"]]
    if min_value_btc is not None:
        rows = [r for r in rows if r["value_btc"] >= min_value_btc]
    rows.sort(key=lambda r: -r["value_btc"])
    return {"enabled": enabled(), "blocks": [{k: b[k] for k in ("height", "time", "tx_count", "scanned")} for b in _blocks],
            "transactions": rows[:limit], **_state}


def maybe_start() -> Optional[asyncio.Task]:
    return asyncio.create_task(loop()) if enabled() else None
