"""Smoke tests for the alpha factor zoo + backtest validation modules
extracted from HKUDS/Vibe-Trading (MIT). Not exhaustive per-factor
correctness (that lives upstream) — just proves the extraction didn't
break: registry loads, a factor computes on a Vantage-shaped panel, and
statistical validation runs on a real trade series.
"""
import numpy as np
import pandas as pd
import pytest

from backend.factors.registry import Registry


@pytest.fixture(scope="module")
def registry():
    return Registry()


def test_registry_loads_all_factors_with_zero_failures(registry):
    health = registry.health()
    assert health["loaded"] > 400, f"expected 400+ factors, got {health['loaded']}"
    assert health["failed"] == 0, health["errors"]


def test_compute_alpha101_018_on_synthetic_panel(registry):
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    symbols = ["SOL", "BTC", "ETH"]
    close = pd.DataFrame(
        100 + np.cumsum(np.random.randn(60, 3), axis=0), index=dates, columns=symbols
    )
    open_ = close.shift(1).fillna(close.iloc[0])
    panel = {"close": close, "open": open_}

    out = registry.compute("alpha101_018", panel)
    assert out.shape == (60, 3)
    assert not out.iloc[10:].isna().all().all()  # not all-NaN past warmup


def test_monte_carlo_validation_on_real_trade_series():
    from backend.backtest.validation import monte_carlo_test
    from backend.backtest.models import TradeRecord

    trades = [
        TradeRecord(
            symbol="SOL", direction=1 if i % 2 == 0 else -1,
            entry_price=100 + i, exit_price=100 + i + ((-1) ** i) * 2,
            entry_time=pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
            exit_time=pd.Timestamp("2026-01-02") + pd.Timedelta(days=i),
            size=1.0, leverage=1.0, pnl=((-1) ** i) * 2.0, pnl_pct=0.02,
            exit_reason="signal", holding_bars=1, commission=0.1,
            entry_margin=100.0, exit_margin=100.0,
        )
        for i in range(20)
    ]
    result = monte_carlo_test(trades, initial_capital=10000, n_simulations=200, seed=1)
    assert "p_value_sharpe" in result
    assert 0.0 <= result["p_value_sharpe"] <= 1.0
    assert result["n_trades"] == 20


def test_crypto_engine_imports_and_subclasses_base_engine():
    from backend.backtest.engines.crypto import CryptoEngine
    from backend.backtest.engines.base import BaseEngine

    assert issubclass(CryptoEngine, BaseEngine)
