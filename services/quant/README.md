# services/quant -- TradingOSQuant (vendored from kanban, P4-14)

`julia/TradingOSQuant/` is copied from
[cryptonomicsed-byte/kanban](https://github.com/cryptonomicsed-byte/kanban)
`services/julia/TradingOSQuant` at `06c0fba`. The kanban copy stays where it is.

One local change, in `src/server.jl`: the bind address is read from
`JULIA_HOST` (default `127.0.0.1`) instead of the hard-coded `0.0.0.0`, so the
service is not exposed off-box by default. Everything else is verbatim.

| | |
|---|---|
| Service | `ares-quant-julia` (`ares-quant-julia.service`, shipped only, **not installed**) |
| Class | INTEL -- analytics only, places no orders |
| Flag | `QUANT_JULIA_ENABLED`, default off (unit exits 0; Vantage client returns `enabled:false`) |
| Endpoints | `GET /health`, `POST /backtest` on `JULIA_HOST:JULIA_PORT` (127.0.0.1:8083) |
| Vantage side | `backend/quant_client.py`; `GET /api/intel/quant/status`, `GET /api/intel/quant/backtest/{symbol}` |

The Vantage client is fail-open (service down -> `ok:false`, never a 5xx) and
read-only: it does not post to the signal pool. Candles come from the existing
free `market_sources.ohlc` (Kraken -> Binance -> CoinGecko).

Run the Julia tests:

    cd julia/TradingOSQuant && julia --project=. -e 'using Pkg; Pkg.instantiate(); Pkg.test()'

Registration in ares-control `DAEMON_REGISTRY` (class INTEL) is a separate ares-control change.
