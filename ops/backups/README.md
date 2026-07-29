# Backups

Real, running daily backups -- previously lived only on the VPS (`/opt/ares/backups/`,
`/etc/systemd/system/ares-backup-*`), invisible to a repo-only audit even though they've
been executing successfully every night (verified via `systemctl list-timers`: both
`ares-backup-vantage.timer`/`ares-backup-gitea.timer` last ran and succeeded). Checked in
here so they're documented and versioned instead of only existing as untracked state on
one box.

- `backup_vantage.sh` -- SQLite online-backup API (`.backup`, safe against a live
  WAL-mode DB, no service stop needed) + gzip, 14-day retention. Runs 03:00 UTC nightly.
- `backup_gitea.sh` -- `pg_dump` of Gitea's Postgres backend + gzip, 14-day retention.
  Runs 03:10 UTC nightly.
- `ares-backup-vantage.{service.example,timer}` / `ares-backup-gitea.{service.example,timer}`
  -- the systemd units that schedule them (`OnCalendar`, `Persistent=true` so a missed
  run due to downtime fires on next boot). The `.service` files are `.example` per this
  repo's `.gitignore` policy (`*.service` is blocked entirely -- a past incident put a
  plaintext `GITEA_TOKEN`/`VANTAGE_ADMIN_KEY` into a committed unit file; these two are
  secret-free, but the convention is followed regardless rather than treated as a
  per-file judgment call).

## Install (new host)

```bash
cp ops/backups/backup_vantage.sh ops/backups/backup_gitea.sh /opt/ares/backups/
chmod +x /opt/ares/backups/backup_*.sh
cp ops/backups/ares-backup-vantage.service.example /etc/systemd/system/ares-backup-vantage.service
cp ops/backups/ares-backup-gitea.service.example /etc/systemd/system/ares-backup-gitea.service
cp ops/backups/ares-backup-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ares-backup-vantage.timer ares-backup-gitea.timer
```

## Known gap

Neither script backs up the `postgres_data` (if the app's own optional Postgres backend
is in use) or `gitea_data` Docker *volumes* directly -- only Gitea's SQL dump. MongoDB
(declared in docker-compose.yml for supermemory) isn't currently a running container on
the live VPS at all, so there's nothing to back up there today; if it's brought up later,
it needs its own `mongodump` cron/timer added here.
