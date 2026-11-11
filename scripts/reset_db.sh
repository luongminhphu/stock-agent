#!/usr/bin/env bash
# DEV ONLY — xoá sạch schema rồi dựng lại từ migration (0001_baseline → head)
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

echo "WARNING: Thao tác này XOÁ TOÀN BỘ dữ liệu (DROP SCHEMA public) rồi tạo lại từ baseline."
read -r -p "Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Không dùng `alembic downgrade base`: DB cũ có thể đang ở revision của chuỗi migration
# đã gộp vào 0001_baseline (alembic không còn biết revision đó) → xoá schema trực tiếp.
echo "Dropping schema public..."
python - <<'PY'
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

asyncio.run(main())
PY

echo "Re-applying all migrations..."
alembic upgrade head

echo "Done. DB reset complete."
