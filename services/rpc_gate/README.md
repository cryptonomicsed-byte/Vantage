# services/rpc_gate -- ares-rpc-gate (Go fleet Solana RPC gateway)

One loopback JSON-RPC endpoint for the whole fleet. It rotates the Helius keys
from the shared pool round-robin, using **the same cooldown state file as
`/opt/ares/api_key_pool.py`**. When every key is capped it falls back to the
public RPC behind a token bucket. It caches immutable reads and coalesces
duplicate in-flight requests. Go standard library only; module `ares-rpc-gate`
(Go 1.22).

| | |
|---|---|
| Service | `ares-rpc-gate` (`deploy/ares-rpc-gate.service.example`, shipped only, **not installed**) |
| Listen | `127.0.0.1:8092` (`RPC_GATE_ADDR`). A non-loopback bind is refused unless `RPC_GATE_TOKEN` is set |
| Endpoints | `POST /` and `POST /solana` (JSON-RPC 2.0, single or batch); `GET /health` |
| Class | INTEL / infra. Read-mostly proxy. `sendTransaction` goes upstream exactly once and is never retried |

## Behaviour

* **Key rotation.** Keys are `config[provider].keys` from `RPC_GATE_POOL_CONFIG`. Each request starts at the next key in round-robin order and skips keys that are cooling (`state["helius:<key>"] > now`).
* **Cooldowns.** These match `api_key_pool.report_error`:
  * HTTP 429/400/402/403 with a `QUOTA_PHRASES` phrase in the body (`usage limit exceeded`, `max usage reached`, `compute units`) cools the key for `COOLDOWN_HARD_QUOTA` (3600s).
  * A plain 429 cools it for `COOLDOWN_RATE_LIMIT` (120s).
  * The cooldown is written to the shared state file and the request moves to the next key.
  * One difference from Python: a 400/402/403 *without* a quota phrase does not cool the key, because here it is just a client error.
* **State file format and write protocol.**
  * The file is flat JSON: `{"helius:<key>": <epoch float>}`.
  * Each write is read-modify-write against the file on disk, so entries from Python and other providers are kept.
  * Same steps as Python: `<state>.tmp` + `flock(LOCK_EX)` + `rename` + `chmod 0600`.
  * Config and state are re-read only when mtime/size changes, checked at most every `RPC_GATE_RELOAD_MS`.
* **Public fallback.** When no key is usable the request goes to `SOLANA_PUBLIC_RPC` through a token bucket (`PUBLIC_RPS`, default 4/s). If no token frees up within `PUBLIC_WAIT_MS`, the gateway answers with a local HTTP 429 JSON-RPC error (`-32005`) and does not call the public endpoint.
* **Verbatim responses.** Upstream status and body are passed through unchanged. The exceptions are cache hits and coalesced followers: they get the cached/shared `result` with their own `id`.
* **Cache.** LRU (`RPC_GATE_CACHE_SIZE`) with TTL (`RPC_GATE_CACHE_TTL_S`). Only non-null, error-free results are stored:
  * `getTransaction`: commitment absent or `finalized`.
  * `getBlock`: commitment absent or `finalized`.
  * `getBlockTime`: only if the returned timestamp is more than 2 min old, i.e. long finalized.
  * `getSignaturesForAddress`: only when `before` is set, the page is non-empty and every entry is `finalized`.
  * Everything else is never cached (`getSlot`, `getLatestBlockhash`, `getBalance`, `getAccountInfo`, `sendTransaction`, `simulateTransaction`, …).
* **Batches.**
  * A batch is served from the cache only if every element hits.
  * Otherwise the whole batch is forwarded once, verbatim. Cacheable elements of the reply are then stored, matched by unique id.
  * A batch that contains `sendTransaction` is not retried across keys.
* **Coalescing.** Identical cacheable requests (same method + canonical params) that are in flight at the same time share one upstream call.
* **Secrets.** Keys appear only in the outbound URL. Logs and `/health` show `…` + the last 4 characters. Upstream transport errors are stripped of the URL before they are logged.

`GET /health` returns:

```json
{"keys":6,"available":0,
 "per_key":[{"key":"…abcd","cooling":true,"cooldown_remaining_s":3412}],
 "public_fallback_used":12,"public_fallback_denied":0,
 "cache":{"hits":3,"misses":9,"size":4,"coalesced":2},
 "upstream":{"requests":40,"errors":18}}
```

## Env

| Var | Default |
|---|---|
| `RPC_GATE_ADDR` | `127.0.0.1:8092` |
| `RPC_GATE_TOKEN` | unset (no auth). If set: `Authorization: Bearer <t>` or `?token=<t>` (for URL-only clients) |
| `RPC_GATE_POOL_CONFIG` | `/opt/ares/.api_key_pools.json` |
| `RPC_GATE_POOL_STATE` | `/opt/ares/.api_key_pool_state.json` |
| `RPC_GATE_PROVIDER` | `helius` |
| `HELIUS_RPC_BASE` | `https://mainnet.helius-rpc.com/` (the key is added as `?api-key=`) |
| `SOLANA_PUBLIC_RPC` | `https://api.mainnet-beta.solana.com` |
| `PUBLIC_RPS` / `PUBLIC_BURST` / `PUBLIC_WAIT_MS` | `4` / `ceil(RPS)` / `3000` (`PUBLIC_RPS=0` disables the fallback) |
| `RPC_GATE_CACHE_SIZE` / `RPC_GATE_CACHE_TTL_S` / `RPC_GATE_CACHE_MAX_ENTRY` | `5000` / `600` / `1048576` bytes |
| `RPC_GATE_UPSTREAM_TIMEOUT_S` | `30` |
| `RPC_GATE_RELOAD_MS` | `1000` |

## Tests

```
cd services/rpc_gate
go vet ./... && go test -race -count=1 -v ./...
/opt/ares/venv/bin/python tests/crosslang_test.py   # builds the binary into a temp dir
```

* The Go tests use `httptest` fake upstreams and temp config/state files. They cover:
  * round-robin distribution;
  * 429 + `max usage reached` writing a Python-format cooldown and skipping the key;
  * plain 429 giving 120s;
  * honouring and reloading external cooldowns;
  * all keys capped falling back to public;
  * the public token bucket;
  * the cache rules;
  * coalescing (25 concurrent calls become 1 upstream call);
  * batches;
  * `sendTransaction` going upstream once;
  * masked health and token auth;
  * no key leaks in error bodies.
* The cross-language script runs a **copy** of `/opt/ares/api_key_pool.py` pointed at temp files against a real gateway process. It checks both directions: Python `report_error` is honoured by Go, and a Go-written cooldown is visible to Python `pool_status`/`get_key`, with merge-preservation and file mode 0600. The real `/opt/ares` state files are never touched.

## Rollout (not done in this PR)

1. Build: `cd services/rpc_gate && go build -o /opt/ares/bin/ares-rpc-gate .`
2. Install `deploy/ares-rpc-gate.service.example` as `/etc/systemd/system/ares-rpc-gate.service` and enable it. This is the operator's call; nothing in this PR starts it.
3. Vantage: `backend/early_buyer.py` already treats a key-source value that starts with `http` as a full RPC URL (`url = key if key.startswith("http") else f"https://mainnet.helius-rpc.com/?api-key={key}"`). The intended switch is a Vantage env flag, **`HELIUS_GATE_URL=http://127.0.0.1:8092`**. When it is set, the Helius key source returns that URL instead of a raw pool key. The Python change is deliberately **not** in this PR, because another branch is editing `early_buyer.py`; it is a follow-up. With `RPC_GATE_TOKEN` set, use `http://127.0.0.1:8092/?token=…`.
4. Other Python daemons can point at the same URL one by one. They keep using `api_key_pool.py` for non-RPC Helius endpoints (e.g. the enhanced `/v0/addresses/...` API, which this gateway does not proxy). Both sides share the same cooldown file.

Known limits:
* Python's `_save_json_atomic` opens `.tmp` with `"w"` before it takes the lock. Two writers racing on the same instant can still lose one update (an existing Python↔Python race). The gateway takes the lock before it truncates, so it never makes this worse.
* Cache entries are per-process and in memory.
