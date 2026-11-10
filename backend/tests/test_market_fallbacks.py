"""Pyth Hermes 401 fallbacks + sentiment dead band / mixed verdict."""
import pytest

from backend import market_sources as ms
from backend.routers import intel as I


def _paprika(*_a, **_k):
    async def f(*_a, **_k):
        return [{"symbol": "BTC", "price": 74000.0, "market_cap": 1.4e12, "rank": 1, "change_24h": 0.4, "volume_24h": 3e10},
                {"symbol": "ETH", "price": 2100.0, "market_cap": 2.5e11, "rank": 2, "change_24h": -0.4, "volume_24h": 1e10},
                {"symbol": "SOL", "price": 86.0, "market_cap": 4e10, "rank": 6, "change_24h": 22.0, "volume_24h": 3e9}]
    return f


class _Resp:
    status_code = 401
    text = "unauthorized"

    def json(self):
        return {}


class _Client:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _Resp()


@pytest.mark.asyncio
async def test_majors_fall_back_when_pyth_401s(monkeypatch):
    monkeypatch.setattr(I.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(ms, "coinpaprika_markets", _paprika())
    assert await I._majors() == (74000.0, 2100.0, 86.0)


@pytest.mark.asyncio
async def test_full_market_uses_paprika_when_pyth_has_no_prices(monkeypatch):
    async def feeds():
        return {"id1": {"symbol": "Crypto.BTC/USD", "base": "?", "asset_type": "Crypto"}}

    async def batch(_ids):
        return {}

    async def cg(*_a, **_k):
        raise AssertionError("CoinGecko must not be called when Paprika answered")

    monkeypatch.setattr(I, "_get_all_crypto_feeds", feeds)
    monkeypatch.setattr(I, "_fetch_prices_batch", batch)
    monkeypatch.setattr(ms, "coinpaprika_markets", _paprika())
    monkeypatch.setattr(ms, "coingecko_markets", cg)
    out = await I.full_market(limit=10, agent={})
    snap = out["market_snapshot"]
    assert snap["price_source"] == "coinpaprika" and snap["tokens_tracked"] == 3
    assert [t["symbol"] for t in out["tokens"]][:2] == ["BTC", "ETH"]


def _rows(green: int, avg: float, n: int = 100):
    """n tokens, exactly `green` of them up, mean 24h change == avg."""
    up, dn = [1.0] * green, [-1.0] * (n - green)
    rows = up + dn
    shift = avg - sum(rows) / n
    # keep signs: scale the positive / negative halves instead of shifting
    if shift:
        if green:
            up = [1.0 + shift * n / green] * green if shift > 0 else up
        if n - green and shift < 0:
            dn = [-1.0 + shift * n / (n - green)] * (n - green)
        rows = up + dn
    return [{"symbol": f"T{i}", "change_24h": c} for i, c in enumerate(rows)]


@pytest.mark.asyncio
@pytest.mark.parametrize("green,avg,fg,label,overall,trend", [
    (58, 0.26, 32, "Fear", "neutral", "neutral"),       # noise is not "bullish" (live case)
    (70, 2.0, 32, "Fear", "mixed", "bullish"),          # up, but positioning fearful
    (30, -2.0, 20, "Extreme Fear", "bearish", "bearish"),
    (65, 1.5, 75, "Greed", "bullish", "bullish"),
    (50, 0.0, 50, "Neutral", "neutral", "neutral"),
])
async def test_breadth_sentiment_classification(monkeypatch, green, avg, fg, label, overall, trend):
    rows = _rows(green, avg)

    async def mk(*_a, **_k):
        return rows

    async def fng():
        return {"value": fg, "classification": label}

    async def gl(*_a, **_k):
        return {"btc_dominance": 57.0}

    monkeypatch.setattr(ms, "coinpaprika_markets", mk)
    monkeypatch.setattr(ms, "coinpaprika_global", gl)
    monkeypatch.setattr(ms, "alt_fear_greed", fng)
    ms._cache.pop("breadth", None)
    out = await ms.market_breadth()
    ms._cache.pop("breadth", None)
    assert out["gainers_pct"] == float(green)
    assert out["price_trend"] == trend and out["overall"] == overall
