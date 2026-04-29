# MTI Brain Database

Docker Compose stack providing the data layer for MTI Brain.

## Services

| Service | Image | Exposed Port | Purpose |
|---|---|---|---|
| **PostgreSQL** | `pgvector/pgvector:0.8.1-pg18` | None (internal only) | Primary relational store with pgvector extension for vector similarity search |
| **PgBouncer** | `edoburu/pgbouncer:v1.25.1-p0` | `5432` | Connection pooler in front of PostgreSQL (transaction pooling mode). Depends on PostgreSQL being healthy. |
| **Neo4j** | `neo4j:5.26.0-enterprise` | `7474` (HTTP/Browser), `7687` (Bolt) | Graph database with APOC and Graph Data Science plugins. |

All services are connected via a private `db_net` bridge network.

## Prerequisites

- Docker and Docker Compose

## Getting Started

1. **Create your environment file:**

   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** and set real values for at minimum:

   - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
   - `NEO4J_USER`, `NEO4J_PASSWORD`

3. **Start the stack:**

   ```bash
   docker compose up -d
   ```

4. **Verify health:**

   ```bash
   docker compose ps
   ```

   All three services should show a `healthy` status once fully started.

## Connecting

| Database | Connection String |
|---|---|
| PostgreSQL (via PgBouncer) | `postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@localhost:5432/<POSTGRES_DB>` |
| Neo4j (Bolt) | `bolt://localhost:7687` (auth: `<NEO4J_USER>/<NEO4J_PASSWORD>`) |
| Neo4j Browser | `http://localhost:7474` |

## Configuration

### PostgreSQL Tuning

Key runtime parameters are set in `docker-compose.yml` under the `postgres` service `command` section:

**Connections & Memory:**
- `max_connections=100`
- `shared_buffers=256MB`, `effective_cache_size=1024MB`, `work_mem=2MB`
- `maintenance_work_mem=64MB`
- `huge_pages=try`
- `shm_size: 128mb` (Docker shared memory)

**WAL & Checkpoints:**
- `wal_level=replica`, `wal_buffers=16MB`
- `min_wal_size=512MB`, `max_wal_size=2GB`
- `checkpoint_completion_target=0.9`

**I/O & Planner:**
- `random_page_cost=1.1`, `effective_io_concurrency=200`
- `default_statistics_target=100`

**Logging:**
- `log_destination=stderr`, `logging_collector=off`
- Slow query logging at 500 ms (`log_min_duration_statement=500`)
- `log_checkpoints=on`, `log_lock_waits=on`, `log_statement=ddl`, `log_disconnections=on`

**Security & Timeouts:**
- `password_encryption=scram-sha-256`
- Statement timeout: 5 minutes (`statement_timeout=300000`)
- Idle-in-transaction timeout: 60 seconds (`idle_in_transaction_session_timeout=60000`)
- `deadlock_timeout=1s`

### PgBouncer

Configured via environment variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `PGBOUNCER_AUTH_TYPE` | `scram-sha-256` | Authentication method |
| `PGBOUNCER_POOL_MODE` | `transaction` | Pooling mode |
| `PGBOUNCER_MAX_CLIENT_CONN` | `200` | Maximum client connections |
| `PGBOUNCER_DEFAULT_POOL_SIZE` | `20` | Default pool size per user/db |
| `PGBOUNCER_MIN_POOL_SIZE` | `5` | Minimum pool size |
| `PGBOUNCER_RESERVE_POOL_SIZE` | `5` | Reserve connections for bursts |
| `PGBOUNCER_RESERVE_POOL_TIMEOUT` | `3` | Seconds before using reserve pool |
| `PGBOUNCER_SERVER_IDLE_TIMEOUT` | `600` | Close idle server connections after (seconds) |
| `PGBOUNCER_CLIENT_IDLE_TIMEOUT` | `1800` | Close idle client connections after (seconds) |
| `PGBOUNCER_LOG_CONNECTIONS` | `1` | Log new connections |
| `PGBOUNCER_LOG_DISCONNECTIONS` | `1` | Log disconnections |
| `PGBOUNCER_LOG_STATS` | `1` | Log periodic stats |
| `PGBOUNCER_STATS_PERIOD` | `60` | Stats logging interval (seconds) |

### Neo4j

**Memory** (tuned for 8 GB VM):
- Heap: 512 MB initial and max (`heap_initial_size`, `heap_max_size`)
- Page cache: 512 MB (`pagecache_size`)
- Estimated total: ~1.4 GB (heap + pagecache + JVM overhead)

**Transactions:**
- Max concurrent transactions: 8
- Transaction memory total max: 256 MB
- Transaction timeout: 5 minutes

**Plugins & Procedures:**
- APOC plugin enabled (`apoc.*` unrestricted)
- Graph Data Science plugin enabled (`gds*` unrestricted)
- APOC file import/export disabled for security

**JVM:**
- G1GC with `MaxGCPauseMillis=200`
- `+ExitOnOutOfMemoryError` for clean crash recovery

**OS Tuning:**
- `ulimits.nofile`: 40000 (soft and hard)

## Environment Variables

All variables are defined in `.env` (copy from `.env.example`):

```
# ─── PostgreSQL ───
POSTGRES_DB=your_postgres_db
POSTGRES_USER=your_postgres_user
POSTGRES_PASSWORD=your_postgres_password

# ─── PgBouncer ───
PGBOUNCER_AUTH_TYPE=scram-sha-256
PGBOUNCER_POOL_MODE=transaction
PGBOUNCER_MAX_CLIENT_CONN=200
PGBOUNCER_DEFAULT_POOL_SIZE=20
PGBOUNCER_MIN_POOL_SIZE=5
PGBOUNCER_RESERVE_POOL_SIZE=5
PGBOUNCER_RESERVE_POOL_TIMEOUT=3
PGBOUNCER_SERVER_IDLE_TIMEOUT=600
PGBOUNCER_CLIENT_IDLE_TIMEOUT=1800
PGBOUNCER_LOG_CONNECTIONS=1
PGBOUNCER_LOG_DISCONNECTIONS=1
PGBOUNCER_LOG_STATS=1
PGBOUNCER_STATS_PERIOD=60

# ─── Neo4j ───
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=your_neo4j_user
NEO4J_PASSWORD=your_neo4j_password
```

## Data Volumes

Persistent data is stored in `./docker_volume/`:

```
docker_volume/
  postgres/data/   # PostgreSQL data directory (PGDATA)
  postgres/logs/   # PostgreSQL logs
  neo4j/data/      # Neo4j data
  neo4j/logs/      # Neo4j logs
  neo4j/import/    # Neo4j import directory
  neo4j/plugins/   # Neo4j plugins
```

This directory is git-ignored.

## Resource Limits

| Service | Memory Limit | Memory Reserved | CPU Limit | CPU Reserved |
|---|---|---|---|---|
| PostgreSQL | 1.5 GB | 512 MB | 2.0 | 0.5 |
| PgBouncer | 128 MB | 32 MB | 0.5 | 0.1 |
| Neo4j | 2 GB | 768 MB | 2.0 | 1.0 |

## Health Checks

| Service | Method | Interval | Timeout | Retries | Start Period |
|---|---|---|---|---|---|
| PostgreSQL | `pg_isready` | 10s | 5s | 5 | 30s |
| PgBouncer | `pg_isready` | 10s | 5s | 5 | 10s |
| Neo4j | HTTP `wget` to `:7474` | 30s | 10s | 5 | 60s |

## Stopping

```bash
docker compose down
```

To also remove volumes:

```bash
docker compose down -v
```
