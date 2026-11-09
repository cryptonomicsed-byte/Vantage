"""
Non-repainting Fibonacci retracement / extension engine.
Pivots are confirmed only after `pivot_order` bars on both sides (no lookahead).
"""

const FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.764, 1.0,
                    1.272, 1.414, 1.618, 2.0, 2.618]

"""
    calculate_fibonacci_levels(high, low; pivot_order=5, max_age_bars=100)
    → (levels::Union{Dict{String,Float64}, Nothing}, direction::Int)

Scans backwards for the most recent confirmed pivot high and pivot low.
A pivot high at index i is confirmed when `pivot_order` bars on each side
are all lower (so the candle at i is the local peak with `pivot_order` lookback).

direction: 1 = uptrend (pivot high is more recent), -1 = downtrend, 0 = unknown.
Returns nothing if no pivots found or both pivots are older than `max_age_bars`.
"""
function calculate_fibonacci_levels(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real};
    pivot_order::Int=5,
    max_age_bars::Int=100
)::Tuple{Union{Dict{String,Float64}, Nothing}, Int}
    n = length(high)
    last_ph::Union{Float64, Nothing} = nothing
    last_ph_idx = -1
    last_pl::Union{Float64, Nothing} = nothing
    last_pl_idx = -1

    # Scan newest-to-oldest; stop once we have both pivots
    for i in (n - pivot_order):-1:(pivot_order + 1)
        if isnothing(last_ph)
            window_h = high[(i - pivot_order):(i + pivot_order)]
            if high[i] == maximum(window_h)
                last_ph = high[i]
                last_ph_idx = i
            end
        end

        if isnothing(last_pl)
            window_l = low[(i - pivot_order):(i + pivot_order)]
            if low[i] == minimum(window_l)
                last_pl = low[i]
                last_pl_idx = i
            end
        end

        !isnothing(last_ph) && !isnothing(last_pl) && break
    end

    (isnothing(last_ph) || isnothing(last_pl)) && return (nothing, 0)

    age = n - max(last_ph_idx, last_pl_idx)
    age > max_age_bars && return (nothing, 0)

    x = last_ph
    z = last_pl
    direction = last_ph_idx > last_pl_idx ? 1 : -1
    range_val  = x - z

    levels = Dict{String, Float64}()
    for r in FIB_RATIOS
        key = "fib" * replace(string(round(r; digits=3)), "." => "")
        levels[key] = z + range_val * r
    end

    (levels, direction)
end

"""
    golden_pocket_filter(price, levels) → Bool

Returns true if `price` is within the Fibonacci Golden Pocket zone (0.618–0.65).
Used as an optional entry filter in the Confluence Matrix.
"""
function golden_pocket_filter(
    price::Real,
    levels::Dict{String, Float64}
)::Bool
    fib618 = get(levels, "fib0618", NaN)
    fib764 = get(levels, "fib0764", NaN)
    isnan(fib618) || isnan(fib764) && return false
    lo = min(fib618, fib764)
    hi = max(fib618, fib764)
    lo <= price <= hi
end
