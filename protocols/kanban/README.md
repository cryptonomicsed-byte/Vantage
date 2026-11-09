# Vendored: kanban (TradingOS) protocols, P4-14

Copied verbatim from [cryptonomicsed-byte/kanban](https://github.com/cryptonomicsed-byte/kanban)
at commit `06c0fba`. Reference only -- nothing in Vantage compiles or serves these yet.
Do not edit here; re-vendor from kanban. Nothing in kanban was moved or deleted.

| Here | kanban source |
|---|---|
| `proto/agent.proto`, `proto/market.proto`, `proto/strategy.proto` | `proto/` |
| `mcp/trading_os_mcp/` (mix.exs, test/test_helper.exs) | `services/elixir/trading_os/apps/trading_os_mcp` (the MCP app named in the handoff; at 06c0fba it is a skeleton) |
| `mcp/kanban_tools.ex` | `services/elixir/trading_os/apps/trading_os_web/lib/trading_os_web/mcp/tools/kanban_tools.ex` (the actual kanban MCP tool handlers) |
| `mcp/card.ex` | `services/elixir/trading_os/apps/trading_os_core/lib/trading_os_core/kanban/card.ex` (card state machine) |

`strategy.proto` `BacktestRequest/StrategyParams` describes the same parameters as
`services/quant` `POST /backtest`. `protocols/tradingos/signal.proto` (P1-02) remains
the schema for `signal_record/1`.
