#!/usr/bin/env bash
# Creates a database owned by repintel_owner with PUBLIC schema rights locked down.
# Usage: create-db.sh <dbname>   (uses PG* env vars / superuser connection)
set -euo pipefail
DB="${1:?db name}"
psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$DB\" OWNER repintel_owner"
psql -v ON_ERROR_STOP=1 -d "$DB" <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO repintel_owner;
REVOKE ALL ON DATABASE "$DB" FROM PUBLIC;
GRANT CONNECT ON DATABASE "$DB" TO repintel_app;
SQL
