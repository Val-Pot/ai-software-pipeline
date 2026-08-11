# ==============================================================================
# AI Software Pipeline — Production Dockerfile
# Python 3.12 · FastAPI · Aiogram · Multi-stage build
# ==============================================================================

# ---- Stage 1: dependency builder -------------------------------------------
FROM python:3.12-slim AS builder

# System packages needed to build native extensions (httpx, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only requirements first — leverages Docker layer cache when code changes
# but dependencies do not.
COPY requirements.txt ./

# Install dependencies into a dedicated prefix so we can copy them cleanly.
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ---- Stage 2: production image ---------------------------------------------
FROM python:3.12-slim AS production

# Security: run as non-root user
RUN groupadd --system pipeline && useradd --system --gid pipeline pipeline

WORKDIR /app

# Install curl for HEALTHCHECK probe
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=pipeline:pipeline . .

# Drop privileges
USER pipeline

# Expose FastAPI port
EXPOSE 8000

# Healthcheck: liveness probe via the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ---------------------------------------------------------------------------
# Entrypoint
#
# uvicorn serves the FastAPI ASGI app.
#   --host 0.0.0.0   — bind to all interfaces inside the container
#   --port 8000      — matches EXPOSE above
#   --workers 1      — single worker (Aiogram polling is single-process)
#   --log-level info — structured logs captured by the Python logging config
#   --no-access-log  — access logging handled by middleware / reverse proxy
# ---------------------------------------------------------------------------
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--no-access-log"]
