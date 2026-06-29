"""Market-data layer tests — direct no-auth sources + live-valued positions.

Network is always mocked so these are deterministic and offline-safe.
"""
import pytest

from backend import market_sources as ms


# ── market_sources unit tests (no app needed) ─────────────────────────────────────

def test_sources_registry_complete():
    # The full no-auth API catalog is surfaced for transparency.
    assert len(ms.SOURCES) >= 34
    integrated = [s for s in ms.SOURCES if s["integrated"]]
    assert any(s["name"] == "Pyth Network" for s in integrated)
    assert any(s["name"] == "CoinGecko" for s in integrated)
    # Every entry is well-formed.
    for s in ms.SOURCES:
        assert {"name", "category", "url", "integrated"} <= set(s)


@pytest.mark.asyncio
async def test_exchange_spreads_computes_real_spread(monkeypatch):
    # Mock each venue fetcher with distinct prices → a real, ranked spread.
    monkeypatch.setattr(ms, "_binance", lambda s: _ret(100.0))
    monkeypatch.setattr(ms, "_okx", lambda s: _ret(101.0))
    monkeypatch.setattr(ms, "_kucoin", lambda s: _ret(99.0))   # cheapest → buy venue
    monkeypatch.setattr(ms, "_coinbase", lambda s: _ret(102.0))  # priciest → sell venue
    monkeypatch.setattr(ms, "_gemini", lambda s: _ret(None))
    ms._cache.clear()

    out = await ms.exchange_spreads("BTC")
    assert out["buy_venue"] == "kucoin"
    assert out["sell_venue"] == "coinbase"
    assert out["spread_pct"] == pytest.approx((102 - 99) / 99 * 100, abs=0.01)
    assert len(out["venues"]) == 4  # gemini returned None → excluded


@pytest.mark.asyncio
async def test_resolve_price_prefers_pyth_then_caches(monkeypatch):
    calls = {"pyth": 0, "cg": 0}

    async def fake_pyth(syms):
        calls["pyth"] += 1
        return {"BTC": 64000.0}

    async def fake_cg(sym):
        calls["cg"] += 1
        return 1.23

    monkeypatch.setattr(ms, "_pyth_prices", fake_pyth)
    monkeypatch.setattr(ms, "_coingecko_price", fake_cg)
    ms._cache.clear()

    p1 = await ms.resolve_price("BTC")
    p2 = await ms.resolve_price("BTC")  # served from cache
    assert p1 == 64000.0 and p2 == 64000.0
    assert calls["pyth"] == 1  # second call hit the cache
    assert calls["cg"] == 0    # CoinGecko fallback not needed for a Pyth major


@pytest.mark.asyncio
async def test_resolve_price_falls_back_to_coingecko(monkeypatch):
    async def empty_pyth(syms):
        return {}

    async def fake_cg(sym):
        return 7.77

    monkeypatch.setattr(ms, "_pyth_prices", empty_pyth)
    monkeypatch.setattr(ms, "_coingecko_price", fake_cg)
    ms._cache.clear()

    assert await ms.resolve_price("RNDR") == 7.77


def _ret(v):
    async def _coro(*a, **k):
        return v
    return _coro(0)


# ── /api/trading/positions integration (live-valued) ──────────────────────────────

def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_positions_live_valuation(client, registered_agent, monkeypatch):
    # Deterministic, mutable "live" price shared by paper-fill and positions valuation.
    holder = {"p": 100.0}

    async def fake_resolve(symbol):
        return holder["p"]

    monkeypatch.setattr(ms, "resolve_price", fake_resolve)

    # Log a BUY limit order and paper-fill it at 100.
    r = await client.post("/api/trading/orders", headers=_h(registered_agent), json={
        "symbol": "BTC", "side": "buy", "chain": "bitcoin",
        "quantity": 2, "price": 100.0, "order_type": "limit",
    })
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    rf = await client.post(f"/api/trading/orders/{oid}/paper-fill", headers=_h(registered_agent))
    assert rf.status_code == 200, rf.text

    # Price moves up to 150 → +50% unrealized on a 2-unit position.
    holder["p"] = 150.0
    rp = await client.get("/api/trading/positions", headers=_h(registered_agent))
    assert rp.status_code == 200, rp.text
    body = rp.json()
    btc = next(p for p in body["positions"] if p["symbol"] == "BTC")
    assert btc["net_quantity"] == 2
    assert btc["avg_cost"] == pytest.approx(100.0, abs=0.01)
    assert btc["live_price"] == 150.0
    assert btc["market_value_usd"] == pytest.approx(300.0, abs=0.01)
    assert btc["unrealized_pnl_usd"] == pytest.approx(100.0, abs=0.01)
    assert btc["unrealized_pnl_pct"] == pytest.approx(50.0, abs=0.1)


@pytest.mark.asyncio
async def test_positions_requires_agent_key(client):
    r = await client.get("/api/trading/positions")
    assert r.status_code == 401
