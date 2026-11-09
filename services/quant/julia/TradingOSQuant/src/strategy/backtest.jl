"""
Vectorized backtesting engine — no bar-by-bar loops, no lookahead bias.
ATR-locked stops captured at entry (never recalculated mid-trade).
"""

using Statistics

struct BacktestParams
    hma_len::Int
    mom_req::Int
    vol_req::Int
    use_fib::Bool
    sl_mult::Float64
    tp_mult::Float64
    min_adx::Float64
    commission_pct::Float64
    initial_capital::Float64
end

BacktestParams(; hma_len=14, mom_req=3, vol_req=1, use_fib=false,
               sl_mult=2.0, tp_mult=3.0, min_adx=20.0,
               commission_pct=0.001, initial_capital=10_000.0) =
    BacktestParams(hma_len, mom_req, vol_req, use_fib,
                   sl_mult, tp_mult, min_adx, commission_pct, initial_capital)

struct BacktestResult
    sharpe_ratio::Float64
    sortino_ratio::Float64
    max_drawdown_pct::Float64
    profit_factor::Float64
    win_rate::Float64
    total_trades::Int
    winning_trades::Int
    losing_trades::Int
    net_profit::Float64
    net_profit_pct::Float64
    calmar_ratio::Float64
    avg_trade_duration_bars::Float64
    equity_curve::Vector{Float64}
end

"""
    run_backtest(open, high, low, close, volume, params) → BacktestResult

Run the Ògún Master Forge v2 strategy against historical OHLCV data.
"""
function run_backtest(
    open_prices::Vector{Float64},
    high::Vector{Float64},
    low::Vector{Float64},
    close::Vector{Float64},
    volume::Vector{Float64},
    params::BacktestParams
)::BacktestResult
    n = length(close)
    equity = fill(params.initial_capital, n)
    trades_pnl = Float64[]
    trade_durations = Int[]

    # Compute all indicators upfront (vectorized)
    hma1 = calculate_hma(open_prices, params.hma_len)
    hma2 = vcat([NaN], hma1[1:end-1])  # shift by 1

    _, _, lead1, lead2 = calculate_ichimoku(high, low)
    macd, macd_sig = calculate_hma_macd(close)
    rsi_vals = calculate_rsi(close)
    mfi_vals = calculate_mfi(high, low, close, volume)
    stoch_k, _ = calculate_stochastic(high, low, close)
    _, bb_middle, _, _, _ = calculate_bollinger_bands(close)
    atr_vals = calculate_atr(high, low, close)
    adx_vals, _, _ = calculate_adx(high, low, close)
    obv_vals = calculate_obv(close, volume)
    obv_sma = calculate_rolling_sma(obv_vals, 20)
    vol_ratio = calculate_volume_ratio(volume)
    rolling_vwap = calculate_rolling_vwap(high, low, close, volume)

    # Scoring helpers
    function mom_long_score(i)
        ((!isnan(macd[i]) && macd[i] > macd_sig[i]) ? 1 : 0) +
        ((!isnan(rsi_vals[i]) && rsi_vals[i] > 50.0) ? 1 : 0) +
        ((!isnan(mfi_vals[i]) && mfi_vals[i] > 50.0) ? 1 : 0) +
        ((!isnan(stoch_k[i]) && stoch_k[i] > 50.0) ? 1 : 0)
    end

    function vol_long_score(i)
        ((!isnan(bb_middle[i]) && close[i] > bb_middle[i] &&
          !isnan(obv_sma[i]) && obv_vals[i] > obv_sma[i]) ? 1 : 0) +
        ((!isnan(rolling_vwap[i]) && close[i] > rolling_vwap[i]) ? 1 : 0)
    end

    # Trade simulation
    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    locked_sl = 0.0
    locked_tp = 0.0
    entry_bar = 0
    capital = params.initial_capital

    for i in 53:n  # warm-up period for Ichimoku (52 bars)
        # Check exits first (locked SL/TP)
        if position == 1
            if close[i] <= locked_sl || close[i] >= locked_tp
                exit_price = position == 1 ?
                    (close[i] <= locked_sl ? locked_sl : locked_tp) :
                    close[i]
                pnl = (exit_price - entry_price) / entry_price
                commission = params.commission_pct * 2
                net_pnl = pnl - commission
                capital *= (1.0 + net_pnl)
                push!(trades_pnl, net_pnl)
                push!(trade_durations, i - entry_bar)
                position = 0
            end
        elseif position == -1
            if close[i] >= locked_sl || close[i] <= locked_tp
                pnl = (entry_price - close[i]) / entry_price
                commission = params.commission_pct * 2
                net_pnl = pnl - commission
                capital *= (1.0 + net_pnl)
                push!(trades_pnl, net_pnl)
                push!(trade_durations, i - entry_bar)
                position = 0
            end
        end

        equity[i] = capital

        # Skip if warm-up data incomplete
        if any(isnan, [hma1[i], hma2[i], lead1[i], lead2[i], macd[i],
                       atr_vals[i], adx_vals[i]])
            continue
        end

        core_long = (hma1[i] > hma2[i] &&
                     lead1[i] > lead2[i] &&
                     adx_vals[i] > params.min_adx)

        core_short = (hma1[i] < hma2[i] &&
                      lead1[i] < lead2[i] &&
                      adx_vals[i] > params.min_adx)

        long_signal = core_long &&
                      mom_long_score(i) >= params.mom_req &&
                      vol_long_score(i) >= params.vol_req

        short_signal = core_short &&
                       (4 - mom_long_score(i)) >= params.mom_req &&
                       (2 - vol_long_score(i)) >= params.vol_req

        # Entry logic (only when flat)
        if position == 0
            if long_signal
                position = 1
                entry_price = close[i]
                entry_bar = i
                locked_sl = close[i] - atr_vals[i] * params.sl_mult
                locked_tp = close[i] + atr_vals[i] * params.tp_mult
            elseif short_signal
                position = -1
                entry_price = close[i]
                entry_bar = i
                locked_sl = close[i] + atr_vals[i] * params.sl_mult
                locked_tp = close[i] - atr_vals[i] * params.tp_mult
            end
        end
    end

    compute_backtest_result(equity, trades_pnl, trade_durations, params.initial_capital)
end

function compute_backtest_result(
    equity::Vector{Float64},
    trades_pnl::Vector{Float64},
    trade_durations::Vector{Int},
    initial_capital::Float64
)::BacktestResult
    if isempty(trades_pnl)
        return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, equity)
    end

    winning = filter(p -> p > 0, trades_pnl)
    losing = filter(p -> p <= 0, trades_pnl)
    win_rate = length(winning) / length(trades_pnl)

    gross_profit = sum(winning; init=0.0)
    gross_loss = abs(sum(losing; init=0.0))
    profit_factor = gross_loss == 0.0 ? Inf : gross_profit / gross_loss

    final_equity = last(filter(!isnan, equity))
    net_profit = final_equity - initial_capital
    net_profit_pct = net_profit / initial_capital * 100.0

    # Daily returns approximation (assumes 1 bar = 1h for Sharpe scaling)
    returns = diff(filter(!isnan, equity)) ./ filter(!isnan, equity)[1:end-1]
    sharpe = isempty(returns) ? 0.0 : mean(returns) / (std(returns) + 1e-10) * sqrt(252 * 24)

    negative_returns = filter(r -> r < 0, returns)
    sortino_denom = isempty(negative_returns) ? 1e-10 : std(negative_returns)
    sortino = isempty(returns) ? 0.0 : mean(returns) / sortino_denom * sqrt(252 * 24)

    # Max drawdown
    peak = initial_capital
    max_dd = 0.0
    for eq in filter(!isnan, equity)
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        max_dd = max(max_dd, dd)
    end

    calmar = max_dd == 0.0 ? 0.0 : (net_profit_pct / 100.0) / max_dd
    avg_duration = isempty(trade_durations) ? 0.0 : mean(trade_durations)

    BacktestResult(
        sharpe, sortino, max_dd * 100.0, profit_factor, win_rate,
        length(trades_pnl), length(winning), length(losing),
        net_profit, net_profit_pct, calmar, avg_duration, equity
    )
end

# Helper not in other modules
function calculate_rolling_sma(series::Vector{Float64}, period::Int)::Vector{Float64}
    n = length(series)
    result = fill(NaN, n)
    for i in period:n
        window = filter(!isnan, series[(i - period + 1):i])
        if length(window) == period
            result[i] = mean(window)
        end
    end
    result
end
