#!/bin/bash
set -euo pipefail
# Metabase runs as a non-root container user (uid 2000) and the live
# Vantage DB is deliberately 600-permissioned (protects wallet-encryption
# keys, password/API-key hashes) -- not something to loosen just so a
# dashboard tool can read it. Instead, mirror it with SQLite's online
# backup API (same technique ops/backups/backup_vantage.sh already uses,
# safe against a live WAL-mode DB) into a separate file owned by a group
# matching the container's uid, isolating any slow analytical query from
# the live production file too.
SRC=/opt/ares/Vantage/data/vantage.db
DEST=/opt/ares/Vantage/ops/dashboards/vantage_mirror.db

sqlite3 "$SRC" ".backup '$DEST.tmp'"
mv -f "$DEST.tmp" "$DEST"
chown root:metabase-mirror "$DEST"
chmod 640 "$DEST"

echo "[$(date -u -Iseconds)] mirrored $SRC -> $DEST"
