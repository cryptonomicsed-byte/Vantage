"""
Momentum oscillators: RSI, Stochastic, MFI.
"""

"""
    calculate_rsi(close, period=14) → Vector{Float64}

Relative Strength Index using Wilder's smoothing (EMA with α=1/period).
Range: 0–100. < 30 = oversold, > 70 = overbought.
"""
function calculate_rsi(close::AbstractVector{<:Real}, period::Int=14)::Vector{Float64}
    n = length(close)
    result = fill(NaN, n)
    delta = diff(close)

    gain = max.(delta, 0.0)
    loss = max.(-delta, 0.0)

    # Wilder's smoothing (equivalent to EMA with α = 1/period)
    avg_gain = fill(NaN, length(delta))
    avg_loss = fill(NaN, length(delta))

    # First value: simple average
    if length(delta) >= period
        avg_gain[period] = mean(gain[1:period])
        avg_loss[period] = mean(loss[1:period])

        for i in (period + 1):length(delta)
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
        end

        for i in period:length(delta)
            rs = avg_loss[i] == 0.0 ? Inf : avg_gain[i] / avg_loss[i]
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))
        end
    end
    result
end

"""
    calculate_stochastic(high, low, close; k_period=14, d_period=3) → (k, d)

Stochastic Oscillator. %K = position of close within k_period range.
%D = d_period SMA of %K. Range: 0–100.
"""
function calculate_stochastic(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real},
    close::AbstractVector{<:Real};
    k_period::Int=14,
    d_period::Int=3
)::Tuple{Vector{Float64}, Vector{Float64}}
    n = length(close)
    k = fill(NaN, n)

    for i in k_period:n
        highest_high = maximum(high[(i - k_period + 1):i])
        lowest_low = minimum(low[(i - k_period + 1):i])
        range_val = highest_high - lowest_low
        k[i] = range_val == 0.0 ? 50.0 : 100.0 * (close[i] - lowest_low) / range_val
    end

    # %D = simple moving average of %K
    d = fill(NaN, n)
    for i in (k_period + d_period - 1):n
        valid = filter(!isnan, k[(i - d_period + 1):i])
        if length(valid) == d_period
            d[i] = mean(valid)
        end
    end

    (k, d)
end

"""
    calculate_mfi(high, low, close, volume, period=14) → Vector{Float64}

Money Flow Index — volume-weighted RSI. Range: 0–100.
"""
function calculate_mfi(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real},
    close::AbstractVector{<:Real},
    volume::AbstractVector{<:Real},
    period::Int=14
)::Vector{Float64}
    n = length(close)
    result = fill(NaN, n)

    typical_price = (high .+ low .+ close) ./ 3.0
    money_flow = typical_price .* volume

    for i in (period + 1):n
        pos_flow = 0.0
        neg_flow = 0.0
        for j in (i - period + 1):i
            if typical_price[j] > typical_price[j - 1]
                pos_flow += money_flow[j]
            elseif typical_price[j] < typical_price[j - 1]
                neg_flow += money_flow[j]
            end
        end
        mfi_ratio = neg_flow == 0.0 ? 100.0 : pos_flow / neg_flow
        result[i] = 100.0 - (100.0 / (1.0 + mfi_ratio))
    end
    result
end
