"""Tests for POST /api/pine/indicators/{id}/signal — wiring a saved Pine
indicator's plotshape() markers into a real (paper-fill) order tagged for
trade_outcome_learner.py's source_performance tracking. Mocks the
pine-runtime HTTP call (same pattern as test_execute_live_rpc_error.py's
httpx.AsyncClient monkeypatch) rather than requiring a live sandbox --
pine-runtime's own real grammar/sandbox coverage lives in
pine-runtime/test.js.
"""
import pytest

from backend.routers.pine import evaluate_pine_signal, save_indicator
from backend.deps import _parse_body


class _FakeRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def pinned_quote(monkeypatch):
    from backend.routers import trading
    async def fake_fetch_quote(symbol):
        return 55000.0
    monkeypatch.setattr(trading, "_fetch_quote", fake_fetch_quote)


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    from backend.config import settings
    import backend.db as db_mod

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "vantage.db")
    return db_mod


async def _make_agent(db_mod):
    from backend.routers.pine import init_pine_db

    await db_mod.init_agents_db()
    await init_pine_db()
    async with db_mod.get_db() as db:
        await db.execute("INSERT INTO agents (id, name, api_key) VALUES (1, 'test-agent', 'k')")
        await db.commit()
    return {"id": 1, "name": "test-agent"}


def _fake_candles(n=60):
    out = []
    base = 1700000000
    for i in range(n):
        c = 100 + i
        out.append({"time": base + i * 86400, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 10})
    return out


def _patch_pine_runtime(monkeypatch, result: dict, status: int = 200):
    from backend.routers import pine as pine_mod

    class _Resp:
        status_code = status
        headers = {"content-type": "application/json"}
        def json(self):
            return result
        text = "err"

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **kw):
            return _Resp()

    monkeypatch.setattr(pine_mod.httpx, "AsyncClient", _FakeClient)


def _patch_candles(monkeypatch, candles):
    from backend.routers import pine as pine_mod
    async def fake_ohlc(symbol, interval="1d", limit=200):
        return candles
    monkeypatch.setattr(pine_mod.ms, "ohlc", fake_ohlc)


@pytest.mark.asyncio
async def test_no_markers_returns_honest_false(db_env, monkeypatch):
    agent = await _make_agent(db_env)
    saved = await save_indicator(
        _FakeRequest({"name": "MACD", "script": "indicator('x')\nplot(close, 'c')"}), agent
    )
    candles = _fake_candles()
    _patch_candles(monkeypatch, candles)
    _patch_pine_runtime(monkeypatch, {"plots": {"c": [{"time": 1, "value": 1.0}]}, "markers": {}, "alerts": []})

    result = await evaluate_pine_signal(saved["id"], _FakeRequest({"symbol": "BTC"}), agent)
    assert result["triggered"] is False
    assert "markers" in result["reason"]


@pytest.mark.asyncio
async def test_no_marker_true_on_last_bar_returns_false(db_env, monkeypatch):
    agent = await _make_agent(db_env)
    saved = await save_indicator(
        _FakeRequest({"name": "RSI Div", "script": "indicator('x')\nplotshape(close > 0, 'Bearish Div')"}), agent
    )
    candles = _fake_candles(3)
    _patch_candles(monkeypatch, candles)
    markers = {"Bearish Div": [{"time": c["time"], "value": False} for c in candles]}
    _patch_pine_runtime(monkeypatch, {"plots": {}, "markers": markers, "alerts": []})

    result = await evaluate_pine_signal(saved["id"], _FakeRequest({"symbol": "BTC"}), agent)
    assert result["triggered"] is False
    assert result["checked_markers"] == ["Bearish Div"]


@pytest.mark.asyncio
async def test_bullish_marker_triggers_buy_preview_without_execute(db_env, monkeypatch):
    agent = await _make_agent(db_env)
    saved = await save_indicator(
        _FakeRequest({"name": "RSI Div", "script": "indicator('x')\nplotshape(close > 0, 'Bullish Div')"}), agent
    )
    candles = _fake_candles(3)
    _patch_candles(monkeypatch, candles)
    markers = {"Bullish Div": [{"time": c["time"], "value": (i == len(candles) - 1)} for i, c in enumerate(candles)]}
    _patch_pine_runtime(monkeypatch, {"plots": {}, "markers": markers, "alerts": []})

    result = await evaluate_pine_signal(saved["id"], _FakeRequest({"symbol": "BTC"}), agent)
    assert result["triggered"] is True
    assert result["side"] == "BUY"
    assert result["matched_markers"] == ["Bullish Div"]
    assert result["order"] is None  # execute not passed -> preview only


@pytest.mark.asyncio
async def test_bearish_marker_triggers_sell_direction(db_env, monkeypatch):
    agent = await _make_agent(db_env)
    saved = await save_indicator(
        _FakeRequest({"name": "RSI Div", "script": "indicator('x')\nplotshape(close > 0, 'Bearish Div')"}), agent
    )
    candles = _fake_candles(3)
    _patch_candles(monkeypatch, candles)
    markers = {"Bearish Div": [{"time": c["time"], "value": (i == len(candles) - 1)} for i, c in enumerate(candles)]}
    _patch_pine_runtime(monkeypatch, {"plots": {}, "markers": markers, "alerts": []})

    result = await evaluate_pine_signal(saved["id"], _FakeRequest({"symbol": "BTC"}), agent)
    assert result["triggered"] is True
    assert result["side"] == "SELL"


@pytest.mark.asyncio
async def test_execute_true_creates_and_paper_fills_real_order_tagged_for_learner(db_env, monkeypatch):
    agent = await _make_agent(db_env)
    saved = await save_indicator(
        _FakeRequest({"name": "MACD Custom", "script": "indicator('x')\nplotshape(close > 0, 'Bullish Div')"}), agent
    )
    candles = _fake_candles(3)
    _patch_candles(monkeypatch, candles)
    markers = {"Bullish Div": [{"time": c["time"], "value": (i == len(candles) - 1)} for i, c in enumerate(candles)]}
    _patch_pine_runtime(monkeypatch, {"plots": {}, "markers": markers, "alerts": []})

    # quantity kept small enough to clear the real $50 fallback risk-limit
    # cap (backend.routers.trading._FALLBACK_MAX_POSITION_USD) at the
    # pinned $55000 quote -- proves _enforce_risk_limits (real risk logic)
    # runs on this path exactly as it would for a manually-placed order.
    result = await evaluate_pine_signal(
        saved["id"],
        _FakeRequest({"symbol": "BTC", "execute": True, "quantity": 0.0005}),
        agent,
    )
    assert result["triggered"] is True
    assert result["order"] is not None
    assert result["order"]["status"] == "pending"
    assert result["fill"]["status"] == "filled"

    # Real order landed with the pine: source tag trade_outcome_learner.py
    # expects (_source_for_order reads trigger_reason directly).
    import aiosqlite
    async with db_env.get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM trading_orders WHERE id=?", (result["order"]["id"],)
        )).fetchone()
    order = dict(row)
    assert order["trigger_reason"] == f"pine:{saved['id']}:MACD Custom"
    assert order["status"] == "filled"
    assert order["avg_fill_price"] == pytest.approx(55000.0)

    # And it's real, learnable data: trade_outcome_learner picks it up once
    # marked "old enough" -- verify the source tag round-trips through the
    # same _source_for_order() the learner actually uses.
    from backend.trade_outcome_learner import _source_for_order
    assert _source_for_order(order) == f"pine:{saved['id']}:MACD Custom"
