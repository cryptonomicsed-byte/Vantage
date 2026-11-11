"""Boot mode audit: the process records whether live was even possible.

httpx's ASGITransport does not run FastAPI lifespan events, so the startup hook
cannot be reached through the test client. This tests the function the hook
calls, which is the whole of the behaviour.
"""
import pytest

from backend import trading_mode as tm
from backend.db import get_db


@pytest.mark.asyncio
async def test_record_boot_mode_writes_paper_only_row(client, monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    async with get_db() as db:
        await db.execute("DELETE FROM trading_mode_audit")
        await db.commit()

    await tm.record_boot_mode()

    async with get_db() as db:
        rows = await tm.recent_mode_changes(db, limit=10)

    assert rows, "no boot row — the mode this process started in is untraceable"
    row = rows[0]
    assert row["scope"] == "global"
    assert row["action"] == "process_start"
    assert row["new_state"] == "paper_only"
    assert row["actor"] == "startup"
    assert "<unset>" in (row["note"] or "")


@pytest.mark.asyncio
async def test_record_boot_mode_writes_live_row_when_enabled(client, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    monkeypatch.setattr("backend.trading_mode._db_live_flag", lambda: True)  # Layer 0 ceiling + switch
    async with get_db() as db:
        await db.execute("DELETE FROM trading_mode_audit")
        await db.commit()

    await tm.record_boot_mode()

    async with get_db() as db:
        rows = await tm.recent_mode_changes(db, limit=10)

    assert rows[0]["new_state"] == "live_enabled"
    assert "VANTAGE_LIVE_TRADING=1" in (rows[0]["note"] or "")
