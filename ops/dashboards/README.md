# Dashboards (Metabase)

Real business dashboards (trading P&L, agent growth, broadcast volume) on Vantage's
actual data -- complements `ops/monitoring`'s Prometheus+Grafana (operational metrics:
request latency, error rates) rather than replacing it. Grafana/PromQL is the wrong
tool for "how many agents joined this week" style questions; Metabase's SQL-friendly,
non-technical-friendly UI is a much better fit, and it's a single self-hosted
container.

## Why a mirror, not the live DB directly

Vantage's real `data/vantage.db` is deliberately `600`-permissioned (protects the
AES-256-GCM wallet-encryption material and agent API-key hashes it contains) -- to
be found out the hard way that Metabase's container runs as a non-root user (uid
2000) that can't read it, and loosening those permissions is not the right fix.

`refresh_mirror.sh` maintains a periodic read-only copy instead, using the same
`sqlite3 .backup` online-backup technique `ops/backups/backup_vantage.sh` already
uses (safe against a live WAL-mode DB, no need to stop anything). The mirror file is
owned by a host group (`metabase-mirror`, gid 2000) matching the container's uid --
readable by Metabase, not world-readable. Runs hourly via
`ares-metabase-mirror.timer`. Dashboards are up to an hour stale; that's the
deliberate tradeoff for not touching the live file's permissions or querying
production directly for potentially-slow analytical queries.

## Deploy (new host)

```bash
groupadd -g 2000 metabase-mirror   # must match the metabase container's uid, verify via:
                                    # docker exec vantage_metabase id metabase
chmod +x ops/dashboards/refresh_mirror.sh
./ops/dashboards/refresh_mirror.sh   # first mirror, before Metabase starts
cp ops/dashboards/ares-metabase-mirror.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ares-metabase-mirror.timer

cd ops/dashboards && docker compose up -d
```

Bound to `127.0.0.1` only -- use an SSH tunnel (`ssh -L 3401:localhost:3401 ...`) or
add your own Traefik router with real auth for remote access, same as
`ops/monitoring`.

## Known gap

No pre-built dashboard/questions yet -- initial setup + SQLite datasource connection
done via Metabase's API, but building the actual business-metric questions (P&L over
time, agent growth curve, broadcast volume by surface) is a follow-up, same honest
gap noted in `ops/monitoring/README.md` for Grafana.
