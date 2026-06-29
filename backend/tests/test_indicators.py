"""Built-in indicator math + OHLC fetch (network mocked)."""
import pytest

from backend import indicators as ind
from backend import market_sources as ms


def _candles(closes, vols=None):
    vols = vols or [1.0] * len(closes)
    return [{"time": i, "open": c, "high": c, "low": c, "close": c, "volume": vols[i]}
            for i, c in enumerate(closes)]


def test_sma_known_values():
    out = ind.sma(_candles([1, 2, 3, 4, 5]), length=3)
    vals = [p["value"] for p in out]
    assert vals[:2] == [None, None]
    assert vals[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert vals[3] == pytest.approx(3.0)
    assert vals[4] == pytest.approx(4.0)


def test_ema_seeds_with_sma_then_smooths():
    out = ind.ema(_candles([1, 2, 3, 4, 5, 6]), length=3)
    vals = [p["value"] for p in out]
    assert vals[:2] == [None, None]
    assert vals[2] == pytest.approx(2.0)              # seed = SMA(1,2,3)
    assert vals[3] == pytest.approx(4 * 0.5 + 2 * 0.5)  # k=2/(3+1)=0.5 → 3.0
    assert vals[4] == pytest.approx(5 * 0.5 + 3.0 * 0.5)  # 4.0


def test_rsi_all_gains_is_100():
    out = ind.rsi(_candles([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]), length=14)
    last = out[-1]["value"]
    assert last == pytest.approx(100.0)  # no losses → RSI 100


def test_macd_shape_and_histogram():
    closes = [float(x) for x in range(1, 60)]
    out = ind.macd(_candles(closes))
    last = out[-1]
    assert last["macd"] is not None and last["signal"] is not None
    assert last["histogram"] == pytest.approx(last["macd"] - last["signal"], abs=1e-6)


def test_bollinger_bands_ordering():
    out = ind.bollinger(_candles([1, 3, 2, 5, 4, 6, 5, 7, 6, 8, 7, 9, 8, 10, 9, 11, 10, 12, 11, 13, 12]), length=20)
    band = out[-1]
    assert band["lower"] < band["middle"] < band["upper"]


def test_vwap_weights_by_volume():
    out = ind.vwap(_candles([10, 20], vols=[1, 3]))
    # typical price == close here; cumulative: (10*1 + 20*3)/(1+3) = 17.5
    assert out[-1]["value"] == pytest.approx(17.5)


def test_compute_default_pack():
    res = ind.compute(_candles([float(x) for x in range(1, 80)]))
    assert {"sma_20", "ema_50", "rsi_14", "macd", "bollinger_20"} <= set(res)


@pytest.mark.asyncio
async def test_ohlc_parses_binance_klines(monkeypatch):
    async def fake_get(url, timeout=8):
        if "binance" in url:
            return [[1700000000000, "100", "110", "90", "105", "12.5", 1700003599999]]
        return None
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    candles = await ms.ohlc("BTC", "1h", 50)
    assert candles == [{"time": 1700000000, "open": 100.0, "high": 110.0,
                        "low": 90.0, "close": 105.0, "volume": 12.5}]


@pytest.mark.asyncio
async def test_ohlc_falls_back_to_coingecko(monkeypatch):
    async def fake_get(url, timeout=8):
        if "binance" in url:
            return None  # exchange down
        if "coingecko" in url and "ohlc" in url:
            return [[1700000000000, 100, 110, 90, 105]]
        return None
    monkeypatch.setattr(ms, "_get_json", fake_get)
    ms._cache.clear()
    candles = await ms.ohlc("RNDR", "1d", 50)
    assert len(candles) == 1
    assert candles[0]["close"] == 105.0
    assert candles[0]["volume"] == 0.0  # CoinGecko OHLC has no volume
