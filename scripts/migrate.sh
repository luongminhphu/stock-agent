#!/usr/bin/env bash
# Usage:
#   ./scripts/migrate.sh upgrade        — apply all pending migrations
#   ./scripts/migrate.sh downgrade -1   — rollback 1 revision
#   ./scripts/migrate.sh revision "msg" — autogenerate new migration
#   ./scripts/migrate.sh current        — show current DB revision
#   ./scripts/migrate.sh history        — show migration history
#
# Requires: DATABASE_URL set in environment or .env file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present (dev convenience)
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a && source .env && set +a
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set."
    exit 1
fi

COMMAND="${1:-upgrade}"

case "$COMMAND" in
    upgrade)
        echo "Running: alembic upgrade head"
        alembic upgrade head
        ;;
    downgrade)
        TARGET="${2:--1}"
        echo "Running: alembic downgrade $TARGET"
        alembic downgrade "$TARGET"
        ;;
    revision)
        MSG="${2:-auto}"
        echo "Running: alembic revision --autogenerate -m \"$MSG\""
        alembic revision --autogenerate -m "$MSG"
        ;;
    current)
        alembic current
        ;;
    history)
        alembic history --verbose
        ;;
    *)
        echo "Unknown command: $COMMAND"
        echo "Usage: $0 {upgrade|downgrade|revision|current|history}"
        exit 1
        ;;
esac
