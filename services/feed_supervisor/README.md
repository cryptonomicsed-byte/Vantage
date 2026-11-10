# feed_supervisor (ares-feed-supervisor)

Elixir/OTP supervisor that runs Vantage's long-lived feed workers as separate
OS processes instead of asyncio tasks inside the API process. It addresses
two problems seen on the box:

* On restart, Vantage starts dozens of background loops at once, and SQLite
  answers `database is locked` for minutes.
* The in-process pump.fun curve listener drops with
  `sent 1011 (internal error) keepalive ping timeout`. A busy event loop
  cannot answer pings in time.

Each worker in `feeds.json` is a GenServer that owns an Erlang Port:

| key | default | meaning |
|---|---|---|
| `name`, `cmd` | required | `cmd` absolute, or looked up on `PATH` |
| `args`, `cwd`, `env` | `[]`, inherit, `{}` | `env` is added to the supervisor's env |
| `start_delay_ms` | 0 | staggered start, measured from supervisor boot |
| `max_restarts` / `restart_window_s` | 10 / 600 | more restarts than this in the window -> `failed` (no more restarts) |
| `backoff.initial_ms` / `max_ms` / `factor` | 1000 / 60000 / 2 | restart delay doubles from 1 s to a 60 s cap |
| `backoff.reset_after_s` | 120 | a run that stayed up this long resets the delay |
| `kill_timeout_ms` | 10000 | SIGTERM, then SIGKILL of the child's process group |
| `lines_kept` | 20 | output lines kept for `/health` |

Worker states: `waiting` (start delay), `running`, `backoff`, `failed`.
Every output line (stdout and stderr) is logged as `[name] line`.

**No orphans.** The worker runs `cmd` under `priv/run_child.sh`, which puts
it in its own session or process group and watches the port's stdin. The
pipe closes when the BEAM exits for any reason, including SIGKILL, and the
wrapper then TERMs the group and KILLs it 10 s later. Tests cover this, and
it was checked by hand by SIGKILLing a running BEAM.

**Health.** `GET http://127.0.0.1:8094/health` (port from `FEED_SUPERVISOR_PORT`)
returns:

```json
{"status": "ok|degraded", "order": ["curve_listener"],
 "workers": {"curve_listener": {"status": "running", "pid": 1234, "restarts": 0,
   "last_exit_status": null, "uptime_s": 12.3, "last_lines": ["..."],
   "backoff_ms": 1000, "last_restart_delay_ms": null, "next_start_in_ms": null}}}
```

`GET /health/<name>` returns a single worker. The listener binds to
127.0.0.1 only. `status` is `degraded` when any worker is `failed`.

## Curve listener worker

`python -m backend.curve_listener` runs `listener_loop()` in its own process.
It reconnects on its own (1 s to 60 s backoff). Exit codes:

* 0 on SIGTERM or SIGINT.
* 1 on a fatal error, such as an unopenable `CURVE_DB` or the loop dying.
* 2 when `CURVE_LISTENER_ENABLED` is off.

`--check` prints the resolved config and exits. The process writes its live
state to `curve_state.json` next to `curve.db` (override with
`CURVE_STATE_FILE`). With `CURVE_LISTENER_EXTERNAL=1`, `coverage()` in the API
process reads that file, adding `external: true` and `state_age_s`. The env
vars it reads are the same as before: `CURVE_LISTENER_ENABLED`,
`CURVE_USE_PUMPPORTAL`, `CURVE_WS_URL`, `CURVE_DB` / `VANTAGE_DATA_DIR`,
`CURVE_BACKFILL_RPC` and `CURVE_KEEP_*`. If the API process sets any of these
in a drop-in, copy them into the worker's `env` in `feeds.json`.

## Rollout (both halves default off)

Nothing changes until **both** of these are done:

1. `CURVE_LISTENER_EXTERNAL=1` in a Vantage drop-in, so the API stops
   running the listener in-process.
2. `ares-feed-supervisor` is started, so the listener runs out-of-process.

If you do only 1, no listener runs. If you do only 2, two listeners write
`curve.db` at once. Duplicate trades are ignored (`INSERT OR IGNORE`), but
the per-mint counters can drift and the websocket load doubles.

```bash
# install (once): build in place, nothing restarts
cd /opt/ares/Vantage/services/feed_supervisor
MIX_ENV=prod mix deps.get && MIX_ENV=prod mix compile
cp deploy/ares-feed-supervisor.service.example /etc/systemd/system/ares-feed-supervisor.service
cp deploy/vantage-curve-listener-external.conf.example \
   /etc/systemd/system/vantage.service.d/98-curve-listener-external.conf
systemctl daemon-reload

# cut over: restart Vantage with the flag, then start the supervisor right away
systemctl restart vantage
systemctl start ares-feed-supervisor     # curve_listener starts after start_delay_ms (5 s)
curl -s 127.0.0.1:8094/health | python3 -m json.tool
journalctl -u ares-feed-supervisor -f    # lines look like [curve_listener] ...
systemctl enable ares-feed-supervisor    # once it looks healthy
```

During the cut-over, the listener is down for a few seconds. The new process
starts with no `last_sig`, so that short window is not recorded in
`curve_gaps`.

Rollback: `rm /etc/systemd/system/vantage.service.d/98-curve-listener-external.conf`,
`systemctl daemon-reload`, `systemctl stop ares-feed-supervisor`,
`systemctl restart vantage`.

## Tests

```bash
cd services/feed_supervisor && mix deps.get && mix test
/opt/ares/venv/bin/python -m pytest -q -p no:cacheprovider backend/tests/test_curve_listener_external.py
```

The dependencies use the same pins as `ares-swarm-supervisor` (plug_cowboy
2.7.3, plug 1.14.2, cowboy 2.10.0, cowlib 2.13.0), so the host's hex cache
covers them. `mix deps.get` reports security advisories against these
cowboy/cowlib versions. The HTTP surface here is a single loopback-only GET,
but bump the pins (in both apps) when convenient.
