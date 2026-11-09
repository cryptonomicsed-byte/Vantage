"""
Volatility indicators: ATR, Bollinger Bands, ADX.
"""

using Statistics

"""
    calculate_atr(high, low, close, period=14) → Vector{Float64}

Average True Range. Measures volatility for stop-loss placement.
"""
function calculate_atr(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real},
    close::AbstractVector{<:Real},
    period::Int=14
)::Vector{Float64}
    n = length(close)
    tr = fill(NaN, n)

    for i in 2:n
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)
    end

    # Simple moving average of TR (Wilder's smoothing for strict ATR)
    atr = fill(NaN, n)
    if n >= period + 1
        atr[period + 1] = mean(filter(!isnan, tr[2:(period + 1)]))
        for i in (period + 2):n
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        end
    end
    atr
end

"""
    calculate_bollinger_bands(close; period=20, std_dev=2.0) → (upper, middle, lower, bandwidth, pct_b)

Bollinger Bands. bandwidth = squeeze indicator. %B = position within bands (0=lower, 1=upper).
"""
function calculate_bollinger_bands(
    close::AbstractVector{<:Real};
    period::Int=20,
    std_dev::Float64=2.0
)::Tuple{Vector{Float64}, Vector{Float64}, Vector{Float64}, Vector{Float64}, Vector{Float64}}
    n = length(close)
    middle = fill(NaN, n)
    upper = fill(NaN, n)
    lower = fill(NaN, n)
    bandwidth = fill(NaN, n)
    pct_b = fill(NaN, n)

    for i in period:n
        window = close[(i - period + 1):i]
        m = mean(window)
        s = std(window)
        u = m + std_dev * s
        l = m - std_dev * s

        middle[i] = m
        upper[i] = u
        lower[i] = l
        bandwidth[i] = (u - l) / m * 100.0
        range_val = u - l
        pct_b[i] = range_val == 0.0 ? 0.5 : (close[i] - l) / range_val
    end

    (upper, middle, lower, bandwidth, pct_b)
end

"""
    calculate_adx(high, low, close, period=14) → (adx, plus_di, minus_di)

Average Directional Index. ADX > 25 = strong trend. ADX < 20 = ranging.
"""
function calculate_adx(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real},
    close::AbstractVector{<:Real},
    period::Int=14
)::Tuple{Vector{Float64}, Vector{Float64}, Vector{Float64}}
    n = length(close)
    adx = fill(NaN, n)
    plus_di = fill(NaN, n)
    minus_di = fill(NaN, n)

    plus_dm = fill(0.0, n)
    minus_dm = fill(0.0, n)
    tr_vals = fill(NaN, n)

    for i in 2:n
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        plus_dm[i] = (up_move > down_move && up_move > 0.0) ? up_move : 0.0
        minus_dm[i] = (down_move > up_move && down_move > 0.0) ? down_move : 0.0

        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr_vals[i] = max(hl, hc, lc)
    end

    if n < period + 2
        return (adx, plus_di, minus_di)
    end

    # Wilder smoothing
    atr_smooth = mean(filter(!isnan, tr_vals[2:(period + 1)]))
    pdm_smooth = sum(plus_dm[2:(period + 1)])
    mdm_smooth = sum(minus_dm[2:(period + 1)])

    for i in (period + 1):n
        atr_smooth = atr_smooth - atr_smooth / period + tr_vals[i]
        pdm_smooth = pdm_smooth - pdm_smooth / period + plus_dm[i]
        mdm_smooth = mdm_smooth - mdm_smooth / period + minus_dm[i]

        pdi = atr_smooth == 0.0 ? 0.0 : 100.0 * pdm_smooth / atr_smooth
        mdi = atr_smooth == 0.0 ? 0.0 : 100.0 * mdm_smooth / atr_smooth

        plus_di[i] = pdi
        minus_di[i] = mdi

        dx = (pdi + mdi) == 0.0 ? 0.0 : 100.0 * abs(pdi - mdi) / (pdi + mdi)

        # ADX is smoothed DX
        if !isnan(adx[i - 1])
            adx[i] = (adx[i - 1] * (period - 1) + dx) / period
        else
            adx[i] = dx
        end
    end

    (adx, plus_di, minus_di)
end
