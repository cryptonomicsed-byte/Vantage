"""The master switch as the Execution panel uses it.

It lives in the DB (one row) so the control plane can show and change it, with
the environment kept as a hard brake. These tests hold the three-state
behaviour together, and the property that matters most: while the switch is
off, every per-daemon arm reads as "0" no matter what is stored.
"""
import pytest
import pytest_asyncio

from backend import trading_mode as tm
from backend.config import settings
from backend.db import get_db

DAEMON_KEY = "jupiter_signer_trading_enabled"


@pytest.fixture(autouse=True)
def _tool_auth(monkeypatch):
    monkeypatch.setattr(settings, "TOOL_TRADING", "test-tool-key", raising=False)


@pytest_asyncio.fixture(autouse=True)
async def _reset_switch(client):
    """The flag is global state in a shared DB — always leave it paper-only."""
    async with get_db() as db:
        await db.execute("UPDATE live_mode SET enabled=0")
        await db.commit()
    tm.invalidate_cache()
    yield
    async with get_db() as db:
        await db.execute("UPDATE live_mode SET enabled=0")
        await db.commit()
    tm.invalidate_cache()


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


def _tool_h():
    return {"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": "test-tool-key"}


@pytest.mark.asyncio
async def test_defaults_to_paper_only(client, registered_agent, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)

    r = await client.get("/api/trading/live-mode", headers=_h(registered_agent))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["source"] == "db"
    assert body["db_enabled"] is False


@pytest.mark.asyncio
async def test_enabling_is_recorded_and_takes_effect(client, registered_agent, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    async with get_db() as db:
        await db.execute("DELETE FROM trading_mode_audit")
        await db.commit()

    r = await client.put("/api/trading/live-mode", headers=_h(registered_agent),
                         json={"enabled": True, "note": "test"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    # the predicate the order path uses must agree immediately
    assert tm.global_live_enabled() is True

    async with get_db() as db:
        rows = await tm.recent_mode_changes(db, limit=5)
    assert rows[0]["action"] == "live_mode_on"
    assert rows[0]["scope"] == "global"
    assert rows[0]["new_state"] == "live"


@pytest.mark.asyncio
async def test_switch_governs_the_daemon_read(client, registered_agent, monkeypatch):
    """On -> the daemon read mirrors storage; off -> it reports "0"."""
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)

    # cannot arm while paper-only
    r = await client.put(f"/api/trading/daemon-settings/{DAEMON_KEY}",
                         headers=_h(registered_agent), json={"value": "1"})
    assert r.status_code == 409

    await client.put("/api/trading/live-mode", headers=_h(registered_agent),
                     json={"enabled": True})

    r = await client.put(f"/api/trading/daemon-settings/{DAEMON_KEY}",
                         headers=_h(registered_agent), json={"value": "1"})
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/trading/daemon-settings/{DAEMON_KEY}", headers=_tool_h())
    assert r.json()["value"] == "1"
    assert "global_override" not in r.json()

    # back to paper: stored stays "1", reported value must be "0"
    await client.put("/api/trading/live-mode", headers=_h(registered_agent),
                     json={"enabled": False})

    r = await client.get(f"/api/trading/daemon-settings/{DAEMON_KEY}", headers=_tool_h())
    body = r.json()
    assert body["value"] == "0"
    assert body["stored"] == "1"
    assert body["global_override"].startswith(tm.GLOBAL_LIVE_ENV)


@pytest.mark.asyncio
async def test_enable_live_strategy_refused_while_master_off(client, registered_agent, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    r = await client.post("/api/trading/strategies/999999/enable-live",
                          headers=_h(registered_agent))
    assert r.status_code == 409, r.text
    assert "master switch" in r.text.lower()


@pytest.mark.asyncio
async def test_env_brake_beats_the_switch(client, registered_agent, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "0")
    tm.invalidate_cache()

    r = await client.get("/api/trading/live-mode", headers=_h(registered_agent))
    assert r.json()["enabled"] is False
    assert r.json()["source"] == "env_brake"

    r = await client.put("/api/trading/live-mode", headers=_h(registered_agent),
                         json={"enabled": True})
    assert r.status_code == 409, r.text
    assert tm.GLOBAL_LIVE_ENV in r.text


@pytest.mark.asyncio
async def test_env_override_beats_the_switch(client, registered_agent, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    tm.invalidate_cache()

    r = await client.get("/api/trading/live-mode", headers=_h(registered_agent))
    body = r.json()
    assert body["enabled"] is True
    assert body["source"] == "env_override"
    assert body["db_enabled"] is False      # stored intent unchanged, override wins
