# Langfuse Self-Hosted

Langfuse v3 observability and tracing stack for mti-brain's LangGraph pipeline. Provides comprehensive logging, tracing, and monitoring for LLM applications.

## Overview

This folder contains a self-hosted Langfuse deployment that integrates with the mti-brain architecture. Langfuse enables:

- **LLM Tracing**: End-to-end tracing of LangGraph pipeline executions
- **Cost Tracking**: Monitor token usage and costs across LLM calls
- **Performance Monitoring**: Track latency and throughput metrics
- **Debugging**: Inspect detailed execution traces for troubleshooting
- **Analytics**: Aggregate statistics on model performance and usage

## Architecture

The Langfuse stack consists of the following services:

```
langfuse-web      → Web UI & API (port 3100)
  ↓
langfuse-worker   → Background job processing (port 3030, localhost only)
  ↓
┌─────────────────────────────────────┐
│ Shared Infrastructure (from /database)
│  • PostgreSQL (langfuse db)
│  • Redis (cache & queues)
└─────────────────────────────────────┘
  ↓
┌─────────────────────────────────────┐
│ Langfuse-Specific Services
│  • ClickHouse (time-series analytics)
│  • MinIO (object storage for events)
└─────────────────────────────────────┘
```

### Service Details

| Service | Image | Purpose | Host Port(s) | Bind | Network(s) | Mem Limit | CPU Limit |
|---------|-------|---------|--------------|------|------------|-----------|-----------|
| `langfuse-web` | `langfuse/langfuse:3` | UI, API, auth | 3100 → 3000 | 0.0.0.0 | `app_net`, `db_net`, `langfuse_internal` | 1536M | 0.5 |
| `langfuse-worker` | `langfuse/langfuse-worker:3` | Background tasks, ingestion | 3030 → 3030 | 127.0.0.1 | `db_net`, `langfuse_internal` | 1G | 0.5 |
| `langfuse-clickhouse` | `clickhouse/clickhouse-server` | Time-series analytics DB | (none published) | — | `langfuse_internal` | 1536M | 1.0 |
| `langfuse-minio` | `minio/minio` | S3-compatible object storage | 9090 → 9000, 9091 → 9001 | 127.0.0.1 | `langfuse_internal` | 256M | 0.25 |

All images are pulled from `docker.io`.

MinIO API is available at `http://127.0.0.1:9090` and the MinIO Console at `http://127.0.0.1:9091`. Both ports are bound to `127.0.0.1` only and are not reachable from the network.

### External Dependencies

- **PostgreSQL**: Dedicated `langfuse` database in shared Postgres instance. Connects directly (bypassing pgbouncer because Langfuse migrations rely on advisory locks, which are broken by transaction pooling).
- **Redis**: Shared Redis instance for queues and caching.
  - **Note**: Set `REDIS_MAXMEMORY_POLICY=noeviction` in `database/.env` if BullMQ queue data loss under memory pressure is a concern (the database stack defaults to `allkeys-lru` eviction).

## Setup

### 1. Prerequisites

Ensure the external Docker networks exist before starting the stack:

```bash
docker network create db_net
docker network create app_net
```

Ensure the main database and Redis services are running:

```bash
cd ../database
docker-compose up -d
```

### 2. Configure Environment

Copy the example environment file and fill in required values:

```bash
cp .env.example .env
```

Edit `.env` with the following values:

```env
# Authentication
NEXTAUTH_SECRET=<generate-random-string>
SALT=<generate-random-string>
ENCRYPTION_KEY=<generate-random-string>

# Database password (must match the langfuse user password created by init/01-create-langfuse-db.sql)
LANGFUSE_DB_PASSWORD=<your-password>

# ClickHouse password
CLICKHOUSE_PASSWORD=<your-password>

# Redis password (must match REDIS_PASSWORD in database/.env)
REDIS_PASSWORD=<your-password>

# MinIO credentials
MINIO_ROOT_PASSWORD=<your-password>

# Public URL (change for remote access)
NEXTAUTH_URL=http://localhost:3100

# Optional: Bootstrap initial organization and project
LANGFUSE_INIT_ORG_NAME=MTI Brain
LANGFUSE_INIT_PROJECT_NAME=Default
LANGFUSE_INIT_USER_EMAIL=admin@example.com
LANGFUSE_INIT_USER_PASSWORD=<initial-password>

# Observability (disable telemetry for privacy)
TELEMETRY_ENABLED=false
```

To generate secure random strings:

```bash
openssl rand -base64 32
```

### 3. Initialize Database

The Langfuse database and user are created automatically when Postgres starts:

```bash
cd ../database
docker-compose up -d postgres
```

The initialization SQL in `init/01-create-langfuse-db.sql` is executed by the database setup.

### 4. Start Services

```bash
docker-compose up -d
```

Verify services are running:

```bash
docker-compose ps
```

### 5. Access the UI

Open your browser and navigate to:

```
http://localhost:3100
```

Log in with the credentials you set in the bootstrap configuration above.

Get your API keys from the Langfuse UI:
1. Log in to http://localhost:3100
2. Navigate to **Settings → Projects**
3. Copy your **Public Key** and **Secret Key**

### Configuration Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NEXTAUTH_SECRET` | Yes | — | NextAuth.js session secret (must be secure) |
| `SALT` | Yes | — | Encryption salt for sensitive data |
| `ENCRYPTION_KEY` | Yes | — | Encryption key for stored credentials |
| `LANGFUSE_DB_PASSWORD` | Yes | — | Postgres password for langfuse user |
| `CLICKHOUSE_USER` | No | `langfuse` | ClickHouse username |
| `CLICKHOUSE_PASSWORD` | Yes | — | ClickHouse password |
| `REDIS_PASSWORD` | Yes | — | Redis password (must match database/.env) |
| `MINIO_ROOT_USER` | No | `langfuse` | MinIO admin username |
| `MINIO_ROOT_PASSWORD` | Yes | — | MinIO admin password |
| `NEXTAUTH_URL` | No | `http://localhost:3100` | Public URL of the Langfuse UI |
| `TELEMETRY_ENABLED` | No | `false` | Enable anonymous telemetry |
| `LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES` | No | `false` | Enable beta features |
| `LANGFUSE_INIT_ORG_ID` | No | — | Bootstrap org ID on first start |
| `LANGFUSE_INIT_ORG_NAME` | No | — | Bootstrap org name on first start |
| `LANGFUSE_INIT_PROJECT_ID` | No | — | Bootstrap project ID on first start |
| `LANGFUSE_INIT_PROJECT_NAME` | No | — | Bootstrap project name on first start |
| `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` | No | — | Bootstrap project public key on first start |
| `LANGFUSE_INIT_PROJECT_SECRET_KEY` | No | — | Bootstrap project secret key on first start |
| `LANGFUSE_INIT_USER_EMAIL` | No | — | Bootstrap admin user email on first start |
| `LANGFUSE_INIT_USER_NAME` | No | — | Bootstrap admin user display name on first start |
| `LANGFUSE_INIT_USER_PASSWORD` | No | — | Bootstrap admin user password on first start |

## Networking

Langfuse uses three Docker networks:

- **`langfuse_internal`** (bridge, created by this compose file): Internal communication between ClickHouse, MinIO, langfuse-worker, and langfuse-web. Not accessible outside the stack.
- **`db_net`** (external, pre-created): Shared with the database stack. Gives langfuse-web and langfuse-worker access to the shared Postgres and Redis containers.
- **`app_net`** (external, pre-created): Shared with backend services. Allows containers on this network to reach `langfuse-web:3000` directly without host-gateway hops.

Both external networks must be created before starting the stack:

```bash
docker network create db_net
docker network create app_net
```

Non-UI ports are bound to `127.0.0.1` only:
- `langfuse-worker`: `127.0.0.1:3030`
- `langfuse-minio` API: `127.0.0.1:9090` (maps to container port 9000)
- `langfuse-minio` Console: `127.0.0.1:9091` (maps to container port 9001)

The web UI port (`3100 → 3000`) binds to all interfaces.

## Troubleshooting

### Services Won't Start

Check logs:

```bash
docker-compose logs langfuse-web
docker-compose logs langfuse-worker
docker-compose logs langfuse-clickhouse
docker-compose logs langfuse-minio
```

### Connection to Database Failed

Ensure:
- Postgres service in the database stack is running
- `LANGFUSE_DB_PASSWORD` matches the `langfuse` user password in `database/.env`
- The `langfuse` database exists (created by `init/01-create-langfuse-db.sql`)
- The `db_net` external network exists

```bash
# Verify from database container
docker-compose -f ../database/docker-compose.yml exec postgres \
  psql -U postgres -d langfuse -c "SELECT version();"
```

### ClickHouse or MinIO Health Issues

```bash
docker-compose logs langfuse-clickhouse
docker-compose logs langfuse-minio
```

### Can't Access UI

- Check if port 3100 is already in use: `lsof -i :3100`
- Verify firewall/security groups allow access on port 3100
- Check `NEXTAUTH_URL` matches the URL you're accessing
- Review logs: `docker-compose logs langfuse-web`

### Redis Connection Errors

Verify Redis credentials match between this stack and the database stack:

```bash
grep REDIS_PASSWORD .env
grep REDIS_PASSWORD ../database/.env
```

## Data Persistence

All data is stored on the host under `./docker_volume/`:

- **ClickHouse data**: `docker_volume/clickhouse/data/`
- **ClickHouse logs**: `docker_volume/clickhouse/logs/`
- **MinIO data**: `docker_volume/minio/data/`
- **Postgres**: Stored in the shared database stack
- **Redis**: Stored in the shared database stack

To backup ClickHouse data:

```bash
docker-compose exec langfuse-clickhouse \
  clickhouse-client --query="BACKUP DATABASE langfuse TO File('/var/lib/clickhouse/backups/backup')"
```

To backup MinIO data:

```bash
docker-compose exec langfuse-minio \
  mc mirror minio/langfuse ./backup/
```

## Performance Tuning

Resource limits from `docker-compose.yml`:

```
langfuse-web:      1536M limit / 512M reserved,  0.5 CPU
langfuse-worker:   1G limit    / 512M reserved,  0.5 CPU
langfuse-clickhouse: 1536M limit / 512M reserved, 1.0 CPU
langfuse-minio:    256M limit  / 128M reserved,  0.25 CPU
```

`langfuse-web` also sets `NODE_OPTIONS=--max-old-space-size=1024` to cap the Node.js heap at 1 GB within the 1536M container limit.

Adjust limits in `docker-compose.yml` based on observed load.

## Security Considerations

1. **Always change default secrets**: `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`
2. **Use strong passwords** for Postgres, Redis, ClickHouse, and MinIO
3. **Non-UI ports are localhost-only**: MinIO (9090/9091) and the worker (3030) bind to `127.0.0.1` and are not reachable from the network
4. **Disable telemetry** by keeping `TELEMETRY_ENABLED=false`
5. **Rotate API keys** regularly in the Langfuse UI
6. **Use HTTPS** in production (configure via nginx reverse proxy)

## Useful Commands

```bash
# View status
docker-compose ps

# View logs (follow mode)
docker-compose logs -f langfuse-web

# Restart services
docker-compose restart

# Stop services (preserve data)
docker-compose stop

# Bring down stack (remove containers but keep data)
docker-compose down

# Bring down stack and remove all volumes
docker-compose down -v
```

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI) | [../backend/README.md](../backend/README.md) |
| Frontend (Next.js) | [../frontend/README.md](../frontend/README.md) |
| Database (PostgreSQL + PgBouncer) | [../database/README.md](../database/README.md) |
| Deployment (AWS CodeDeploy) | [../deploy/README.md](../deploy/README.md) |
