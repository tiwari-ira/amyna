#!/bin/sh
# Amyna Postgres backups (task 27): dump, rotate, keep N days, poke a restore
# loop is manual -- restore into a scratch DB and run scripts/seed.sh + smoke.
#
# This script runs in the `backup` service of deploy/docker-compose.prod.yml:
# a tiny postgres image with pg_dump; it writes `/backups` and rotates.
set -eu

DB="${POSTGRES_DB:-amyna}"
HOST="${POSTGRES_HOST:-db}"
USER="${POSTGRES_USER:?POSTGRES_USER is required}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
DIR="${BACKUP_DIR:-/backups}"

mkdir -p "$DIR"
cd "$DIR"

while :; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  file="amyna_${ts}.sql.gz"
  echo "[backup] dumping $DB to $file"
  pg_dump -h "$HOST" -U "$USER" -d "$DB" -Fc -f "$file" || {
    echo "[backup] pg_dump failed"
    sleep 3600
    continue
  }
  find . -name 'amyna_*.sql.gz' -mtime +"$KEEP" -delete
  echo "[backup] done; sleeping 24h"
  sleep 86400
done
