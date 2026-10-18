#!/usr/bin/env bash
# Scheduled PostgreSQL dump with retention. Cron example (daily 02:00):
#   0 2 * * * cd /opt/assessment && ./scripts/backup.sh >> backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p backups
FILE="backups/assess_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U assess --no-owner assess | gzip > "$FILE"
[ -s "$FILE" ] || { echo "backup is empty, removing"; rm -f "$FILE"; exit 1; }
chmod 600 "$FILE"
find backups -name 'assess_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "backup written: $FILE"
# restore:  gunzip -c backups/<file> | docker compose exec -T db psql -U assess assess
