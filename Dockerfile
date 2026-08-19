FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    JOBS_STORE_PATH=/app/data/jobs.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --gecos "" --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser adapters ./adapters
COPY --chown=appuser:appuser config ./config
COPY --chown=appuser:appuser domain ./domain
COPY --chown=appuser:appuser orchestrator ./orchestrator
COPY --chown=appuser:appuser ports ./ports
COPY --chown=appuser:appuser webhooks ./webhooks

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python", "-m", "app.main"]
