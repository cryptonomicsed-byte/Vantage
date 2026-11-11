"""The master switch governs the standalone signers, not just agent orders.

The standalone daemons (ares-jupiter-signer, base_trader, hyperliquid, sui,
polymarket, solana_engine, autotrade, freqtrade) never touch a strategy row —
they poll GET /trading/daemon-settings/{key} each cycle and execute if it says
"1". That read is therefore the only place a process-wide kill switch can reach
them, and these tests hold it to that.

Rules:
  * with the switch off, a stored "1" is reported as "0" (stored intent kept
    visible, never silently rewritten);
  * arming is refused (409) while paper-only — a toggle that looks live and
    does nothing is worse than no toggle;
  * disarming is always allowed, in any mode;
  * every arm/disarm is audited.
"""
import pytest

from backend import trading_mode as tm
from backend.config import settings
from backend.db import get_db

from .sentinel_helpers import operator

KEY = "jupiter_signer_trading_enabled"


@pytest.fixture(autouse=True)
def _tool_auth(monkeypatch):
    """System-tool auth for the daemon-facing read path."""
    monkeypatch.setattr(settings, "TOOL_TRADING", "test-tool-key", raising=False)
    monkeypatch.setattr(settings, "TOOL_SECURITY", "test-tool-key", raising=False)
    monkeypatch.setattr(settings, "TOOL_INTEL", "test-tool-key", raising=False)


def _tool_h():
    return {"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": "test-tool-key"}


def _agent_h(agent):
    return {"X-Agent-Key": agent["api_key"]}


async def _store(key: str, value: str):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO daemon_settings (key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_stored_arm_reads_as_disabled_while_paper_only(client, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    await _store(KEY, "1")

    r = await client.get(f"/api/trading/daemon-settings/{KEY}", headers=_tool_h())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["value"] == "0", f"daemon would execute while paper-only: {body}"
    assert body["stored"] == "1", "operator intent must stay visible, not be rewritten"
    assert tm.GLOBAL_LIVE_ENV in body["global_override"]


@pytest.mark.asyncio
async def test_stored_arm_is_honoured_when_switch_on(client, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    monkeypatch.setattr("backend.trading_mode._db_live_flag", lambda: True)  # Layer 0 ceiling + switch
    await _store(KEY, "1")

    r = await client.get(f"/api/trading/daemon-settings/{KEY}", headers=_tool_h())
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "1"


@pytest.mark.asyncio
async def test_arming_refused_while_paper_only(client, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    op = await operator(client)
    r = await client.put(f"/api/trading/daemon-settings/{KEY}", headers=op["h"], json={"value": "1"})
    assert r.status_code == 409, r.text
    assert "paper only" in r.text.lower()


@pytest.mark.asyncio
async def test_disarming_always_allowed(client, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    op = await operator(client)
    r = await client.put(f"/api/trading/daemon-settings/{KEY}", headers=op["h"], json={"value": "0"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_agent_key_cannot_arm(client, registered_agent):
    r = await client.put(f"/api/trading/daemon-settings/{KEY}", headers=_agent_h(registered_agent), json={"value": "1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_disarm_is_audited(client, monkeypatch):
    async with get_db() as db:
        await db.execute("DELETE FROM trading_mode_audit")
        await db.commit()
    op = await operator(client)
    r = await client.put(f"/api/trading/daemon-settings/{KEY}", headers=op["h"], json={"value": "0"})
    assert r.status_code == 200, r.text

    async with get_db() as db:
        rows = await tm.recent_mode_changes(db, limit=5)

    assert rows, "disarming a standalone signer left no audit row"
    assert rows[0]["scope"] == "daemon"
    assert rows[0]["action"] == "daemon_disarm"
    assert rows[0]["note"] == KEY
    assert rows[0]["actor"].startswith(f"operator:{op['id']}")
