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

| Service | Purpose | Port | Network | Resources |
|---------|---------|------|---------|-----------|
| `langfuse-web` | UI, API, auth | 3100 | `app_net`, `db_net`, `langfuse_internal` | 1536M RAM, 0.5 CPU |
| `langfuse-worker` | Background tasks, ingestion | 3030 (localhost) | `db_net`, `langfuse_internal` | 1G RAM, 0.5 CPU |
| `langfuse-clickhouse` | Time-series analytics DB | 8123, 9000 | `langfuse_internal` | — |
| `langfuse-minio` | S3-compatible object storage | 9000, 9001 | `langfuse_internal` | — |

### External Dependencies

- **PostgreSQL**: Dedicated `langfuse` database in shared Postgres instance (`/database`)
- **Redis**: Shared Redis instance for queues and caching (`/database`)
  - **Note**: Set `REDIS_MAXMEMORY_POLICY=noeviction` in `database/.env` if data loss under memory pressure is a concern (BullMQ uses Redis for queues)

## Setup

### 1. Prerequisites

Ensure the main database and Redis services are running:

```bash
cd database
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

# Database password (must match database/.env POSTGRES_PASSWORD for langfuse user)
LANGFUSE_DB_PASSWORD=<your-password>

# ClickHouse password
CLICKHOUSE_PASSWORD=<your-password>

# Redis password (must match database/.env REDIS_PASSWORD)
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

The Langfuse database and user are created automatically by running:

```bash
cd database
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
| `CLICKHOUSE_PASSWORD` | Yes | — | ClickHouse admin password |
| `REDIS_PASSWORD` | Yes | — | Redis password (must match database/.env) |
| `MINIO_ROOT_PASSWORD` | Yes | — | MinIO admin password |
| `NEXTAUTH_URL` | No | `http://localhost:3100` | Public URL of the Langfuse UI |
| `TELEMETRY_ENABLED` | No | `false` | Enable anonymous telemetry |
| `LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES` | No | `false` | Enable beta features |
| `LANGFUSE_INIT_*` | No | — | Bootstrap org/project/user on first start |

## Networking

Langfuse uses three Docker networks:

- **`langfuse_internal`**: Internal communication (ClickHouse, MinIO, worker, web)
- **`db_net`**: Shared with database stack (Postgres, Redis access)
- **`app_net`**: Shared with backend services (allows app_net containers to reach `langfuse-web:3000`)

Non-UI ports (`langfuse-worker:3030`) are bound to `127.0.0.1` for security.

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
- Postgres service in `/database` is running
- `LANGFUSE_DB_PASSWORD` matches the `langfuse` user password in `database/.env`
- The `langfuse` database exists (created by `init/01-create-langfuse-db.sql`)

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
- Verify firewall/security groups allow access
- Check `NEXTAUTH_URL` matches the URL you're accessing
- Review logs: `docker-compose logs langfuse-web`

### Redis Connection Errors

Verify Redis credentials:

```bash
# Compare REDIS_PASSWORD in .env with database/.env
cat .env | grep REDIS_PASSWORD
cat ../database/.env | grep REDIS_PASSWORD
```

## Data Persistence

- **ClickHouse data**: `docker_volume/clickhouse/`
- **MinIO data**: `docker_volume/minio/`
- **Postgres**: Stored in shared database stack (`../database/docker_volume/postgres/`)
- **Redis**: Stored in shared database stack (`../database/docker_volume/redis/`)

To backup data:

```bash
# ClickHouse
docker-compose exec langfuse-clickhouse \
  clickhouse-client --query="BACKUP DATABASE langfuse TO File('/var/lib/clickhouse/backups/backup')"

# MinIO
docker-compose exec langfuse-minio \
  mc mirror minio/langfuse ./backup/
```

## Performance Tuning

Default resource limits (in docker-compose.yml):

```yaml
langfuse-web:
  memory: 1536M (limit), 512M (reserved)
  cpu: 0.5 cores

langfuse-worker:
  memory: 1G (limit), 512M (reserved)
  cpu: 0.5 cores
```

Adjust in `docker-compose.yml` if needed based on load.

## Security Considerations

1. **Always change default secrets**: `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`
2. **Use strong passwords** for database, Redis, ClickHouse, MinIO
3. **Restrict network access**: Non-UI ports are bound to `127.0.0.1` only
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

# Scale worker for higher throughput
docker-compose up -d --scale langfuse-worker=3
```


## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI) | [../backend/README.md](../backend/README.md) |
| Frontend (Next.js) | [../frontend/README.md](../frontend/README.md) |
| Database (PostgreSQL + PgBouncer) | [../database/README.md](../database/README.md) |
| Deployment (AWS CodeDeploy) | [../deploy/README.md](../deploy/README.md) |
