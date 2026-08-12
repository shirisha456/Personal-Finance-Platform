#!/usr/bin/env bash
# Logical backup: pg_dump the personal_finance_platform database, gzip it, prune old
# copies. This is NOT a substitute for an EBS snapshot (see
# deploy/README.md) — it's a fast, restorable, tiny artifact for the
# common case (a bad migration, an accidental delete), not disaster
# recovery for the whole volume.
#
# Intended to run as a nightly cron job on the host:
#   0 3 * * * /opt/personal-finance-platform/deploy/scripts/backup.sh >> /var/log/personal-finance-platform-backup.log 2>&1
set -euo pipefail

BACKUP_DIR=/opt/personal-finance-platform/backups
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUT_FILE="$BACKUP_DIR/personal-finance-platform-$TIMESTAMP.sql.gz"

mkdir -p "$BACKUP_DIR"
cd /opt/personal-finance-platform

docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
  pg_dump -U personal_finance_platform personal_finance_platform | gzip > "$OUT_FILE"

echo "Backup written to $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"

find "$BACKUP_DIR" -name 'personal-finance-platform-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
echo "Pruned backups older than $RETENTION_DAYS days."
