#!/bin/bash
set -euo pipefail
DEST_DIR=/opt/ares/backups/gitea
STAMP=$(date -u +%Y%m%d_%H%M%S)
DEST="$DEST_DIR/gitea_$STAMP.sql"

docker exec ares-postgres pg_dump -U gitea gitea > "$DEST"
gzip -f "$DEST"

find "$DEST_DIR" -name 'gitea_*.sql.gz' -mtime +14 -delete

echo "[$(date -u -Iseconds)] backed up gitea postgres -> $DEST.gz"
