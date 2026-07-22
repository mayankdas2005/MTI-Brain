FROM mti-brain-backend-base:latest

LABEL org.opencontainers.image.title="MTI Brain Backend" \
      org.opencontainers.image.description="MTI Brain - AI-powered decision intelligence"

WORKDIR /app

COPY ./app ./app
COPY node_names.yml ./node_names.yml
COPY config.yml ./config.yml
COPY join_key_profile.json ./join_key_profile.json

HEALTHCHECK --interval=120s --timeout=5s --retries=3 --start-period=60s \
    CMD ["python3", "-c", "import urllib.request, sys; urllib.request.urlopen('http://localhost:8000/health/live', timeout=3) or sys.exit(1)"]

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