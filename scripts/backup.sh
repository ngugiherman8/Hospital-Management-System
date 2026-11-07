#!/usr/bin/env bash
# Nightly encrypted dump. Cron: 0 2 * * * /opt/hms/scripts/backup.sh
# Needs BACKUP_PASSPHRASE in the environment; copy the .gpg file to cloud storage afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
out="backups/hms-$(date +%F).sql.gz.gpg"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip | gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" -c -o "$out"
find backups -name 'hms-*.gpg' -mtime +30 -delete
echo "Wrote $out"
