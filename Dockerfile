# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# stock-agent — multi-stage Dockerfile
#
# Dep install strategy:
#   1. Copy pyproject.toml + a stub src/ (so hatchling can resolve packages)
#   2. pip install --no-cache-dir . — installs all [project.dependencies] from
#      pyproject.toml as the single source of truth. No manual dep list.
#   3. Runtime stage copies real src/ over PYTHONPATH so the installed stub
#      is shadowed by live source — editable-install behaviour without -e.
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

# Copy pyproject.toml + minimal stub so hatchling can build the wheel metadata
# without needing the full source tree. The stub is overwritten in runtime stage.
COPY pyproject.toml ./
RUN mkdir -p src && touch src/__init__.py

# Install all runtime deps declared in [project.dependencies] — single source
# of truth. Adding a dep to pyproject.toml is enough; no Dockerfile change needed.
RUN pip install --no-cache-dir .

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
