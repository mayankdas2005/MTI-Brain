# ── Stage 1: Build dependencies ────────────────────────────────
FROM python:3.12.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /usr/local/bin/uv

# Build-stage system libs - sdist fallbacks for three Python wheels:
#
#   * pyodbc / aioodbc  → need unixodbc-dev + gcc if the wheel is absent
#   * pymssql           → need libssl-dev + libkrb5-dev + gcc if the
#                         wheel is absent (bundled FreeTDS source build)
#
# CPython 3.12 on glibc x86_64 / arm64 is covered by the PyPI wheels for
# all three packages, so in practice nothing here compiles from source -
# but keeping the headers present means ``uv pip install`` never errors
# out on a platform where the wheel is missing (e.g. musl alpine builds
# if someone swaps the base image).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        unixodbc-dev \
        libssl-dev \
        libkrb5-dev && \
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

# ──────────────────────────────────────────────────────────────────────────
# SQL Server drivers + FreeTDS for the three supported connection paths.
# All selected per-datasource via ``dialect`` in datasource_config.json:
#
#   dialect="sqlserver"  (TsqlAdapter, aioodbc + pyodbc)
#     → default driver = "ODBC Driver 18 for SQL Server"
#       Right choice for SQL Server 2012+ / Azure SQL (TLS 1.2+).
#     → override driver = "ODBC Driver 17 for SQL Server"
#       Older driver; works with some legacy TLS 1.0 instances.
#     → override driver = "FreeTDS"  (system-registered below)
#       Last-resort ODBC path for very old servers.
#
#   dialect="pymssql"  (PymssqlAdapter)
#     → Uses pymssql's BUNDLED FreeTDS - no system driver required.
#       Best choice for SQL Server 2008 / 2008 R2.
#
# ``packages-microsoft-prod.deb`` registers the signing key + APT source
# in one atomic step (less fragile than manual gpg/keyring wiring on
# minimal base images where /etc/apt/keyrings may be missing or gnupg
# can't bootstrap a home dir).
#
# ``ACCEPT_EULA=Y`` is Microsoft's required non-interactive install flag;
# without it debconf prompts and the build hangs.
# ──────────────────────────────────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        unixodbc \
        unixodbc-dev \
        freetds-dev \
        freetds-bin \
        tdsodbc \
        gnupg2 \
        curl \
        ca-certificates \
        apt-transport-https && \
    # Add the Microsoft package repository key
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | \
        gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    # Register the Microsoft Debian 12 (bookworm) package repository
    echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    # Install the ODBC driver and command-line tools (EULA auto-accepted)
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql17 \
        msodbcsql18 \
        mssql-tools18 && \
    # Add sqlcmd / bcp to PATH for debugging
    echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> /etc/profile.d/mssql-tools.sh && \
    # Clean up apt caches to keep the image small
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────────────────
# OpenSSL 3.x disables TLS 1.0 / 1.1 by default. SQL Server 2008 / 2008 R2
# can only speak TLS 1.0; Driver 18 fails with ODBC error 0x2746 unless we
# lower the minimum protocol version and security level. This config file
# keeps TLS 1.2+ as the default everywhere else while allowing the legacy
# handshake for servers that still need it.
#
# Applied globally via OPENSSL_CONF - affects every process in the image
# (pyodbc, aioodbc, psycopg, httpx). Only matters when talking to legacy
# endpoints; modern TLS 1.2+ handshakes negotiate the stronger protocol
# automatically.
# ──────────────────────────────────────────────────────────────────────────
RUN printf '[openssl_init]\nssl_conf = ssl_sect\n\n[ssl_sect]\nsystem_default = system_default_sect\n\n[system_default_sect]\nMinProtocol = TLSv1.0\nCipherString = DEFAULT:@SECLEVEL=0\n' \
    > /etc/ssl/openssl_override.cnf
ENV OPENSSL_CONF=/etc/ssl/openssl_override.cnf

# ──────────────────────────────────────────────────────────────────────────
# Register FreeTDS as an ODBC driver named "FreeTDS". Lets operators set
# ``"driver": "FreeTDS"`` in datasource_config.json's ODBC path (when
# dialect="sqlserver") for servers that neither Driver 17 nor Driver 18
# can handle. Not used by the pymssql path - that one has its own
# bundled FreeTDS inside the wheel.
#
# Auto-detects ``libtdsodbc.so`` for both amd64 (x86_64-linux-gnu) and
# arm64 (aarch64-linux-gnu) multiarch paths so the same Dockerfile
# builds cleanly on both platforms.
# ──────────────────────────────────────────────────────────────────────────
RUN TDS_LIB=$(find /usr/lib -name 'libtdsodbc.so*' | head -n 1) && \
    if [ -z "$TDS_LIB" ]; then echo "libtdsodbc.so not found" >&2; exit 1; fi && \
    printf '[FreeTDS]\nDescription=FreeTDS ODBC driver for SQL Server\nDriver=%s\nSetup=%s\nFileUsage=1\n' \
        "$TDS_LIB" "$TDS_LIB" >> /etc/odbcinst.ini && \
    echo "Registered FreeTDS driver at: $TDS_LIB"

# Security: non-root user
RUN groupadd -r appuser && \
    useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /venv /venv
# mssql-tools18 on PATH so ``docker exec`` users can run sqlcmd / bcp
# without sourcing /etc/profile.d scripts (Alpine-style minimal shell).
ENV PATH="/venv/bin:/opt/mssql-tools18/bin:$PATH"

COPY ./app ./app

# Writable data directory for uploaded scheduling files
RUN mkdir -p /app/data && chown appuser:appuser /app/data

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