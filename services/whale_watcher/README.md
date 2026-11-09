# whale_watcher (vendored from kanban, P4-14)

`whale_watcher.py` is copied verbatim from
[cryptonomicsed-byte/kanban](https://github.com/cryptonomicsed-byte/kanban)
`services/python/onchain/whale_watcher.py` at `06c0fba`. Do not edit it here;
re-vendor instead. The kanban copy stays where it is.

- `run.py` -- the Vantage runner. Flag `WHALE_WATCHER_ENABLED` (default off;
  off = exits 0 without importing web3). Requires `ETH_WSS_URL` (free or
  self-hosted node); the module's keyed provider default is never reached.
  Appends alerts as JSON lines to `WHALE_WATCHER_OUT`
  (default `$VANTAGE_DATA_DIR/whale_watcher.jsonl`).
- `ares-whale-watcher.service.example` -- systemd unit, shipped only, not installed.
  Class INTEL.
- Read side: `GET /api/intel/quant/whales` (backend/quant_client.py), read-only.
  Nothing posts to the signal pool.

Runtime deps (not in Vantage's requirements; install only if enabled): `web3`, `structlog`, `httpx`.
Note: the ETH price comes from CoinGecko's free endpoint with a hard-coded fallback;
`stream_pending` (mempool) is opt-in via `WHALE_WATCHER_PENDING=1` because most
free nodes do not serve `pendingTransactions`.
