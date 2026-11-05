#!/usr/bin/env bash
# Docker entrypoint hook (runs once, on an empty data directory, as the superuser).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -f /docker-entrypoint-initdb.d/init-roles.sql
PGUSER="$POSTGRES_USER" /docker-entrypoint-initdb.d/create-db.sh repintel
