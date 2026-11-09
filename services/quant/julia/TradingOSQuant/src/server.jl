"""
HTTP server exposing the TradingOSQuant backtest engine.
Start with: julia --project=. src/server.jl
"""

using HTTP
using JSON3
using Logging

include("TradingOSQuant.jl")
using .TradingOSQuant: BacktestParams, run_backtest

function handle_health(::HTTP.Request)
    HTTP.Response(200, ["Content-Type" => "application/json"],
        JSON3.write(Dict("status" => "ok", "service" => "julia-quant")))
end

function handle_backtest(req::HTTP.Request)
    try
        body  = JSON3.read(req.body)
        ticker = get(body, :ticker, "UNKNOWN")
        p     = get(body, :params, Dict())

        bp = BacktestParams(
            hma_len          = get(p, :hma_len, 14),
            mom_req          = get(p, :mom_req, 3),
            vol_req          = get(p, :vol_req, 1),
            use_fib          = get(p, :use_fib, false),
            sl_mult          = Float64(get(p, :sl_mult, 2.0)),
            tp_mult          = Float64(get(p, :tp_mult, 3.0)),
            min_adx          = Float64(get(p, :min_adx, 20.0)),
            commission_pct   = Float64(get(p, :commission_pct, 0.001)),
            initial_capital  = Float64(get(p, :initial_capital, 10_000.0))
        )

        open_v   = Vector{Float64}(body[:open])
        high_v   = Vector{Float64}(body[:high])
        low_v    = Vector{Float64}(body[:low])
        close_v  = Vector{Float64}(body[:close])
        volume_v = Vector{Float64}(body[:volume])

        r = run_backtest(open_v, high_v, low_v, close_v, volume_v, bp)

        resp = Dict(
            "ticker"                   => ticker,
            "sharpe_ratio"             => r.sharpe_ratio,
            "sortino_ratio"            => r.sortino_ratio,
            "max_drawdown_pct"         => r.max_drawdown_pct,
            "profit_factor"            => r.profit_factor,
            "win_rate"                 => r.win_rate,
            "total_trades"             => r.total_trades,
            "winning_trades"           => r.winning_trades,
            "losing_trades"            => r.losing_trades,
            "net_profit"               => r.net_profit,
            "net_profit_pct"           => r.net_profit_pct,
            "calmar_ratio"             => r.calmar_ratio,
            "avg_trade_duration_bars"  => r.avg_trade_duration_bars
        )

        HTTP.Response(200, ["Content-Type" => "application/json"], JSON3.write(resp))
    catch e
        @error "backtest failed" exception=e
        HTTP.Response(500, ["Content-Type" => "application/json"],
            JSON3.write(Dict("error" => string(e))))
    end
end

const PORT = parse(Int, get(ENV, "JULIA_PORT", "8083"))
# Vantage P4-14: bind address from JULIA_HOST (default loopback, was 0.0.0.0).
const HOST = get(ENV, "JULIA_HOST", "127.0.0.1")

router = HTTP.Router()
HTTP.register!(router, "GET",  "/health",   handle_health)
HTTP.register!(router, "POST", "/backtest", handle_backtest)

@info "Julia quant server listening" host=HOST port=PORT
HTTP.serve(router, HOST, PORT)
