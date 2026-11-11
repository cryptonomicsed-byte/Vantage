"""council overview reports the signal pool (was always None)."""
import pytest

from backend.routers import council, intel


@pytest.mark.asyncio
async def test_overview_counts_council_signals(monkeypatch):
    async def fake_q(sql, params=()):
        return [{"n": 0}]

    async def fake_my(url):
        return {"status": "ok"}

    monkeypatch.setattr(council, "q", fake_q)
    monkeypatch.setattr(council, "my_get", fake_my)
    monkeypatch.setattr(intel, "_signal_pool", [{"source": "ares_council", "symbol": "BTC"},
                                               {"source": "kraken", "symbol": "ETH"}])
    out = await council.council_overview(agent={"id": 1})
    assert out["signal_pool"]["total"] == 2 and out["signal_pool"]["council"] == 1
    assert out["signal_pool"]["latest_council"]["symbol"] == "BTC"
