"""
Vectorized Moving Average indicators.
All functions operate on AbstractVector{Float64} and return Vector{Float64}.
NaN is used for the warm-up period (no lookahead bias).
"""

"""
    calculate_wma(series, period) → Vector{Float64}

Weighted Moving Average. Weights increase linearly from 1 to `period`.
"""
function calculate_wma(series::AbstractVector{<:Real}, period::Int)::Vector{Float64}
    n = length(series)
    result = fill(NaN, n)
    weights = collect(1.0:period)
    weight_sum = sum(weights)

    for i in period:n
        window = series[(i - period + 1):i]
        result[i] = dot(window, weights) / weight_sum
    end
    result
end

"""
    calculate_hma(series, period) → Vector{Float64}

Hull Moving Average — reduces lag while maintaining smoothness.
HMA(n) = WMA(2×WMA(n/2) − WMA(n), √n)
"""
function calculate_hma(series::AbstractVector{<:Real}, period::Int)::Vector{Float64}
    half_period = max(1, period ÷ 2)
    sqrt_period = max(1, round(Int, sqrt(period)))

    wma_half = calculate_wma(series, half_period)
    wma_full = calculate_wma(series, period)

    # Raw HMA series: 2×WMA(n/2) − WMA(n)
    raw_hma = 2.0 .* wma_half .- wma_full

    # Final smoothing pass
    calculate_wma(raw_hma, sqrt_period)
end

"""
    calculate_hma_macd(df; fast=12, slow=26, signal=9) → (macd, signal_line)

Custom HMA-MACD: uses HMA instead of EMA to reduce lag.
Returns (macd_line, signal_line) — both Vector{Float64}.
"""
function calculate_hma_macd(
    close::AbstractVector{<:Real};
    fast::Int=12, slow::Int=26, signal::Int=9
)::Tuple{Vector{Float64}, Vector{Float64}}
    hma_fast = calculate_hma(close, fast)
    hma_slow = calculate_hma(close, slow)
    macd_line = hma_fast .- hma_slow
    signal_line = calculate_hma(macd_line, signal)
    (macd_line, signal_line)
end
