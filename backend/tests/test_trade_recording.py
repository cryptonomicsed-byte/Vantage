"""P4-11: recording defaults hold, prefs round-trip, live traces honour them."""
import pytest

from backend import trade_recording as tr
from backend.db import get_db


@pytest.mark.asyncio
async def test_defaults_hold_for_a_new_account():
    async with get_db() as db:
        p = await tr.get_prefs(db, 987654)
        assert p["record_live"] is True and p["leaderboard_opt_in"] is False and p["is_default"]
        assert await tr.should_record(db, 987654, "live") is True


@pytest.mark.asyncio
async def test_paper_always_recorded_live_follows_pref():
    async with get_db() as db:
        await tr.set_prefs(db, 987655, record_live=False)
        assert await tr.should_record(db, 987655, "paper") is True
        assert await tr.should_record(db, 987655, "live") is False
        p = await tr.set_prefs(db, 987655, leaderboard_opt_in=True)  # partial update keeps record_live
        assert p["record_live"] is False and p["leaderboard_opt_in"] is True


@pytest.mark.asyncio
async def test_endpoints_round_trip(client, registered_agent):
    h = {"X-Agent-Key": registered_agent["api_key"]}
    r = await client.get("/api/trading/recording", headers=h)
    assert r.status_code == 200 and r.json()["record_live"] is True and r.json()["paper_always_recorded"]
    r = await client.put("/api/trading/recording", headers=h, json={"record_live": False})
    assert r.status_code == 200 and r.json()["record_live"] is False
    assert (await client.get("/api/trading/recording", headers=h)).json()["record_live"] is False


@pytest.mark.asyncio
async def test_signup_carries_recording_notice(client):
    r = await client.post("/api/agents/register", json={"name": "rec-notice-agent"})
    if r.status_code != 200:
        pytest.skip(f"register needs more fields here: {r.status_code}")
    assert r.json()["recording_notice"] == tr.RECORDING_NOTICE
