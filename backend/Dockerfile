# ── Stage 1: Build dependencies ────────────────────────────────
FROM python:3.12.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /usr/local/bin/uv

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build
COPY requirements.txt .

RUN uv venv /venv && \
    uv pip install --no-cache --python /venv/bin/python -r requirements.txt


# ── Stage 2: Production runtime ────────────────────────────────
FROM python:3.12.13-slim AS runtime

LABEL org.opencontainers.image.title="MTI Brain Backend" \
      org.opencontainers.image.description="MTI Brain - AI-powered decision intelligence"

RUN groupadd -r appuser && \
    useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

COPY ./app ./app
COPY node_names.yml ./node_names.yml
COPY config.yml ./config.yml


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUNICORN_TIMEOUT=120 \
    GUNICORN_GRACEFUL_TIMEOUT=30 \
    GUNICORN_KEEP_ALIVE=5

HEALTHCHECK --interval=120s --timeout=90s --retries=3 --start-period=60s \
    CMD ["python3", "-c", "import urllib.request, sys; urllib.request.urlopen('http://localhost:8000/health', timeout=80) or sys.exit(1)"]

STOPSIGNAL SIGTERM

USER appuser
EXPOSE 8000

CMD ["gunicorn", "app.main:app", \
     "--bind", "0.0.0.0:8000", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--threads", "2", \
     "--timeout", "480", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "50", \
     "--forwarded-allow-ips", "*", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info"]