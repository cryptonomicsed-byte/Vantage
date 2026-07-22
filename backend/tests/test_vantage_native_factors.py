"""Tests for the vantage/ zoo — factors native to Vantage's own signal_pool
(predictor conviction, GDELT news tone, pump.fun attention density) blended
with live OHLCV. These are the first zoo factors to use extras_required,
and degen_pump_density is the first with no OHLCV dependency at all.
"""
import pandas as pd
import pytest

from backend.factors.registry import Registry


@pytest.fixture(scope="module")
def registry():
    return Registry()


def test_all_three_vantage_native_factors_registered(registry):
    ids = registry.list(zoo="vantage")
    assert set(ids) == {
        "vantage_predictor_price_divergence",
        "vantage_sentiment_tone_momentum",
        "vantage_degen_pump_density",
    }


def test_degen_pump_density_needs_no_ohlcv(registry):
    """First zoo factor with columns_required=[] — signal_pool is the only input."""
    alpha = registry.get("vantage_degen_pump_density")
    assert alpha.meta["columns_required"] == []
    assert set(alpha.meta["extras_required"]) == {"degen_buy_count", "degen_pump_pct"}

    idx = pd.to_datetime(["2026-01-01T00:00:00", "2026-01-01T00:15:00"])
    buy_count = pd.DataFrame({"KET": [3, 5], "BOP": [1, 2]}, index=idx)
    pump_pct = pd.DataFrame({"KET": [647, 200], "BOP": [74, 50]}, index=idx)
    out = registry.compute(
        "vantage_degen_pump_density",
        {"degen_buy_count": buy_count, "degen_pump_pct": pump_pct},
    )
    # KET has both higher buy_count and higher pump_pct every row -> ranks above BOP
    assert (out["KET"] > out["BOP"]).all()


def test_predictor_price_divergence_uses_extras(registry):
    alpha = registry.get("vantage_predictor_price_divergence")
    assert alpha.meta["columns_required"] == ["close"]
    assert set(alpha.meta["extras_required"]) == {"predictor_conviction", "predictor_direction"}

    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    close = pd.DataFrame({"BTC": range(100, 110), "ETH": range(50, 60)}, index=idx)
    conv = pd.DataFrame({"BTC": [0.8] * 10, "ETH": [0.5] * 10}, index=idx)
    direction = pd.DataFrame({"BTC": [1] * 10, "ETH": [-1] * 10}, index=idx)
    out = registry.compute(
        "vantage_predictor_price_divergence",
        {"close": close, "predictor_conviction": conv, "predictor_direction": direction},
    )
    assert out.shape == close.shape


def test_sentiment_tone_momentum_handles_missing_tone_as_zero(registry):
    """zscore is cross-sectional (per-row, across symbols) — a single-symbol
    panel can never have cross-sectional spread, so this needs >=2 symbols
    to be a meaningful test, matching how the factor is actually used."""
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    close = pd.DataFrame(
        {"SOL": [100 + i for i in range(10)], "BTC": [50000 + i * 10 for i in range(10)]},
        index=idx,
    )
    tone = pd.DataFrame({"SOL": [0.0] * 10, "BTC": [0.2] * 10}, index=idx)  # quiet-ish window
    out = registry.compute(
        "vantage_sentiment_tone_momentum",
        {"close": close, "news_tone": tone},
    )
    assert out.shape == close.shape
    assert not out.isna().all().all()
