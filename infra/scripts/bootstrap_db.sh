#!/usr/bin/env bash
# Layer 1 of the DB bootstrap (see docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md §3.5).
# Runs the bootstrap_db.sql as Postgres SUPERUSER. Idempotent — safe to re-run.
#
# Environment:
#   CODEORACLE_DB__HOST       (default: localhost)
#   CODEORACLE_DB__PORT       (default: 5432)
#   CODEORACLE_DB__USER       (default: codeoracle — must be SUPERUSER in dev/CI)
#   CODEORACLE_DB__PASSWORD   (default: codeoracle)
#   CODEORACLE_DB__DATABASE   (default: codeoracle)
#
# Exit codes:
#   0 — bootstrap succeeded (or was already in the desired state)
#   1 — psql connection failed (DB not up, wrong credentials)
#   2 — SQL execution failed (likely AGE / pgvector not installed in the image)

set -euo pipefail

HOST="${CODEORACLE_DB__HOST:-localhost}"
PORT="${CODEORACLE_DB__PORT:-5432}"
USER="${CODEORACLE_DB__USER:-codeoracle}"
PASSWORD="${CODEORACLE_DB__PASSWORD:-codeoracle}"
DATABASE="${CODEORACLE_DB__DATABASE:-codeoracle}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/bootstrap_db.sql"

if [ ! -f "$SQL_FILE" ]; then
  echo "::error::bootstrap_db.sql not found at $SQL_FILE"
  exit 2
fi

echo "Bootstrapping $USER@$HOST:$PORT/$DATABASE ..."

export PGPASSWORD="$PASSWORD"

# Wait up to 30s for Postgres to accept connections (CI / cold compose start).
for _ in $(seq 1 30); do
  if psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DATABASE" -c 'SELECT 1' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DATABASE" -c 'SELECT 1' >/dev/null 2>&1; then
  echo "::error::cannot connect to Postgres after 30s"
  unset PGPASSWORD
  exit 1
fi

# Run the bootstrap SQL with the db name substituted into ALTER DATABASE.
if ! psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DATABASE" \
     -v db_name="$DATABASE" -v ON_ERROR_STOP=1 -f "$SQL_FILE"; then
  echo "::error::bootstrap_db.sql failed — is the image AGE+pgvector-enabled?"
  unset PGPASSWORD
  exit 2
fi

unset PGPASSWORD
echo "Bootstrap complete."
