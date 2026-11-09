"""
Risk metrics: Sharpe, Sortino, Calmar, Max Drawdown, Profit Factor.
All calculations operate on equity curves or trade PnL vectors.
"""

using Statistics

"""
    sharpe_ratio(returns; annualize_factor=sqrt(252*24)) → Float64

Annualised Sharpe assuming 1-bar = 1 hour (252 trading days × 24 hours).
"""
function sharpe_ratio(
    returns::Vector{Float64};
    annualize_factor::Float64=sqrt(252.0 * 24.0)
)::Float64
    isempty(returns) && return 0.0
    σ = std(returns)
    μ = mean(returns)
    if σ < 1e-10
        return μ > 0.0 ? Inf : (μ < 0.0 ? -Inf : 0.0)
    end
    μ / σ * annualize_factor
end

"""
    sortino_ratio(returns; annualize_factor=sqrt(252*24)) → Float64

Penalises only downside volatility (semi-deviation of negative returns).
"""
function sortino_ratio(
    returns::Vector{Float64};
    annualize_factor::Float64=sqrt(252.0 * 24.0)
)::Float64
    isempty(returns) && return 0.0
    neg = filter(r -> r < 0.0, returns)
    σ_down = isempty(neg) ? 1e-10 : std(neg)
    mean(returns) / σ_down * annualize_factor
end

"""
    max_drawdown(equity) → Float64

Peak-to-trough drawdown as a fraction (0–1). Returns the worst seen.
"""
function max_drawdown(equity::Vector{Float64})::Float64
    peak = -Inf
    max_dd = 0.0
    for eq in filter(!isnan, equity)
        peak = max(peak, eq)
        dd = peak > 0.0 ? (peak - eq) / peak : 0.0
        max_dd = max(max_dd, dd)
    end
    max_dd
end

"""
    profit_factor(trades_pnl) → Float64

Gross profit / gross loss. Inf if no losing trades.
"""
function profit_factor(trades_pnl::Vector{Float64})::Float64
    isempty(trades_pnl) && return 0.0
    gross_profit = sum(filter(p -> p > 0, trades_pnl); init=0.0)
    gross_loss   = abs(sum(filter(p -> p <= 0, trades_pnl); init=0.0))
    gross_loss == 0.0 ? Inf : gross_profit / gross_loss
end

"""
    calmar_ratio(net_profit_pct, max_dd_pct) → Float64

Annual return / max drawdown. Higher = better risk-adjusted performance.
"""
function calmar_ratio(net_profit_pct::Float64, max_dd_pct::Float64)::Float64
    max_dd_pct == 0.0 ? 0.0 : (net_profit_pct / 100.0) / (max_dd_pct / 100.0)
end

"""
    win_stats(trades_pnl) → NamedTuple

Returns win_rate, winning_count, losing_count, avg_win, avg_loss.
"""
function win_stats(trades_pnl::Vector{Float64})
    isempty(trades_pnl) && return (
        win_rate=0.0, winning=0, losing=0, avg_win=0.0, avg_loss=0.0
    )
    winning = filter(p -> p > 0, trades_pnl)
    losing  = filter(p -> p <= 0, trades_pnl)
    (
        win_rate = length(winning) / length(trades_pnl),
        winning  = length(winning),
        losing   = length(losing),
        avg_win  = isempty(winning) ? 0.0 : mean(winning),
        avg_loss = isempty(losing)  ? 0.0 : mean(losing),
    )
end

"""
    monte_carlo_drawdown(trades_pnl, n_sims=10_000, seed=42) → Vector{Float64}

Bootstrap shuffle of trade sequence to estimate drawdown distribution.
Returns vector of max drawdown values (one per simulation).
"""
function monte_carlo_drawdown(
    trades_pnl::Vector{Float64},
    n_sims::Int=10_000;
    seed::Int=42
)::Vector{Float64}
    isempty(trades_pnl) && return Float64[]
    rng = MersenneTwister(seed)
    results = Vector{Float64}(undef, n_sims)
    n = length(trades_pnl)
    equity_base = 10_000.0

    for s in 1:n_sims
        perm = randperm(rng, n)
        eq = fill(equity_base, n + 1)
        for (k, idx) in enumerate(perm)
            eq[k+1] = eq[k] * (1.0 + trades_pnl[idx])
        end
        results[s] = max_drawdown(eq)
    end
    sort!(results)
    results
end
