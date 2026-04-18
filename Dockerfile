# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# stock-agent — multi-stage Dockerfile
# Stage 1: builder  — installs deps into a venv
# Stage 2: runtime  — minimal image, copies venv only
#
# Build:   docker build -t stock-agent .
# Run API: docker run --env-file .env -p 8000:8000 stock-agent api
# Run bot: docker run --env-file .env stock-agent bot
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps needed to compile some Python packages (e.g. asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtualenv so we can copy it cleanly to runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install deps first (layer-cached unless pyproject.toml changes)
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -e ".[dev]"

# Copy source last (invalidates layer only on code changes)
COPY . .


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY --from=builder /app /app

# Ensure the entrypoint is executable
RUN chmod +x /app/scripts/entrypoint.sh

# Run as non-root
USER appuser

# Default: start API. Override with "bot" to start the Discord bot.
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["api"]
