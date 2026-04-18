#!/bin/sh
# stock-agent entrypoint
# Usage: entrypoint.sh [api|bot|migrate|worker]
set -e

CMD=${1:-api}

case "$CMD" in
  api)
    echo "[entrypoint] Starting FastAPI with uvicorn..."
    exec uvicorn src.api.app:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers 2 \
      --loop uvloop \
      --access-log
    ;;

  bot)
    echo "[entrypoint] Starting Discord bot..."
    exec python -m src.bot.app
    ;;

  migrate)
    echo "[entrypoint] Running Alembic migrations..."
    exec alembic upgrade head
    ;;

  shell)
    echo "[entrypoint] Starting Python shell..."
    exec python
    ;;

  *)
    echo "[entrypoint] Unknown command: $CMD"
    echo "Usage: entrypoint.sh [api|bot|migrate|shell]"
    exit 1
    ;;
esac
