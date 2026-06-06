# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# stock-agent — multi-stage Dockerfile
#
# Dep install strategy:
#   - pyproject.toml là single source of truth cho tất cả runtime deps.
#   - pip cache được giữ lại giữa các lần build qua BuildKit cache mount
#     (/root/.cache/pip trên host) — không re-download khi pyproject.toml không đổi.
#   - Layer cache: COPY pyproject.toml → pip install là 1 layer riêng.
#     Chỉ invalidate khi pyproject.toml thay đổi, không bị ảnh hưởng bởi code commit.
#   - Runtime stage COPY src/ ghi đè stub — source thay đổi không làm pip chạy lại.
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

# Layer 1: chỉ copy pyproject.toml + stub src — layer này cache until pyproject.toml đổi.
# Commit code không đụng vào layer này → pip không bao giờ chạy lại khi chỉ đổi code.
COPY pyproject.toml ./
RUN mkdir -p src && touch src/__init__.py

# BuildKit cache mount giữ pip wheel cache giữa các lần build trên host.
# Khi pyproject.toml đổi: pip chỉ download package mới, package cũ vẫn được cache.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

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
