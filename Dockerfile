# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# stock-agent — multi-stage Dockerfile
# No pip install of the project itself — source is copied directly and
# PYTHONPATH is set so Python can find src/ without a package install step.
# This avoids all hatchling / editable-install issues in Docker.
# ---------------------------------------------------------------------------

# -- Stage 1: builder -------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip

# Copy only the deps list first — layer cached until pyproject.toml changes
COPY pyproject.toml ./

# Install all runtime dependencies directly (no package install, no hatchling)
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0" \
    "sqlalchemy[asyncio]>=2.0.0" \
    "alembic>=1.13.0" \
    "asyncpg>=0.29.0" \
    "aiosqlite>=0.20.0" \
    "pydantic>=2.7.0" \
    "pydantic-settings>=2.3.0" \
    "httpx>=0.27.0" \
    "tenacity>=8.3.0" \
    "structlog>=24.2.0" \
    "discord.py>=2.4.0" \
    "python-dotenv>=1.0.0"

# -- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy venv (deps only, no project wheel needed)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source files
COPY src        ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY scripts    ./scripts

# PYTHONPATH lets Python find src.* without installing the package
ENV PYTHONPATH="/app"

RUN chmod +x /app/scripts/entrypoint.sh

USER appuser

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["api"]
