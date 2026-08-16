"""Signal persistence + stale-while-revalidate cache for /api/intel/signals.

Verifies ingested signals survive in the durable pool table, that the feed is
served fast from cache, and that a rehydrated snapshot means the feed is never
empty on a cold process.
"""
import asyncio
import os
import time

import pytest


def _tool_headers():
    """Signal ingestion is system-tool auth, not agent auth: the endpoint's own
    contract is "agents cannot post signals directly; signals come from
    infrastructure only". This test used an agent key and had been failing on
    the resulting 401."""
    return {
        "X-Vantage-Tool": "intel",
        "X-Vantage-Tool-Key": os.environ["VANTAGE_TOOL_INTEL"],
    }


@pytest.mark.asyncio
async def test_ingested_signal_persists_and_shows(client, fresh_agent):
    r = await client.post(
        "/api/intel/signals/ingest",
        json={"symbol": "PERSISTME", "source": "unit_test", "type": "alpha",
              "conviction": 0.9, "direction": "BUY", "detail": "durable"},
        headers=_tool_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ingested"

    # It must land in the durable pool table (survives a restart / empty in-mem pool).
    from backend.routers import intel
    pool = await intel._durable_pool(50)
    assert any(p["symbol"] == "PERSISTME" and p["source"] == "unit_test" for p in pool)


@pytest.mark.asyncio
async def test_signals_served_from_snapshot_when_cold(client, fresh_agent):
    """A cold process with a persisted snapshot returns instantly, not empty."""
    from backend.routers import intel

    agent = await fresh_agent()
    headers = {"X-Agent-Key": agent["api_key"]}

    # Seed a known snapshot and clear the in-memory cache to simulate a restart.
    snapshot = {
        "signals": [{"symbol": "SNAP", "source": "seed", "type": "alpha", "conviction": 0.8, "ts": int(time.time())}],
        "sources": ["seed"], "timestamp": int(time.time()),
    }
    await intel._persist_signals_snapshot(snapshot, int(time.time()))
    intel._signals_cache["data"] = None
    intel._signals_cache["ts"] = 0

    r = await client.get("/api/intel/signals?limit=10", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Rehydrated from the snapshot → SNAP present, served fast (cached true).
    syms = [s["symbol"] for s in body["signals"]]
    assert "SNAP" in syms
    assert body["cached"] is True
    assert "age_seconds" in body


@pytest.mark.asyncio
async def test_min_conviction_filter(client, fresh_agent):
    from backend.routers import intel

    agent = await fresh_agent()
    headers = {"X-Agent-Key": agent["api_key"]}

    intel._signals_cache["data"] = {
        "signals": [
            {"symbol": "HI", "source": "s", "type": "t", "conviction": 0.9, "ts": int(time.time())},
            {"symbol": "LO", "source": "s", "type": "t", "conviction": 0.1, "ts": int(time.time())},
        ],
        "sources": ["s"], "timestamp": int(time.time()),
    }
    intel._signals_cache["ts"] = int(time.time())

    r = await client.get("/api/intel/signals?limit=10&min_conviction=0.5", headers=headers)
    assert r.status_code == 200, r.text
    syms = [s["symbol"] for s in r.json()["signals"]]
    assert "HI" in syms and "LO" not in syms
