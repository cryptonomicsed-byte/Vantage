# Vantage trading integration: operator reference

This covers the 2026-11-08 trading handoff (49 tickets, 4 phases), which wires
the trading repos into Vantage as the hub. It is ticket P3-10 (docs only).

Ground rules held throughout:

- Additive only.
- Every new behaviour sits behind an env flag that defaults to off.
- New services are INTEL, BRIDGE or PAPER, never EXECUTION.
- New signal sources are capped at conviction 0.69.
- Shadow before live.
- Guild agents hold no keys.
- No secrets in git.

Never changed: `signal_pool` and its 500 trim, `GET /api/intel/signals`,
`aggregate_score`/`WEIGHTS`, `promotable_signals` 0.75, `jev_decide` (with
its flags off), `trade_outcome_learner`, `source_performance`, the Brier
gate, `trading_gate`, risk limits, `live_mode`, and
`SIGNAL_AUTO_EXECUTE_ENABLED`.

Flags are set in a systemd drop-in, for example
`/etc/systemd/system/vantage.service.d/95-intel-flags.conf`, then:

```
systemctl daemon-reload && systemctl restart vantage
```

Most loops re-read their flag every cycle.

## Where things show up in the app

| Screen | What |
|---|---|
| Trading → Terminal → drawer → **Learning** | flag strip, signal ledger, source reliability (hit rate, Brier, n_eff), shadow rank, TradingOS challenger, walk-forward |
| Trading → Terminal → ExecutionPanel (under the master switch) | the master switch is global and recording is per account; your record-live toggle |
| Trading → Analytics (Market Intel) → trace token | first buyers in entry order: rank, SOL, major entry, funder |
| Trading → **Degen Trenches** → Early-Runner Intel | curve-feed coverage, confluence pings, runners → first buyers with label chips, runner watchlist + strategy-lab radar age |
| Guild → trading space → **Reliability** | bondhive reliability per agent |
| Guild → trading space → **Intel** | guild-router lanes and the intel records filed on this space |

Every section names its own flag when it is off, so "off" never looks
broken.

## Phase 1: bridges and the ledger

- **Signal record and ledger.**
  - `SIGNAL_RECORD_V1` turns on the `signal_record/1` record shape.
  - `SIGNAL_LEDGER_ENABLED` turns on `signal_ledger`, which is never trimmed.
  - `LEDGER_MIRROR_ENABLED` mirrors the ledger to Mycelium.
  - `GET /api/pipeline/ledger` reads it.
- **Bonding-curve progress fix (P1-11).** Progress comes from the curve
  reserves; the response says where it came from in `progress_src`.
- **Trading-space claims are leases (P1-12).**
  - `claim_expires_at`.
  - `POST .../tasks/{id}/heartbeat`.
  - Default lease 30 minutes, maximum 1440.
- **Submit checks (P1-13).** To submit you must be a member, hold the claim,
  and the lease must not have expired.
- **Bridges.**
  - Loom (`LOOM_BASE`), Portent, fpconv and strategy-lab (`STRATEGY_LAB_BASE`).
  - TradingOS schema, vendored at `protocols/tradingos/signal.proto`.
  - ares-control registrations.
  - Crucible as a service: the unit exists but is not installed.
  - Mycelium lineage.

## Phase 2: learning loop in shadow

- **Resolver (mycelium).** Marks each ledger call at +1h, +6h and +24h as a
  `signal_outcome` trace. Flag: `MYCELIUM_RESOLVER_ENABLED`.
- **Source hit rate (mycelium finding `source_hit_rate`).**
  - Fields: n, hits, hit_rate, Brier.
  - Hit rate is null below 30 samples.
  - `n_eff` (P3-02) counts echoes of one call (same token, direction and
    hour) once.
- **Shadow rank.**
  - Shadow score = conviction × (hits+15)/(n+30).
  - Logged every 5 minutes to `shadow_rank_log`.
  - Never fed back into anything. Flag: `SHADOW_RANK_ENABLED`.
  - Endpoints: `GET /api/pipeline/shadow-rank` and `/api/pipeline/learning`.
- **mycelium-tools merged into mycelium (P2-01).** The packages, systemd
  units and a `mycelium_substrate` shim.

## Phase 3: Crucible, bondhive, minipae, Portent

| Ticket | What | Flag |
|---|---|---|
| P3-01 | Crucible trading adapter (`backend/crucible_adapter.py`). The first long/short call for an asset+direction becomes a 47001 claim (falsifier `signal-outcome.wat`); the same address+direction within 30 min becomes a 47002 attestation on that claim; resolver marks become 47010 verdicts. Per-source key files: `CRUCIBLE_SOURCE_KEYS_DIR`. Guild agents go via `CRUCIBLE_ROUTER_KEY_FILE` with attested=false; verdicts are signed only with `CRUCIBLE_RESOLVER_KEY_FILE`. Signed events go to the `crucible_events` outbox and are pushed to `CRUCIBLE_RELAY_URL`. | `CRUCIBLE_ADAPTER_ENABLED` |
| P3-03 | One 47005 calibration event per source, per window, per UTC day, signed by the resolver key. | `CRUCIBLE_CALIBRATION_ENABLED` |
| P3-04 | Portent settles only from Crucible keys. | `PORTENT_CRUCIBLE_ONLY` |
| P3-05 | Cassandra's types, commit-then-verify, monitor and vector client, moved into Portent's `cassandra/`. The random engine and the code generator were dropped. | `PORTENT_VECTOR_ENABLED`, `PORTENT_ORACLE_MONITOR_ENABLED` |
| P3-06 | bondhive reliability, ported bit-for-bit from `score.rs`. `GET /api/guilds/{slug}/trading-spaces/{id}/reliability`. | — (read/record only) |
| P3-07 | minipae decision memory, at `mem/decision/<id>`. | `MINIPAE_DECISIONS_ENABLED` |
| P3-08 | minipae `signed_record/1`: detached BIP-340 signatures that verify elsewhere. | `MINIPAE_SIGNED_RECORDS_ENABLED` (signing only) |
| P3-09 | Engrams built and signed by minipae. The format is wire-identical to the legacy one, so no migration is needed. | `BUZZ_ENGRAMS_VIA_MINIPAE`; `BUZZ_ENGRAMS_LEGACY=1` forces the old path |

Crucible is not installed on the box. Until it is, the adapter idles and says
so. Installing it means:

- run `deploy/install.sh` in the Crucible repo;
- generate keys on the VPS with `crucible keygen.live`;
- set the resolver key as an `oracle_authority`.

Who holds the oracle key long term is open decision 4.

## Phase 4: early-runner intel, guild agents, recording, TradingOS

| Ticket | What | Flag | Endpoint |
|---|---|---|---|
| P4-01 | Curve listener. Free public websocket `logsSubscribe`; trade/create events decoded from the logs; its own `data/curve.db` with retention (`CURVE_DB`, `CURVE_KEEP_FIRST`, …). | `CURVE_LISTENER_ENABLED` | `GET /api/intel/curve/coverage` |
| P4-02 | Runner detection. DexScreener, taking the largest pair's mcap; tiers 1M/5M/10M (the 100k tier is off: `RUNNER_TIER_100K`). | `RUNNER_DETECT_ENABLED` | `GET /api/intel/runners` |
| P4-03 | Backtrace to the create transaction: resumable, oldest first, exact buyer and SOL from the TradeEvent. Uses the free public RPC (`RUNNER_TRACE_RPC`). | `RUNNER_TRACE_ENABLED` | `GET/POST /api/intel/runners/{mint}/early-buyers` |
| P4-04 | trace-token fixed; first buyers come from the early-buyer walk. | — | existing |
| P4-05 | Wallet and KOL labels with evidence: dev, sniper, bundler, insider, bot, funding_hub, kol, whale. | `WALLET_LABELS_ENABLED` | `GET /api/intel/wallet-labels`, `/api/intel/wallets/{w}/labels` |
| P4-06 / P4-07 | Runner watchlist. Score = tier × 1/rank × size, with a 90-day decay; a wallet needs 2+ runners; a cluster counts once. The strategy-lab radar is a separate source. | `RUNNER_WATCHLIST_ENABLED` | `GET /api/intel/runner-watchlist` |
| P4-08 / P4-10 / P4-13 | Guild router. The `intel_record/1` lanes are x.* (Grokbot), news.* (Muse) and research.* (Q). x.* writes to `social_signals.posted_at`. Schedules come from `GUILD_ROUTER_SCHEDULES`. | `GUILD_ROUTER_ENABLED` | `POST .../tasks/{id}/intel`, `GET .../intel`, `GET /api/guild-router/status` |
| P4-09 | Shared anti-pump filter plus confluence ping. Fires on 3 watchlist wallets, 2 KOLs posting the exact contract, or one of each, under $60k within 30 min. Posted to the pool as `runner_confluence`, conviction ≤ 0.69. | `CONFLUENCE_ENABLED`, `CONFLUENCE_ENRICHER` | `GET /api/intel/confluence` |
| P4-11 / P4-12 | Recording preferences per account. `record_live` defaults to 1; `leaderboard_opt_in` is stored only. | — | `GET/PUT /api/trading/recording` |
| P4-14 | kanban's TradingOSQuant (Julia) into `services/quant` (unit not installed), plus whale_watcher. | `QUANT_JULIA_ENABLED`, `WHALE_WATCHER_ENABLED` | `GET /api/intel/quant/*` |
| P4-15 | TradingOS challenger in shadow. `TRADINGOS_INTEL_URL` has no default. | `TRADINGOS_CHALLENGER_ENABLED` | `GET /api/pipeline/tradingos-challenger` |
| P4-16 | Walk-forward report: day by day with no leakage; live vs shadow top-K and the challenger's veto split. | — (read-only) | `GET /api/pipeline/walk-forward` |

## Turning things on, in order

1. Start the ledger, mirror and shadow rank. Hit rates then need the
   Mycelium resolver to be merged and running.
2. Start the curve listener, then runner detection, trace, labels and the
   watchlist. The watchlist fills as runners are traced, which takes days.
3. Turn on confluence only once the watchlist has entities and someone has
   decided to. It posts to the signal pool. Its conviction cap of 0.69 keeps
   it below `promotable_signals`' 0.75, but check live-mode arming and
   `SIGNAL_AUTO_EXECUTE_ENABLED` before enabling it.
4. Crucible needs to be installed and have keys before the adapter and
   calibration do anything.
5. The guild router needs a lane allowlist. Muse/Q access is open decision 3.

## Open decisions (not built)

1. Other launchpads.
2. Exit signals, dev launch score, Telegram/Discord calls, cold-wallet decay.
3. Muse and Q access, schedules, space owner, the Omokoda agent key.
4. The long-term oracle key holder.
5. A watchlist seed. The watchlist is computed only.
6. Hit thresholds per asset class.
7. Cap raises, and shadow → live.
