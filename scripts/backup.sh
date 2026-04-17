#!/bin/bash
set -euo pipefail

# ── Configuration ──
BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL not set}"
DATE=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="$BACKUP_DIR/flaam_$DATE.dump.gz"

mkdir -p "$BACKUP_DIR"

# ── Dump PostgreSQL ──
echo "[backup] Starting pg_dump..."
pg_dump --format=custom "$DATABASE_URL" | gzip > "$DUMP_FILE"
echo "[backup] Dump created: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

# ── Retention locale 7 jours ──
echo "[backup] Cleaning old backups (>7 days)..."
find "$BACKUP_DIR" -name "flaam_*.dump.gz" -mtime +7 -delete

# ── Upload remote (a configurer en prod) ──
# Decommenter quand S3/R2 est configure :
# aws s3 cp "$DUMP_FILE" "s3://flaam-backups/$(basename $DUMP_FILE)"
# echo "[backup] Uploaded to S3"

echo "[backup] Done."
