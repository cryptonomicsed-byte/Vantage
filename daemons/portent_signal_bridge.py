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


# ── P1-09 ────────────────────────────────────────────────────────────────────
# The relay answers {"resolutions": [...]}, {"predictions": [...]} and
# {"leaderboard": [...]} -- dicts, not lists -- so the old `isinstance(data,
# list)` check sent nothing. Resolution rows name the field `outcome`, not
# `resolution`; /predictions rows are one per (market, predictor) with `side`
# and `stake`, so yes/no stakes are summed per market; leaderboard rows carry
# `composite` (0..100 by contracts/portent_program.composite_score), not
# `score`. Each push is a signal_record/1 (chain portent, source_ref
# portent:<market_id>, conviction capped 0.69) with today's legacy fields
# alongside, and state makes every resolution push once and every skew or
# reputation push only when it changes.
CAP = 0.69
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MAX_REMEMBERED = 5000


def _ulid(ts_s: float | None = None) -> str:
    import secrets
    n = (int((ts_s if ts_s is not None else time.time()) * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


def _rows(data, key: str) -> list:
    if isinstance(data, dict):
        rows = data.get(key)
        return rows if isinstance(rows, list) else []
    return data if isinstance(data, list) else []


def _record(market_id: str, type_: str, direction: str, conviction: float, detail: str,
            created_at: float | None, evidence: dict) -> dict:
    sym = f"PORT:{market_id[:15]}"
    conv = round(min(CAP, _clamp01(conviction)), 4)
    return {
        "schema": "signal_record/1",
        "id": _ulid(created_at),
        "created_at": int(created_at or time.time()),
        "source": "portent",
        "source_ref": f"portent:{market_id}",
        "asset": {"symbol": sym, "chain": "portent", "address": ""},
        "type": type_,
        "direction": direction,
        "conviction": conv,
        "producer": {"kind": "service", "agent_id": None, "version": "portent-bridge/2",
                     "lineage": "portent-relay", "env": os.environ.get("PORTENT_ENV", "contabo-vps"),
                     "blind": True},
        "evidence": evidence,
        "tags": ["portent"],
        "detail": detail[:500],
        # legacy fields alongside: ingested either way, SIGNAL_RECORD_V1 on or off
        "symbol": sym,
        "mint": "",
    }


def _remember(state: dict, key: str, ids) -> None:
    kept = list(dict.fromkeys(list(state.get(key, [])) + list(ids)))
    state[key] = kept[-_MAX_REMEMBERED:]


def _process_resolutions(state: dict) -> int:
    """Each resolution is pushed exactly once (state: pushed_resolutions)."""
    rows = _rows(_relay_get("/resolutions"), "resolutions")
    done = set(state.get("pushed_resolutions", []))
    pushed, newly = 0, []
    for item in rows:
        mid = str(item.get("market_id") or "")
        if not mid or mid in done:
            continue
        outcome = str(item.get("outcome") or "").lower()
        if outcome not in ("yes", "no"):
            continue
        sig = _record(mid, "resolution", "long" if outcome == "yes" else "short",
                      float(item.get("confidence") or 0.5),
                      f"resolved={outcome} oracle={str(item.get('oracle') or '')[:16]}",
                      item.get("resolved_at"),
                      {"cross_checks": int(item.get("cross_checks") or 0)})
        if _vantage_ingest(sig):
            pushed += 1
            newly.append(mid)
            done.add(mid)
    _remember(state, "pushed_resolutions", newly)
    return pushed


def _process_open_markets(state: dict) -> int:
    """Sum stakes by side per market; push a skew only when it is new or its
    direction or 0.1-bucket changed (state: pushed_momentum)."""
    rows = _rows(_relay_get("/predictions?status=open"), "predictions")
    markets: dict[str, dict] = {}
    for r in rows:
        mid = str(r.get("market_id") or "")
        if not mid:
            continue
        m = markets.setdefault(mid, {"yes": 0.0, "no": 0.0, "n": 0, "latest": 0})
        side = str(r.get("side") or "").lower()
        if side in ("yes", "no"):
            m[side] += float(r.get("stake") or 0)
            m["n"] += 1
            m["latest"] = max(m["latest"], int(r.get("created_at") or 0))
    seen = dict(state.get("pushed_momentum", {}))
    pushed = 0
    for mid, m in list(markets.items())[:200]:
        total = m["yes"] + m["no"]
        if total < 500:
            continue
        skew = abs(m["yes"] - m["no"]) / total
        if skew < 0.3:
            continue
        direction = "long" if m["yes"] > m["no"] else "short"
        mark = f"{direction}:{int(skew * 10)}"
        if seen.get(mid) == mark:
            continue
        sig = _record(mid, "momentum", direction, skew,
                      f"yes_stake={m['yes']:.0f} no_stake={m['no']:.0f} predictors={m['n']}",
                      m["latest"] or None,
                      {"yes_stake": m["yes"], "no_stake": m["no"], "predictors": m["n"], "skew": round(skew, 4)})
        if _vantage_ingest(sig):
            pushed += 1
            seen[mid] = mark
    state["pushed_momentum"] = dict(list(seen.items())[-_MAX_REMEMBERED:])
    return pushed


def _process_leaderboard(state: dict) -> int:
    """Top predictors by composite (0..100); push when a composite changes
    (state: pushed_reputation). Direction 'alert': a reputation pulse is not
    a call on any market."""
    rows = _rows(_relay_get("/leaderboard"), "leaderboard")
    seen = dict(state.get("pushed_reputation", {}))
    pushed = 0
    for entry in rows[:5]:
        pubkey = str(entry.get("pubkey") or "")[:64]
        composite = int(entry.get("composite") or 0)
        if not pubkey or composite < 10 or seen.get(pubkey) == composite:
            continue
        sig = _record(f"REP:{pubkey[:11]}", "reputation_pulse", "alert", composite / 100.0,
                      f"composite={composite} tier={entry.get('tier', '')} resolved={entry.get('resolved', 0)} "
                      f"hit_rate_bp={entry.get('hit_rate_bp', 0)}",
                      entry.get("updated_at"),
                      {"composite": composite, "resolved": int(entry.get("resolved") or 0),
                       "hit_rate_bp": int(entry.get("hit_rate_bp") or 0)})
        sig["source_ref"] = f"portent:reputation/{pubkey}"
        if _vantage_ingest(sig):
            pushed += 1
            seen[pubkey] = composite
    state["pushed_reputation"] = seen
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
