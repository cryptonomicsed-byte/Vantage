"""
TradingOSQuant — Julia quantitative engine for Trading OS.
All indicators are non-repainting and vectorized (no lookahead bias).
"""
module TradingOSQuant

using Statistics
using LinearAlgebra
using Random

# ── Indicators ────────────────────────────────────────────────────────────────
include("indicators/moving_averages.jl")
include("indicators/ichimoku.jl")
include("indicators/oscillators.jl")
include("indicators/volume.jl")
include("indicators/volatility.jl")
include("indicators/fibonacci.jl")

# ── Strategy & Backtest ───────────────────────────────────────────────────────
include("strategy/backtest.jl")
include("strategy/confluence.jl")

# ── Risk ──────────────────────────────────────────────────────────────────────
include("risk/metrics.jl")
include("risk/position_sizing.jl")

export
    # Moving averages
    calculate_wma, calculate_hma, calculate_hma_macd,
    # Ichimoku
    calculate_ichimoku,
    # Oscillators
    calculate_rsi, calculate_stochastic, calculate_mfi,
    # Volume
    calculate_obv, calculate_volume_ratio, calculate_rolling_vwap,
    # Volatility
    calculate_atr, calculate_bollinger_bands, calculate_adx,
    # Fibonacci
    calculate_fibonacci_levels, golden_pocket_filter,
    # Strategy
    BacktestParams, BacktestResult, run_backtest,
    generate_confluence_signals,
    # Risk metrics
    sharpe_ratio, sortino_ratio, max_drawdown, profit_factor,
    calmar_ratio, win_stats, monte_carlo_drawdown,
    # Position sizing
    kelly_fraction, fixed_fractional, atr_based_size, half_kelly

end # module
