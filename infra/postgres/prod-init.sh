#!/usr/bin/env bash
# Production database bootstrap (runs once, on an empty data directory, as the superuser).
# Passwords come from the environment (secret manager / .env.production), never from git.
set -euo pipefail
: "${REPINTEL_OWNER_DB_PASSWORD:?}" "${REPINTEL_APP_DB_PASSWORD:?}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
  -v owner_pw="$REPINTEL_OWNER_DB_PASSWORD" -v app_pw="$REPINTEL_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE repintel_owner LOGIN BYPASSRLS PASSWORD :'owner_pw';
CREATE ROLE repintel_app LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';
SQL
PGUSER="$POSTGRES_USER" /opt/repintel/create-db.sh repintel
