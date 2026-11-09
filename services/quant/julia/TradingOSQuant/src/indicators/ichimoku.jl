"""
Ichimoku Kinko Hyo — "equilibrium at a glance".
All components are non-repainting; displacement shifts are applied correctly.
"""

"""
    calculate_ichimoku(high, low; conv=9, base=26, lag=52, displacement=26)
    → (conv_line, base_line, lead_line1, lead_line2, cloud_top, cloud_bot)

Returns six series:
- Tenkan-sen (Conversion): midpoint of highest high / lowest low over `conv` bars
- Kijun-sen (Base): same over `base` bars
- Senkou Span A (Lead 1): average of Tenkan/Kijun shifted forward `displacement` bars
- Senkou Span B (Lead 2): midpoint over `lag` bars, shifted forward `displacement` bars
- cloud_top / cloud_bot: higher and lower of Span A and Span B, aligned to current bar
  by shifting BACK `displacement` bars (so they match current price, non-repainting).
"""
function calculate_ichimoku(
    high::AbstractVector{<:Real},
    low::AbstractVector{<:Real};
    conv::Int=9,
    base::Int=26,
    lag::Int=52,
    displacement::Int=26
)::Tuple{
    Vector{Float64}, Vector{Float64},
    Vector{Float64}, Vector{Float64},
    Vector{Float64}, Vector{Float64}
}
    n = length(high)

    function donchian(period::Int)::Vector{Float64}
        result = fill(NaN, n)
        for i in period:n
            result[i] = (maximum(high[(i - period + 1):i]) +
                         minimum(low[(i - period + 1):i])) / 2.0
        end
        result
    end

    conv_line  = donchian(conv)
    base_line  = donchian(base)
    lead_line1 = (conv_line .+ base_line) ./ 2.0
    lead_line2 = donchian(lag)

    # Cloud values: project forward displacement bars (result[i] uses future data)
    # For non-repainting overlay: shift back by displacement when reading signal
    cloud_span_a = fill(NaN, n)
    cloud_span_b = fill(NaN, n)
    for i in 1:(n - displacement)
        cloud_span_a[i + displacement] = lead_line1[i]
        cloud_span_b[i + displacement] = lead_line2[i]
    end

    # Shift back to align with current bar for signal logic
    cloud_top = fill(NaN, n)
    cloud_bot = fill(NaN, n)
    for i in (displacement + 1):n
        a = cloud_span_a[i]
        b = cloud_span_b[i]
        if !isnan(a) && !isnan(b)
            cloud_top[i] = max(a, b)
            cloud_bot[i] = min(a, b)
        end
    end

    (conv_line, base_line, lead_line1, lead_line2, cloud_top, cloud_bot)
end
