"""
Position sizing models: Kelly Criterion, Fixed Fractional, ATR-based.
All functions return a fraction of capital (0.0–1.0).
"""

"""
    kelly_fraction(win_rate, avg_win, avg_loss; max_fraction=0.25) → Float64

Full Kelly: f* = (p*b - q) / b, where b = avg_win/avg_loss.
Capped at `max_fraction` to avoid over-betting.
"""
function kelly_fraction(
    win_rate::Float64,
    avg_win::Float64,
    avg_loss::Float64;
    max_fraction::Float64=0.25
)::Float64
    avg_loss == 0.0 && return 0.0
    b = avg_win / abs(avg_loss)
    q = 1.0 - win_rate
    f = (win_rate * b - q) / b
    clamp(f, 0.0, max_fraction)
end

"""
    fixed_fractional(risk_pct=0.01) → Float64

Risk a fixed percentage of capital per trade (default 1%).
Simple, robust, widely used baseline.
"""
function fixed_fractional(risk_pct::Float64=0.01)::Float64
    clamp(risk_pct, 0.0, 1.0)
end

"""
    atr_based_size(capital, entry_price, atr, sl_mult=2.0; risk_pct=0.01) → Float64

Size position so that 1 ATR move against the trade risks exactly `risk_pct` of capital.
Returns fraction of capital to deploy (accounting for leverage-free spot).
"""
function atr_based_size(
    capital::Float64,
    entry_price::Float64,
    atr::Float64,
    sl_mult::Float64=2.0;
    risk_pct::Float64=0.01
)::Float64
    (entry_price == 0.0 || atr == 0.0) && return 0.0
    risk_per_unit = atr * sl_mult
    risk_dollars  = capital * risk_pct
    units = risk_dollars / risk_per_unit
    position_value = units * entry_price
    clamp(position_value / capital, 0.0, 1.0)
end

"""
    half_kelly(win_rate, avg_win, avg_loss; max_fraction=0.25) → Float64

Conservative half-Kelly. Reduces variance while preserving most of the edge.
"""
function half_kelly(
    win_rate::Float64,
    avg_win::Float64,
    avg_loss::Float64;
    max_fraction::Float64=0.25
)::Float64
    kelly_fraction(win_rate, avg_win, avg_loss; max_fraction) * 0.5
end
