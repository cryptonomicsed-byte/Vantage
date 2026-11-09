using Test
using Random
using TradingOSQuant

# ── Fixtures ─────────────────────────────────────────────────────────────────

function synthetic_ohlcv(n::Int=300; seed::Int=1)
    rng = MersenneTwister(seed)
    close = cumsum(randn(rng, n)) .+ 100.0
    close = max.(close, 1.0)
    high  = close .+ abs.(randn(rng, n)) .* 0.5
    low   = close .- abs.(randn(rng, n)) .* 0.5
    open  = vcat([close[1]], close[1:end-1])
    vol   = abs.(randn(rng, n)) .* 1_000.0 .+ 5_000.0
    (open=open, high=high, low=low, close=close, volume=vol)
end

# ── Moving Averages ───────────────────────────────────────────────────────────

@testset "WMA" begin
    d = synthetic_ohlcv()
    wma = calculate_wma(d.close, 14)
    @test length(wma) == length(d.close)
    @test all(isnan, wma[1:13])
    @test !isnan(wma[14])
end

@testset "HMA" begin
    d = synthetic_ohlcv()
    hma = calculate_hma(d.close, 14)
    @test length(hma) == length(d.close)
    @test !all(isnan, hma)
end

@testset "HMA-MACD" begin
    d = synthetic_ohlcv()
    macd, sig = calculate_hma_macd(d.close)
    @test length(macd) == length(d.close)
    @test length(sig) == length(d.close)
end

# ── Ichimoku ─────────────────────────────────────────────────────────────────

@testset "Ichimoku" begin
    d = synthetic_ohlcv()
    conv, base, l1, l2, top, bot = calculate_ichimoku(d.high, d.low)
    @test length(conv) == length(d.close)
    # Cloud top/bot should always have top >= bot where both valid
    for i in 1:length(top)
        if !isnan(top[i]) && !isnan(bot[i])
            @test top[i] >= bot[i]
        end
    end
end

# ── Oscillators ───────────────────────────────────────────────────────────────

@testset "RSI bounds" begin
    d = synthetic_ohlcv()
    rsi = calculate_rsi(d.close)
    valid = filter(!isnan, rsi)
    @test all(0.0 .<= valid .<= 100.0)
end

@testset "Stochastic bounds" begin
    d = synthetic_ohlcv()
    k, dd = calculate_stochastic(d.high, d.low, d.close)
    valid_k = filter(!isnan, k)
    @test all(0.0 .<= valid_k .<= 100.0)
end

@testset "MFI bounds" begin
    d = synthetic_ohlcv()
    mfi = calculate_mfi(d.high, d.low, d.close, d.volume)
    valid = filter(!isnan, mfi)
    @test all(0.0 .<= valid .<= 100.0)
end

# ── Volume ────────────────────────────────────────────────────────────────────

@testset "OBV monotone direction" begin
    # Constant-up price → OBV always increases
    n = 50
    close = collect(Float64, 1:n)
    vol = fill(1000.0, n)
    obv = calculate_obv(close, vol)
    @test all(diff(obv) .>= 0)
end

@testset "Rolling VWAP" begin
    d = synthetic_ohlcv()
    vwap = calculate_rolling_vwap(d.high, d.low, d.close, d.volume)
    @test length(vwap) == length(d.close)
    valid = filter(!isnan, vwap)
    @test all(v -> v > 0, valid)
end

# ── Volatility ────────────────────────────────────────────────────────────────

@testset "ATR positive" begin
    d = synthetic_ohlcv()
    atr = calculate_atr(d.high, d.low, d.close)
    valid = filter(!isnan, atr)
    @test all(v -> v >= 0, valid)
end

@testset "Bollinger pct_b range" begin
    d = synthetic_ohlcv()
    _, _, _, _, pct_b = calculate_bollinger_bands(d.close)
    # pct_b can go outside 0–1 on extreme moves; just check it's finite
    valid = filter(!isnan, pct_b)
    @test all(isfinite, valid)
end

@testset "ADX positive" begin
    d = synthetic_ohlcv()
    adx, _, _ = calculate_adx(d.high, d.low, d.close)
    valid = filter(!isnan, adx)
    @test all(v -> v >= 0, valid)
end

# ── Fibonacci ─────────────────────────────────────────────────────────────────

@testset "Fibonacci levels structure" begin
    d = synthetic_ohlcv(500)
    levels, dir = calculate_fibonacci_levels(d.high, d.low)
    @test dir in (-1, 0, 1)
    if !isnothing(levels)
        @test haskey(levels, "fib0618")
        @test haskey(levels, "fib10")
    end
end

# ── Backtest ──────────────────────────────────────────────────────────────────

@testset "Backtest runs without error" begin
    d = synthetic_ohlcv(500)
    params = BacktestParams()
    result = run_backtest(d.open, d.high, d.low, d.close, d.volume, params)
    @test result.total_trades >= 0
    @test 0.0 <= result.win_rate <= 1.0
    @test result.max_drawdown_pct >= 0.0
    @test length(result.equity_curve) == 500
end

# ── Risk Metrics ──────────────────────────────────────────────────────────────

@testset "Sharpe of constant returns" begin
    returns = fill(0.001, 100)
    sr = sharpe_ratio(returns)
    @test sr > 0
end

@testset "Max drawdown of monotone equity" begin
    equity = collect(Float64, 1:100)
    dd = max_drawdown(equity)
    @test dd == 0.0
end

@testset "Profit factor" begin
    pnl = [0.1, -0.05, 0.08, -0.03, 0.12]
    pf = profit_factor(pnl)
    @test pf > 1.0
end

# ── Position Sizing ───────────────────────────────────────────────────────────

@testset "Kelly fraction clamped" begin
    f = kelly_fraction(0.6, 0.1, 0.05)
    @test 0.0 <= f <= 0.25
end

@testset "ATR size positive" begin
    sz = atr_based_size(10_000.0, 100.0, 2.0, 2.0; risk_pct=0.01)
    @test sz > 0.0 && sz <= 1.0
end
