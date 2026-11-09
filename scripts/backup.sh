#!/usr/bin/env bash
# Logical backup of the RepIntel database (custom format, compressed).
# Connection from the standard PG* variables (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE);
# use the owner role or a superuser so every table is readable despite row-level security.
#
#   BACKUP_DIR=/srv/repintel/backups scripts/backup.sh
#   With the production compose stack:
#   docker compose -f docker-compose.prod.yml exec -T postgres \
#     env PGUSER=postgres BACKUP_DIR=/backups sh -s < scripts/backup.sh
#
# Optional: BACKUP_AGE_RECIPIENT=age1… encrypts the dump with `age` (recommended before it
# leaves the host); BACKUP_KEEP_DAYS (default 14) prunes older local dumps.
set -euo pipefail
DIR="${BACKUP_DIR:-./backups}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
DB="${PGDATABASE:-repintel}"
mkdir -p "$DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DIR/repintel-$STAMP.dump"

pg_dump --dbname="$DB" --format=custom --compress=6 --no-comments --file="$OUT.partial"
pg_restore --list "$OUT.partial" >/dev/null          # the archive is readable
mv "$OUT.partial" "$OUT"

if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  age --recipient "$BACKUP_AGE_RECIPIENT" --output "$OUT.age" "$OUT"
  rm -f "$OUT"
  OUT="$OUT.age"
fi
(cd "$DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

find "$DIR" -maxdepth 1 -name 'repintel-*.dump*' -mtime +"$KEEP" -delete
echo "$OUT"
