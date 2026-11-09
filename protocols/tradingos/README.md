# Vendored: TradingOS signal schema

`signal.proto` is copied verbatim from
[cryptonomicsed-byte/TradingOS](https://github.com/cryptonomicsed-byte/TradingOS)
`protos/signal.proto` at commit `16b3275` (handoff P1-02). TradingOS merges into
Vantage (handoff P4-15); this copy is what `POST /api/pipeline/validate-signal`
and the pipeline registry's TradingOS node point at, so neither depends on a
TradingOS checkout being on disk. `/opt/ares/TradingOS/protos/signal.proto`
remains a fallback.

`signal_record/1` (backend/signal_record.py) is built from `SignalGenome`.
Do not edit the .proto here; re-vendor it from TradingOS instead.
