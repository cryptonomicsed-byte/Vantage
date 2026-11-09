"""P4-14: kanban quant pieces -- fail-open, read-only Julia/whale_watcher client.

The Julia service is mocked (httpx.MockTransport); no service is started.
"""
import importlib.util
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from backend import quant_client as qc

REPO = Path(__file__).resolve().parents[2]
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _candles(n=120):
    return [{"time": 1_700_000_000 + i * 86400, "open": 100 + i, "high": 101 + i, "low": 99 + i,
             "close": 100.5 + i, "volume": 1000.0} for i in range(n)]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("QUANT_JULIA_ENABLED", "WHALE_WATCHER_ENABLED", "QUANT_JULIA_URL", "WHALE_WATCHER_OUT"):
        monkeypatch.delenv(k, raising=False)
    qc._cache.clear()
    yield
    qc._cache.clear()


def _mock_httpx(monkeypatch, handler):
    calls = []
    real = _REAL_ASYNC_CLIENT

    def wrapped(request):
        calls.append(request)
        return handler(request)

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(wrapped)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


def _julia_ok(request):
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok", "service": "julia-quant"})
    body = json.loads(request.content)
    assert len(body["close"]) == len(body["open"]) == len(body["volume"])
    return httpx.Response(200, json={"ticker": body["ticker"], "sharpe_ratio": 1.2, "sortino_ratio": 1.5,
                                     "max_drawdown_pct": 8.0, "profit_factor": 1.7, "win_rate": 0.55,
                                     "total_trades": 9, "winning_trades": 5, "losing_trades": 4,
                                     "net_profit": 420.0, "net_profit_pct": 4.2, "calmar_ratio": 0.5,
                                     "avg_trade_duration_bars": 6.0, "unexpected": "dropped"})


def test_flags_default_off():
    assert qc.quant_enabled() is False and qc.whale_enabled() is False


async def test_off_means_no_network(monkeypatch):
    calls = _mock_httpx(monkeypatch, _julia_ok)
    r = await qc.backtest("BTC", _candles())
    assert r["enabled"] is False and r["ok"] is False
    s = await qc.status()
    assert s["quant_enabled"] is False and "quant_ok" not in s and s["posts_to_signal_pool"] is False
    assert qc.whale_alerts()["enabled"] is False
    assert calls == []


async def test_backtest_on(monkeypatch):
    monkeypatch.setenv("QUANT_JULIA_ENABLED", "1")
    monkeypatch.setenv("QUANT_JULIA_URL", "http://quant.test:9999/")
    calls = _mock_httpx(monkeypatch, _julia_ok)
    r = await qc.backtest("btc", _candles(), {"hma_len": 9999, "sl_mult": "2.5", "evil": 1})
    assert r["ok"] and r["symbol"] == "BTC" and r["metrics"]["sharpe_ratio"] == 1.2
    assert "unexpected" not in r["metrics"]
    assert r["params"] == {"hma_len": 200, "sl_mult": 2.5}  # clamped, unknown keys dropped
    assert str(calls[0].url) == "http://quant.test:9999/backtest"
    assert json.loads(calls[0].content)["params"] == {"hma_len": 200, "sl_mult": 2.5}
    r2 = await qc.backtest("btc", _candles(), {"hma_len": 9999, "sl_mult": "2.5", "evil": 1})
    assert r2["cached"] is True and len(calls) == 1


async def test_backtest_fail_open(monkeypatch):
    monkeypatch.setenv("QUANT_JULIA_ENABLED", "1")

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    _mock_httpx(monkeypatch, boom)
    r = await qc.backtest("ETH", _candles())
    assert r["enabled"] and r["ok"] is False and "unreachable" in r["error"]
    s = await qc.status()
    assert s["quant_ok"] is False and "quant_error" in s

    _mock_httpx(monkeypatch, lambda req: httpx.Response(500, json={"error": "x"}))
    assert "HTTP 500" in (await qc.backtest("ETH", _candles()))["error"]
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=[1, 2]))
    assert (await qc.backtest("ETH", _candles()))["ok"] is False


async def test_backtest_insufficient_candles(monkeypatch):
    monkeypatch.setenv("QUANT_JULIA_ENABLED", "1")
    calls = _mock_httpx(monkeypatch, _julia_ok)
    r = await qc.backtest("SOL", _candles(10))
    assert r["ok"] is False and "insufficient" in r["error"] and calls == []


def test_whale_alerts(tmp_path, monkeypatch):
    p = tmp_path / "w.jsonl"
    monkeypatch.setenv("WHALE_WATCHER_OUT", str(p))
    monkeypatch.setenv("WHALE_WATCHER_ENABLED", "1")
    assert qc.whale_alerts()["ok"] is False  # no file yet, still no raise
    rows = [{"tx_hash": f"0x{i}", "amount_usd": 600_000 + i * 1000, "chain": "ethereum"} for i in range(5)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n[1]\n")
    r = qc.whale_alerts(limit=3)
    assert r["ok"] and [a["tx_hash"] for a in r["alerts"]] == ["0x4", "0x3", "0x2"]
    assert qc.whale_alerts(min_usd=603_500)["count"] == 1
    assert qc.whale_alerts(chain="base")["count"] == 0


def _load_runner():
    spec = importlib.util.spec_from_file_location("ww_run", REPO / "services/whale_watcher/run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@dataclass
class _Alert:
    tx_hash: str
    amount_usd: float
    chain: str = "ethereum"
    timestamp: datetime = field(default_factory=lambda: datetime(2026, 11, 9, 12, 0))


def test_runner_off_and_roundtrip(tmp_path, monkeypatch, capsys):
    run = _load_runner()
    monkeypatch.delenv("WHALE_WATCHER_ENABLED", raising=False)
    assert run.main() == 0 and "off" in capsys.readouterr().out
    monkeypatch.setenv("WHALE_WATCHER_ENABLED", "1")
    monkeypatch.delenv("ETH_WSS_URL", raising=False)
    assert run.main() == 2  # refuses without an explicit free node URL
    p = tmp_path / "w.jsonl"
    rec = run.append_alert(_Alert("0xabc", 750_000.0), p)
    assert rec["timestamp"] == "2026-11-09T12:00:00" and rec["source_ref"] == "tx:0xabc"
    monkeypatch.setenv("WHALE_WATCHER_OUT", str(p))
    out = qc.whale_alerts()
    assert out["alerts"][0]["tx_hash"] == "0xabc"


def test_vendored_files_present():
    for rel in ("services/quant/julia/TradingOSQuant/src/TradingOSQuant.jl",
                "services/quant/julia/TradingOSQuant/test/runtests.jl",
                "services/quant/ares-quant-julia.service",
                "services/whale_watcher/whale_watcher.py",
                "protocols/kanban/proto/strategy.proto",
                "protocols/kanban/mcp/kanban_tools.ex"):
        assert (REPO / rel).is_file(), rel
    unit = (REPO / "services/quant/ares-quant-julia.service").read_text()
    assert "Environment=QUANT_JULIA_ENABLED=0" in unit and "JULIA_HOST=127.0.0.1" in unit
    assert 'get(ENV, "JULIA_HOST", "127.0.0.1")' in (
        REPO / "services/quant/julia/TradingOSQuant/src/server.jl").read_text()


def test_module_never_touches_pool():
    src = (REPO / "backend/quant_client.py").read_text()
    for forbidden in ("ingest_signal", "routers.intel", "signal_ledger", "trading_gate", "execution_engine"):
        assert forbidden not in src.split('"""', 2)[2], forbidden


async def test_routes(client, registered_agent, monkeypatch):
    h = {"X-Agent-Key": registered_agent["api_key"]}
    r = await client.get("/api/intel/quant/status", headers=h)
    assert r.status_code == 200 and r.json()["quant_enabled"] is False
    r = await client.get("/api/intel/quant/backtest/BTC", headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await client.get("/api/intel/quant/whales", headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False

    monkeypatch.setenv("QUANT_JULIA_ENABLED", "1")
    from backend import market_sources as ms

    async def fake_ohlc(symbol, interval, limit):
        return _candles(150)

    monkeypatch.setattr(ms, "ohlc", fake_ohlc)
    _mock_httpx(monkeypatch, _julia_ok)
    r = await client.get("/api/intel/quant/backtest/eth?hma_len=21", headers=h)
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["symbol"] == "ETH" and j["params"] == {"hma_len": 21}
