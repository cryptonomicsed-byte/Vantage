#!/bin/bash
set -euo pipefail
# Metabase runs as a non-root container user (uid 2000) and the live
# Vantage DB is deliberately 600-permissioned (protects wallet-encryption
# keys, password/API-key hashes) -- not something to loosen just so a
# dashboard tool can read it. Instead, mirror it with SQLite's online
# backup API (same technique ops/backups/backup_vantage.sh already uses,
# safe against a live WAL-mode DB) into a directory owned by a group
# matching the container's uid.
#
# Real gap found live: a read-only (:ro) single-file bind mount isn't
# enough -- SQLite tries to create a -journal/-wal companion file in the
# same directory even for a plain read, and fails outright if it can't
# ("attempt to write a readonly database"). Mounting a whole DIRECTORY
# read-write instead (containing ONLY this disposable mirror, never the
# live DB) lets SQLite manage its own housekeeping files there -- this
# copy gets replaced wholesale every run regardless, so anything it
# writes into that directory is harmless and gets cleaned up below.
SRC=/opt/ares/Vantage/data/vantage.db
MIRROR_DIR=/opt/ares/Vantage/ops/dashboards/mirror
DEST="$MIRROR_DIR/vantage_mirror.db"

mkdir -p "$MIRROR_DIR"
sqlite3 "$SRC" ".backup '$DEST.tmp'"
mv -f "$DEST.tmp" "$DEST"
rm -f "$DEST-journal" "$DEST-wal" "$DEST-shm"
chown -R root:metabase-mirror "$MIRROR_DIR"
# 770, not 750: the container needs to CREATE its own -journal/-wal
# companion files in this directory (confirmed live -- read+execute alone
# isn't enough, group needs write on the directory itself too).
chmod 770 "$MIRROR_DIR"
chmod 660 "$DEST"

echo "[$(date -u -Iseconds)] mirrored $SRC -> $DEST"
