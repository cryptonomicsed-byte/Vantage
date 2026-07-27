#!/bin/bash
set -euo pipefail
SRC=/opt/ares/Vantage/data/vantage.db
DEST_DIR=/opt/ares/backups/vantage
STAMP=$(date -u +%Y%m%d_%H%M%S)
DEST="$DEST_DIR/vantage_$STAMP.db"

# .backup uses SQLite's online backup API -- safe against a live WAL-mode DB,
# no need to stop the service or risk a torn read of an in-progress write.
sqlite3 "$SRC" ".backup '$DEST'"
gzip -f "$DEST"

# Retain 14 days; disk is tight (85% full at time of setup) so keep this lean.
find "$DEST_DIR" -name 'vantage_*.db.gz' -mtime +14 -delete

echo "[$(date -u -Iseconds)] backed up $SRC -> $DEST.gz"
