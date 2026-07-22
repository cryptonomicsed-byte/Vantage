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


def test_degen_pump_density_survives_degenerate_buy_count(registry):
    """Real production bug: ogun_degen posts one row per token per scan
    cycle, so buy_count is often identical across every tracked token in a
    bucket (no cross-sectional variance) -> zscore(buy_count) is NaN by
    base.py's own "never silent zero" contract. Adding that NaN to a
    perfectly valid zscore(pump_pct) used to poison the whole factor via
    pandas NaN-propagation. Verifies the fillna(0) guard actually holds."""
    idx = pd.to_datetime(["2026-01-01T00:00:00"])
    buy_count = pd.DataFrame({"A": [12], "B": [12], "C": [12]}, index=idx)  # identical
    pump_pct = pd.DataFrame({"A": [10261], "B": [118], "C": [308]}, index=idx)  # varies
    out = registry.compute(
        "vantage_degen_pump_density",
        {"degen_buy_count": buy_count, "degen_pump_pct": pump_pct},
    )
    assert not out.isna().any().any()
    assert out.loc[idx[0], "A"] > out.loc[idx[0], "B"]  # A has by far the biggest pump


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


def test_extras_align_intraday_signal_onto_daily_close_same_day(registry):
    """Real production bug: signal_pool buckets land at whatever time-of-day
    the signal arrived (e.g. today 20:45), while daily OHLCV bars are
    midnight-anchored (today 00:00) — earlier in the same calendar day. A
    naive reindex(close.index, method="ffill") requires source <= target,
    so today's intraday signal could never satisfy today's daily bar and
    every row went all-NaN in production against real live data. Both
    factors now use signal_panel.align_to_price_index, which collapses
    same-day intraday buckets down to the calendar day first."""
    close_idx = pd.date_range("2026-07-13", "2026-07-22", freq="D")
    close = pd.DataFrame(
        {"BTC": range(60000, 60010), "ETH": range(3000, 3010)}, index=close_idx
    )
    # predictor signals only exist *today*, timestamped in the afternoon —
    # strictly later in the day than the daily bar's own midnight timestamp.
    intraday_idx = pd.to_datetime([
        "2026-07-22 20:45:00", "2026-07-22 21:00:00", "2026-07-22 21:15:00",
    ])
    conv = pd.DataFrame({"BTC": [0.8, 0.9, 0.85], "ETH": [0.5, 0.4, 0.45]}, index=intraday_idx)
    direction = pd.DataFrame({"BTC": [1, 1, 1], "ETH": [-1, -1, -1]}, index=intraday_idx)

    out = registry.compute(
        "vantage_predictor_price_divergence",
        {"close": close, "predictor_conviction": conv, "predictor_direction": direction},
    )
    # today's row must actually pick up today's (later-timestamped) signal
    assert not out.loc[close_idx[-1]].isna().all()
