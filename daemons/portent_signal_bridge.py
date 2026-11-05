#!/usr/bin/env python3
"""
Portent relay → Vantage signal bridge.

Polls the local Portent relay (default port 8899) for resolved predictions,
high-activity markets, and reputation snapshots, then normalises them into
Vantage signal_pool entries via POST /api/intel/signals/ingest.

Fail-open: if the relay is down or the market list is empty this daemon
sleeps and retries without crashing.

What it emits as signals:
  - Newly resolved predictions (source="portent", type="resolution")
    direction = side that won (yes/no → bullish/bearish)
    conviction = oracle confidence
  - High-activity open markets with strong staking skew (type="momentum")
    conviction = stake_ratio clamped 0-1
  - Top predictor reputation updates (type="reputation_pulse")
    conviction = composite reputation score / 100

Env:
  PORTENT_RELAY     Portent relay base URL               [http://localhost:8899]
  VANTAGE_URL       Vantage base URL                     [http://localhost:8001]
  VANTAGE_TOOL_INTEL  tool key for X-Vantage-Tool-Key
  PORTENT_POLL_SEC  poll interval in seconds             [120]
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[portent-bridge] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("portent_bridge")

PORTENT_RELAY   = os.environ.get("PORTENT_RELAY",        "http://localhost:8899")
VANTAGE_URL     = os.environ.get("VANTAGE_URL",           "http://localhost:8001")
VANTAGE_KEY     = os.environ.get("VANTAGE_TOOL_INTEL",    "")
POLL_SEC        = int(os.environ.get("PORTENT_POLL_SEC",  "120"))

# State: track last seen resolution to avoid re-posting
_STATE_FILE = Path.home() / ".portent" / "bridge_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"last_resolution_id": None, "last_pushed_ts": 0}


def _save_state(state: dict):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _relay_get(path: str) -> dict | list | None:
    url = f"{PORTENT_RELAY}{path}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        log.debug("relay unreachable: %s", e)
        return None
    except Exception as e:
        log.debug("relay error: %s", e)
        return None


def _vantage_ingest(signal: dict) -> bool:
    """POST one signal to Vantage. Returns True on success."""
    if not VANTAGE_KEY:
        return False
    url = f"{VANTAGE_URL}/api/intel/signals/ingest"
    body = json.dumps(signal).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={
            "Content-Type": "application/json",
            "X-Vantage-Tool": "intel",
            "X-Vantage-Tool-Key": VANTAGE_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except urllib.error.HTTPError as e:
        log.warning("ingest HTTP %s: %s", e.code, e.read()[:120])
        return False
    except Exception as e:
        log.debug("ingest error: %s", e)
        return False


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _process_resolutions(state: dict) -> int:
    """Push newly resolved predictions as Vantage signals."""
    data = _relay_get("/resolutions")
    if not isinstance(data, list):
        return 0

    pushed = 0
    seen_ids = set()
    for item in data:
        rid = str(item.get("market_id", ""))
        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)

        resolution = item.get("resolution", "")  # "yes" / "no"
        confidence = _clamp01(item.get("confidence", 0.5))
        question = str(item.get("question") or item.get("title") or rid)[:80]

        direction = "bullish" if str(resolution).lower() in ("yes", "true", "1") else "bearish"

        sig = {
            "symbol": f"PORT:{rid[:20]}",
            "source": "portent",
            "type": "resolution",
            "conviction": confidence,
            "direction": direction,
            "detail": f"resolved={resolution} | {question}",
            "mint": "",
        }
        if _vantage_ingest(sig):
            pushed += 1

    return pushed


def _process_open_markets(state: dict) -> int:
    """Push momentum signals from open markets with strong staking skew."""
    data = _relay_get("/predictions?status=open")
    markets = data if isinstance(data, list) else (data or {}).get("predictions", [])
    if not markets:
        return 0

    pushed = 0
    for m in markets[:20]:  # top 20
        yes_stake  = float(m.get("yes_stake", 0) or 0)
        no_stake   = float(m.get("no_stake", 0) or 0)
        total      = yes_stake + no_stake
        if total < 500:  # skip markets with no meaningful stake
            continue

        skew = abs(yes_stake - no_stake) / total  # 0 = even, 1 = one-sided
        if skew < 0.3:  # not interesting enough
            continue

        direction = "bullish" if yes_stake > no_stake else "bearish"
        question = str(m.get("question") or m.get("title") or "")[:80]
        mid = str(m.get("market_id", ""))[:20]

        sig = {
            "symbol": f"PORT:{mid}",
            "source": "portent",
            "type": "momentum",
            "conviction": _clamp01(skew),
            "direction": direction,
            "detail": f"yes_stake={yes_stake:.0f} no_stake={no_stake:.0f} | {question}",
            "mint": "",
        }
        if _vantage_ingest(sig):
            pushed += 1

    return pushed


def _process_leaderboard(state: dict) -> int:
    """Push top-predictor reputation pulse."""
    data = _relay_get("/leaderboard")
    entries = data if isinstance(data, list) else (data or {}).get("leaderboard", [])
    if not entries:
        return 0

    pushed = 0
    for entry in entries[:5]:  # top 5 predictors
        pubkey = str(entry.get("pubkey", ""))[:64]
        score  = float(entry.get("score", 0) or 0)
        if score < 10:
            continue

        sig = {
            "symbol": f"PORT:REP:{pubkey[:16]}",
            "source": "portent",
            "type": "reputation_pulse",
            "conviction": _clamp01(score / 100.0),
            "direction": "bullish",
            "detail": f"score={score:.1f} wins={entry.get('wins',0)} trips={entry.get('trips',0)}",
            "mint": "",
        }
        if _vantage_ingest(sig):
            pushed += 1

    return pushed


def _one_cycle(state: dict) -> dict:
    """One full cycle. Returns updated state."""
    stats_raw = _relay_get("/stats")
    if stats_raw is None:
        log.debug("portent relay unreachable at %s", PORTENT_RELAY)
        return state

    total_pushed = 0
    total_pushed += _process_resolutions(state)
    total_pushed += _process_open_markets(state)
    total_pushed += _process_leaderboard(state)

    if total_pushed:
        log.info("portent cycle: pushed %d signals to vantage", total_pushed)
    else:
        log.debug("portent cycle: nothing new")

    state["last_pushed_ts"] = int(time.time())
    return state


def main():
    log.info("portent bridge starting — relay=%s poll=%ds", PORTENT_RELAY, POLL_SEC)
    state = _load_state()
    consecutive_errors = 0

    while True:
        try:
            state = _one_cycle(state)
            _save_state(state)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log.exception("cycle error (%d): %s", consecutive_errors, e)

        delay = min(POLL_SEC * (1 + consecutive_errors // 3), 600)
        time.sleep(delay)


if __name__ == "__main__":
    main()
