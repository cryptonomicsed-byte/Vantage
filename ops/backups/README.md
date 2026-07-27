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
- `ares-backup-vantage.{service,timer}` / `ares-backup-gitea.{service,timer}` -- the
  systemd units that schedule them (`OnCalendar`, `Persistent=true` so a missed run due
  to downtime fires on next boot).

## Install (new host)

```bash
cp ops/backups/backup_vantage.sh ops/backups/backup_gitea.sh /opt/ares/backups/
chmod +x /opt/ares/backups/backup_*.sh
cp ops/backups/ares-backup-*.service ops/backups/ares-backup-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ares-backup-vantage.timer ares-backup-gitea.timer
```

## Known gap

Neither script backs up the `postgres_data` (if the app's own optional Postgres backend
is in use) or `gitea_data` Docker *volumes* directly -- only Gitea's SQL dump. MongoDB
(declared in docker-compose.yml for supermemory) isn't currently a running container on
the live VPS at all, so there's nothing to back up there today; if it's brought up later,
it needs its own `mongodump` cron/timer added here.
