# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# stock-agent — multi-stage Dockerfile
# Stage 1: builder  — installs deps into a venv
# Stage 2: runtime  — minimal image, copies venv + source only
#
# Build:   docker build -t stock-agent .
# Run API: docker run --env-file .env -p 8000:8000 stock-agent api
# Run bot: docker run --env-file .env stock-agent bot
# ---------------------------------------------------------------------------

# -- Stage 1: builder -------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps needed to compile asyncpg / psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv so we can copy it cleanly to the runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 1. Upgrade pip + install hatchling so the build backend is available
RUN pip install --upgrade pip hatchling

# 2. Copy only the manifest first — layer is cached unless deps change
COPY pyproject.toml ./

# 3. Install runtime deps (non-editable — no src needed at this step)
RUN pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        "sqlalchemy[asyncio]" \
        alembic \
        asyncpg \
        aiosqlite \
        pydantic \
        pydantic-settings \
        httpx \
        tenacity \
        structlog \
        "discord.py" \
        python-dotenv

# 4. Copy source and install the package itself (now src/ is present)
COPY . .
RUN pip install --no-cache-dir --no-deps .


# -- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime system libs only (asyncpg needs libpq at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source + config files
COPY --from=builder /app/src        ./src
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini ./alembic.ini
COPY --from=builder /app/scripts    ./scripts

RUN chmod +x /app/scripts/entrypoint.sh

USER appuser

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["api"]
