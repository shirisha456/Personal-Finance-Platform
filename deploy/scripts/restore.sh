#!/usr/bin/env bash
# Restores a backup produced by backup.sh. DESTRUCTIVE — drops and
# recreates the personal_finance_platform database. Stops every service that writes to
# Postgres first so nothing races the restore.
#
# Usage: deploy/scripts/restore.sh /opt/personal-finance-platform/backups/personal-finance-platform-<timestamp>.sql.gz
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <backup-file.sql.gz>" >&2
  exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
  echo "No such file: $BACKUP_FILE" >&2
  exit 1
fi

echo "This will DROP and recreate the personal_finance_platform database using $BACKUP_FILE."
read -r -p "Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

cd /opt/personal-finance-platform

echo "Stopping services that write to Postgres..."
docker compose -f deploy/docker-compose.prod.yml stop \
  core-api enrichment-service anomaly-service notification-service

echo "Dropping and recreating the database..."
docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
  psql -U personal_finance_platform -d postgres -c "DROP DATABASE personal_finance_platform;"
docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
  psql -U personal_finance_platform -d postgres -c "CREATE DATABASE personal_finance_platform;"

echo "Restoring from $BACKUP_FILE..."
gunzip -c "$BACKUP_FILE" | docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
  psql -U personal_finance_platform -d personal_finance_platform

echo "Restarting the full stack..."
docker compose -f deploy/docker-compose.prod.yml up -d

echo "Restore complete."
