"""
Volume-based indicators: OBV, Volume Ratio, Rolling VWAP.
"""

using Statistics

"""
    calculate_obv(close, volume) → Vector{Float64}

On-Balance Volume. Cumulative volume flowing in direction of price.
OBV rising with price = healthy trend. OBV diverging = warning.
"""
function calculate_obv(
    close::AbstractVector{<:Real},
    volume::AbstractVector{<:Real}
)::Vector{Float64}
    n = length(close)
    obv = fill(0.0, n)
    for i in 2:n
        direction = close[i] > close[i-1] ? 1.0 :
                    close[i] < close[i-1] ? -1.0 : 0.0
        obv[i] = obv[i-1] + direction * volume[i]
    end
    obv
end

"""
    calculate_volume_ratio(volume, period=20) → Vector{Float64}

Ratio of current volume to its rolling mean. > 1.5 = elevated activity.
"""
function calculate_volume_ratio(
    volume::AbstractVector{<:Real},
    period::Int=20
)::Vector{Float64}
    n = length(volume)
    result = fill(NaN, n)
    for i in period:n
        avg = mean(volume[(i - period + 1):i])
        result[i] = avg == 0.0 ? 1.0 : volume[i] / avg
    end
    result
end

"""
    calculate_rolling_vwap(high, low, close, volume, period=50) → Vector{Float64}

Rolling VWAP over `period` bars (no daily reset).
Typical price = (high + low + close) / 3.
"""
function calculate_rolling_vwap(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real},
    close::AbstractVector{<:Real},
    volume::AbstractVector{<:Real},
    period::Int=50
)::Vector{Float64}
    n = length(close)
    result = fill(NaN, n)
    typical = (high .+ low .+ close) ./ 3.0
    pv = typical .* volume

    for i in period:n
        total_vol = sum(volume[(i - period + 1):i])
        result[i] = total_vol == 0.0 ? typical[i] :
                    sum(pv[(i - period + 1):i]) / total_vol
    end
    result
end
