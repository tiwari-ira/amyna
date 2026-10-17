#!/bin/sh
# Amyna Postgres backups (task 27): dump, encrypt, rotate, keep N days, poke a
# restore loop is manual -- restore into a scratch DB and run scripts/seed.sh + smoke.
#
# This script runs in the `backup` service of deploy/docker-compose.prod.yml:
# a tiny postgres image with pg_dump; it writes /backups and rotates.
#
# [2.3] Dumps are PHI: they are encrypted at rest with openssl (AES-256-CBC +
# PBKDF2) unless BACKUP_PASSPHRASE is unset, in which case the script refuses
# to run -- an unencrypted backup of patient data must never exist.
# Restore: openssl enc -d -aes-256-cbc -pbkdf2 -in dump.sql.gz.enc |
#          gunzip | pg_restore -d scratch
set -eu

DB="${POSTGRES_DB:-amyna}"
HOST="${POSTGRES_HOST:-db}"
USER="${POSTGRES_USER:?POSTGRES_USER is required}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
DIR="${BACKUP_DIR:-/backups}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required: backups are PHI and are encrypted at rest}"

mkdir -p "$DIR"
cd "$DIR"

while :; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  plain="amyna_${ts}.sql.gz"
  file="${plain}.enc"
  echo "[backup] dumping and encrypting $DB to $file"
  if ! pg_dump -h "$HOST" -U "$USER" -d "$DB" -Fc | gzip > "$plain"; then
    echo "[backup] pg_dump failed"
    rm -f "$plain"
    sleep 3600
    continue
  fi
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -in "$plain" -out "$file"
  rm -f "$plain"  # the plaintext dump exists only for the pipe's duration
  find . -name 'amyna_*.sql.gz.enc' -mtime +"$KEEP" -delete
  echo "[backup] done; sleeping 24h"
  sleep 86400
done
