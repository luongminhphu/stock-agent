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

# Layer 1: chỉ copy pyproject.toml — layer này cache until pyproject.toml đổi.
# Commit code không đụng vào layer này → pip không bao giờ chạy lại khi chỉ đổi code.
COPY pyproject.toml ./

# Parse [project.dependencies] từ pyproject.toml bằng Python stdlib (tomllib, Python 3.11+)
# rồi pip install trực tiếp — không install package stock-agent vào venv, giữ source
# hoàn toàn qua PYTHONPATH. BuildKit cache mount tránh re-download wheel đã có.
RUN --mount=type=cache,target=/root/.cache/pip \
    python - <<'EOF'
import tomllib, subprocess, sys
with open("pyproject.toml", "rb") as f:
    deps = tomllib.load(f)["project"]["dependencies"]
subprocess.check_call([sys.executable, "-m", "pip", "install", "--cache-dir", "/root/.cache/pip", *deps])
EOF

# -- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# --home /home/appuser: set HOME trong passwd nhưng không tạo thư mục.
# Tạo thủ công + chức năng vnstock cache (.vnstock/id) với đúng owner.
RUN addgroup --system appgroup \
 && adduser --system --ingroup appgroup --home /home/appuser appuser \
 && mkdir -p /home/appuser/.vnstock/id \
 && chown -R appuser:appgroup /home/appuser

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
