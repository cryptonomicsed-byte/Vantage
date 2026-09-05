#!/usr/bin/env python3
"""Bridge: Polymarket → Vantage intel signals + Mycelium traces.

Conviction used to be `abs(top - 0.5) * 10` capped at 7.0 -- a 0-5 range
with a 0-7 cap, against a platform contract (backend/routers/intel.py's
ingest_signal) of strictly 0-1, enforced server-side. Silently 422'd on
every post priced past 57%. Now `min(abs(top - 0.5) * 2, 1.0)`: 0.5 priced
= coin flip = 0.0, 1.0 priced = settled = 1.0, clamped so nothing overshoots.

2026-08-31: also emits a real Mycelium observation trace per market
ingested, same {agent, session, kind, action, target, outcome, payload}
contract backend/mycelium_bridge.py's emitters use for Vantage's own
signal sources -- this script can't import that module directly
(standalone /opt/ares deployment, no backend.* package on its path), so
_mycelium_trace() below is a small self-contained equivalent posting to
the same real gateway endpoint. Without this, mycelium/miners/
cross_domain_signal.py is structurally blind to Polymarket signals --
closes that gap the moment there's real overlapping data.
"""
import os, json, urllib.request, urllib.error, time
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")  # deprecated, use VANTAGE_TOOL_* instead
POLYMARKET_URL = "https://gamma-api.polymarket.com"
MYCELIUM_URL = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")
INTERVAL = int(os.environ.get("POLY_BRIDGE_INTERVAL", "600"))

HEADERS = {"User-Agent": "curl/8.0"}

def vantage_post(endpoint, data):
    """Post to Vantage using system tool auth (not agent key)."""
    # Determine tool from endpoint
    if "/trading/signals" in endpoint:
        tool, tool_key = "trading", os.environ.get("VANTAGE_TOOL_TRADING_KEY", os.environ.get("VANTAGE_TOOL_TRADING", ""))
    elif "/intel/signals" in endpoint:
        tool, tool_key = "intel", os.environ.get("VANTAGE_TOOL_INTEL_KEY", os.environ.get("VANTAGE_TOOL_INTEL", ""))
    elif "/security" in endpoint:
        tool, tool_key = "security", os.environ.get("VANTAGE_TOOL_SECURITY_KEY", os.environ.get("VANTAGE_TOOL_SECURITY", ""))
    else:
        raise ValueError(f"Unknown endpoint: {endpoint}")

    if not tool_key:
        raise ValueError(f"VANTAGE_TOOL_{tool.upper()} not set")

    req = urllib.request.Request(
        f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Vantage-Tool": tool,
            "X-Vantage-Tool-Key": tool_key,
            "User-Agent": "daemon/1.0"
        }
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def _mycelium_trace(target, payload, outcome="info"):
    """Real, fail-soft observation-trace POST to the Mycelium gateway --
    same contract backend/mycelium_bridge.py's post_observation() uses,
    reimplemented standalone here (see module docstring). Never raises."""
    body = {
        "agent": "polymarket_bridge", "session": "polymarket-ingest-cycle",
        "kind": "observation", "action": "prediction_market", "target": target,
        "outcome": outcome, "payload": payload,
    }
    try:
        req = urllib.request.Request(
            f"{MYCELIUM_URL}/api/trace",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False

def fetch_markets(limit=20):
    """Fetch trending prediction markets."""
    req = urllib.request.Request(
        f"{POLYMARKET_URL}/markets?limit={limit}&order=volume24hr&ascending=false",
        headers=HEADERS
    )
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    return data if isinstance(data, list) else data.get("data", [])

def cycle():
    markets = fetch_markets()
    count = 0
    traces = 0
    for m in markets:
        title = m.get("question", m.get("title", ""))[:80]
        raw_prices = m.get("outcomePrices", []); outcomes = json.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices if isinstance(raw_prices, list) else [])
        volume = float(str(m.get("volume24hr", m.get("volume", 0)) or 0))
        if volume < 10000:
            continue
        top_outcome = max(float(o) for o in outcomes) if outcomes else 0
        # Real endpoint contract (backend/routers/intel.py::ingest_signal) is
        # strictly 0-1, enforced server-side -- normalise here rather than
        # the old 0-5-ish scale that silently 422d on every post.
        conviction = min(abs(top_outcome - 0.5) * 2, 1.0)
        tag = m.get("tags", [{}])[0].get("label", "prediction") if m.get("tags") else "prediction"
        symbol = tag.upper()[:10] if tag else "PREDICT"
        vantage_post("/api/intel/signals/ingest", {
            "symbol": symbol,
            "source": "polymarket",
            "conviction": conviction,
            "type": "prediction_market",
            "chain": "polygon",
            "detail": f"'{title}' | Vol:${volume:,.0f} | Top outcome:{top_outcome:.1%}"
        })
        count += 1
        condition_id = m.get("conditionId") or m.get("id") or symbol
        if _mycelium_trace(
            str(condition_id),
            {
                "symbol": symbol, "title": title, "tag": tag,
                "volume24hr": volume, "top_outcome": round(top_outcome, 4),
                "conviction": round(conviction, 4),
            },
            outcome="success",
        ):
            traces += 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Polymarket: {count} markets ingested, {traces} mycelium traces emitted")

if __name__ == "__main__":
    print(f"Polymarket Bridge ({INTERVAL}s cycle)")
    while True:
        try: cycle()
        except Exception as e: print(f"Error: {e}")
        time.sleep(INTERVAL)
