# Monitoring (Prometheus + Grafana)

Real request-count/latency/in-flight metrics for the Vantage backend, addressing
the audit's "no monitoring, no observability" finding. Adapted from the already-built
`~/kanban` (TradingOS prototype) Prometheus/Grafana compose stack + Grafana
provisioning rather than written from scratch -- repointed at Vantage's real
`/metrics` endpoint (`prometheus-fastapi-instrumentator`, wired in `backend/main.py`)
instead of that project's Elixir/Go services, with Loki/Jaeger dropped (not
requested, and Loki needs its own promtail log-shipping config that never existed
here either -- not worth the scope creep).

## What's real here

- `backend/main.py` exposes `GET /metrics` (Prometheus text format) via
  `prometheus-fastapi-instrumentator` -- real per-route request counts, latency
  histograms, and in-progress gauges, not fabricated numbers.
- `docker-compose.yml` runs Prometheus (scraping that endpoint every 15s) +
  Grafana (pre-wired to the Prometheus datasource via provisioning).
- Both bound to `127.0.0.1` only -- these are internal ops tools, not exposed to
  the public internet. Use an SSH tunnel or add your own Traefik router with
  real auth if you want remote access.

## Deploy

```bash
cd ops/monitoring
GRAFANA_ADMIN_PASSWORD='<pick a real one>' docker compose up -d
```

Then either SSH-tunnel in (`ssh -L 3301:localhost:3301 -L 9091:localhost:9091 user@host`)
and open `http://localhost:3301` (Grafana) / `http://localhost:9091` (Prometheus), or
work from a shell on the host itself.

## Known gap

No dashboards are pre-built yet -- Grafana comes up with the Prometheus datasource
wired but an empty dashboard list. Explore via Prometheus's own query UI first
(`http://localhost:9091/graph`, try `http_requests_total` or
`http_request_duration_seconds_bucket`) to see what's actually being collected,
then build a dashboard around whichever routes/latencies matter most -- didn't
want to ship a fabricated "looks impressive" dashboard with panels that don't
correspond to real, checked metric names.
