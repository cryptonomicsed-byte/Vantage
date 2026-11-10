"""Sharpe/Sortino annualisation: bars_per_year from the candle interval."""
import pytest

from backend import quant_client as qc


@pytest.mark.parametrize("iv,expect", [("1d", 365.0), ("1h", 8760.0), ("4h", 2190.0), ("15m", 35040.0),
                                       ("1w", 365 / 7), ("1D", 365.0), ("bogus", None), ("", None)])
def test_bars_per_year(iv, expect):
    got = qc.bars_per_year(iv)
    assert (got is None and expect is None) or abs(got - expect) < 1e-9


@pytest.mark.asyncio
async def test_payload_carries_bars_per_year_and_curve(monkeypatch):
    monkeypatch.setenv("QUANT_JULIA_ENABLED", "1")
    qc._cache.clear()
    sent = {}

    async def fake_http(method, path, body=None):
        sent.update(body or {})
        return {"ok": True, "data": {"sharpe_ratio": 0.5, "total_return_pct": 3.0,
                                     "equity_curve": [1.0, 1.1], "trades": [{"side": "long"}]}}

    monkeypatch.setattr(qc, "_http", fake_http)
    candles = [{"time": i, "open": 1 + i, "high": 2 + i, "low": 0.5 + i, "close": 1.5 + i, "volume": 10}
               for i in range(120)]
    out = await qc.backtest("BTC", candles, None, "1d")
    assert sent["params"]["bars_per_year"] == 365.0
    assert out["ok"] and out["bars_per_year"] == 365.0
    assert out["equity_curve"] == [1.0, 1.1] and out["trades"] == [{"side": "long"}]
    assert out["total_return_pct"] == 3.0
