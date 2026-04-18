#!/usr/bin/env bash
# DEV ONLY — drop and recreate database, then run migrations
# Never run this in production.

set -euo pipefail

if [ "${ENVIRONMENT:-development}" = "production" ]; then
    echo "ERROR: reset_db.sh must not run in production."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
    set -a && source .env && set +a
fi

echo "WARNING: This will DROP all tables and re-run migrations."
read -r -p "Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo "Dropping all tables via downgrade base..."
alembic downgrade base

echo "Re-applying all migrations..."
alembic upgrade head

echo "Done. DB reset complete."
