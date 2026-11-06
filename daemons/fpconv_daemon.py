#!/usr/bin/env python3
"""
fomopulse-convergence → Vantage signal daemon.

Polls the fomopulse tape (via fpconv), scores convergence + anomaly signals,
and pushes actionable ones to Vantage /api/intel/signals/ingest.

Fail-open: if the tape is unreachable (Cloudflare IP-block, outage, etc.)
this daemon sleeps and retries — no crash, no alert spam.

Signals written to both:
  - ~/.fpconv/fpconv.db  (alpha_clusters + alpha_signals_log, Vantage schema)
  - Vantage HTTP ingest (POST /api/intel/signals/ingest)

Env:
  FPCONV_REPO         path to fomopulse-convergence checkout  [/opt/ares/fomopulse-convergence]
  FPCONV_DB           path to sqlite store                     [~/.fpconv/fpconv.db]
  VANTAGE_URL         Vantage base URL                         [http://localhost:8001]
  VANTAGE_TOOL_INTEL  tool key for X-Vantage-Tool-Key header
  FPCONV_WINDOW       tape window                              [1h]
  FPCONV_TOP_TRADERS  number of roster traders to weight       [20]
  FPCONV_POLL_SEC     scan interval in seconds                 [300]
"""
from __future__ import annotations

import os
import sys
import time
import logging
import json

logging.basicConfig(
    level=logging.INFO,
    format="[fpconv-daemon] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("fpconv_daemon")

FPCONV_REPO   = os.environ.get("FPCONV_REPO",   "/opt/ares/fomopulse-convergence")
FPCONV_DB     = os.environ.get("FPCONV_DB",     os.path.expanduser("~/.fpconv/fpconv.db"))
VANTAGE_URL   = os.environ.get("VANTAGE_URL",   "http://localhost:8001")
VANTAGE_KEY   = os.environ.get("VANTAGE_TOOL_INTEL", "")
WINDOW        = os.environ.get("FPCONV_WINDOW",       "1h")
TOP_TRADERS   = int(os.environ.get("FPCONV_TOP_TRADERS", "20"))
POLL_SEC      = int(os.environ.get("FPCONV_POLL_SEC",    "300"))

sys.path.insert(0, os.path.join(FPCONV_REPO, "src"))


def _one_scan() -> dict:
    """Run one full scan cycle. Returns a summary dict."""
    from fpconv.client import TapeClient, TapeError
    from fpconv.roster import Roster
    from fpconv.convergence import scan as scan_fills
    from fpconv.signals import rank
    from fpconv.store import Store
    from fpconv.bridges import VantageBridge

    result = {"signals": 0, "pushed": 0, "error": None, "tape_blocked": False}
    store = Store(FPCONV_DB)
    client = TapeClient()

    # 1. Tape health check
    try:
        health = client.status()
        if not health.fresh:
            log.warning("tape lag %ds — signals will be stale", health.lag_seconds)
    except TapeError as e:
        msg = str(e)
        if "403" in msg or "Forbidden" in msg:
            result["tape_blocked"] = True
            log.info("fomopulse tape blocked (Cloudflare/IP block): %s — skipping scan", msg)
        else:
            log.warning("tape unreachable: %s — skipping scan", e)
        result["error"] = msg
        return result

    # 2. Build roster
    try:
        traders = client.traders(window="24h", limit=200)
    except TapeError as e:
        log.warning("traders fetch failed: %s", e)
        traders = []
    roster = Roster(traders)

    # 3. Fetch fills
    try:
        fills = client.tape(window=WINDOW, limit=800)
    except TapeError as e:
        log.warning("tape fetch failed: %s", e)
        result["error"] = str(e)
        return result

    # 4. Score convergence
    try:
        pools_raw = client.discover(window="3d", limit=100)
    except TapeError:
        pools_raw = []
    pools = {(r.get("token") or "").lower(): r for r in pools_raw if r.get("token")}

    tokens = scan_fills(fills, window_seconds={"1h": 3600, "6h": 21600, "24h": 86400}.get(WINDOW, 3600))
    signals = rank(tokens, roster, pools=pools, limit=50)
    actionable = [s for s in signals if s.actionable]
    result["signals"] = len(actionable)

    if not actionable:
        log.info("scan complete — no actionable signals this cycle")
        return result

    # 5. Store locally (Vantage-compatible alpha_clusters + alpha_signals_log)
    now = int(time.time())
    store.save_signals(signals, now)
    log.info("stored %d signals locally", len(signals))

    # 6. Push to Vantage HTTP
    if VANTAGE_KEY:
        bridge = VantageBridge(base=VANTAGE_URL, tool_key=VANTAGE_KEY)
        push_result = bridge.push(actionable, now=now)
        result["pushed"] = push_result.get("accepted", 0)
        log.info(
            "vantage push: %d attempted, %d accepted",
            push_result.get("attempted", 0),
            push_result.get("accepted", 0),
        )
        for r in push_result.get("results", []):
            if r.get("status") not in (200, 201):
                log.warning("push failed for %s: HTTP %s — %s", r.get("token"), r.get("status"), r.get("body", "")[:120])
    else:
        log.warning("VANTAGE_TOOL_INTEL not set — skipping HTTP push (signals stored locally only)")

    return result


def main():
    log.info("fpconv daemon starting — repo=%s poll=%ds", FPCONV_REPO, POLL_SEC)
    consecutive_errors = 0

    while True:
        try:
            result = _one_scan()
            if result.get("tape_blocked"):
                # Quiet backoff: tape Cloudflare blocks are common on VPS IPs
                log.debug("tape blocked — next retry in %ds", POLL_SEC)
                consecutive_errors += 1
            elif result.get("error"):
                consecutive_errors += 1
                log.warning("scan error (run %d): %s", consecutive_errors, result["error"])
            else:
                consecutive_errors = 0
        except ImportError as e:
            log.error("fpconv not importable from %s: %s", FPCONV_REPO, e)
            consecutive_errors += 1
        except Exception as e:
            log.exception("unexpected error in scan: %s", e)
            consecutive_errors += 1

        # Progressive backoff capped at 30 min
        delay = min(POLL_SEC * (1 + consecutive_errors // 5), 1800)
        log.debug("sleeping %ds", delay)
        time.sleep(delay)


if __name__ == "__main__":
    main()
