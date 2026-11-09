#!/usr/bin/env bash
# Restore a dump made by scripts/backup.sh into an EMPTY database.
#   scripts/restore.sh <file.dump|file.dump.age> <target_db>
# Connect as a superuser (PG* variables). The roles repintel_owner and repintel_app must
# already exist (infra/postgres/prod-init.sh); ownership and row-level security policies are
# restored as dumped. For .age files set AGE_IDENTITY=/path/to/key.txt.
set -euo pipefail
FILE="${1:?dump file}"
TARGET="${2:?target database}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$FILE.sha256" ]; then
  (cd "$(dirname "$FILE")" && sha256sum --check --quiet "$(basename "$FILE").sha256")
fi
SRC="$FILE"
if [[ "$FILE" == *.age ]]; then
  SRC="$(mktemp)"; trap 'rm -f "$SRC"' EXIT
  age --decrypt --identity "${AGE_IDENTITY:?set AGE_IDENTITY}" --output "$SRC" "$FILE"
fi

if psql -XtAc "SELECT 1 FROM pg_database WHERE datname = '$TARGET'" | grep -q 1; then
  n=$(psql -XtA -d "$TARGET" -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
  if [ "$n" != "0" ]; then
    echo "refusing: database $TARGET is not empty ($n tables)" >&2; exit 1
  fi
else
  "$HERE/../infra/postgres/create-db.sh" "$TARGET"
fi
pg_restore --dbname="$TARGET" --exit-on-error --single-transaction "$SRC"
psql -X -d "$TARGET" -c "GRANT CONNECT ON DATABASE \"$TARGET\" TO repintel_app" >/dev/null
echo "restored into $TARGET"
