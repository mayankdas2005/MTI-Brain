# MTI Brain Database

Docker Compose stack providing the data layer for MTI Brain.

## Services

| Service | Image | Exposed Port | Purpose |
|---|---|---|---|
| **PostgreSQL** | `pgvector/pgvector:0.8.1-pg18` | None (internal only) | Primary relational store with pgvector extension for vector similarity search |
| **PgBouncer** | `edoburu/pgbouncer:v1.25.1-p0` | `5432` | Connection pooler in front of PostgreSQL (transaction pooling mode). Depends on PostgreSQL being healthy. |
| **Redis** | `redis:8.4-alpine` | `6379` | In-memory store for caching, rate limiting, session data, and Celery/task queues |

All services communicate via the `db_net` bridge network. PostgreSQL is internal-only; PgBouncer and Redis are accessible from outside on their respective ports.

## Prerequisites

- Docker and Docker Compose
- The external `db_net` bridge network must exist before starting the stack:

  ```bash
  docker network create db_net
  ```

## Getting Started

1. **Create your environment file:**

   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** and set real values for:

   - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

3. **Populate PgBouncer config files** (required before first start):

   - `./docker_volume/pgbouncer/pgbouncer.ini`
   - `./docker_volume/pgbouncer/userlist.txt`

4. **Start the stack:**

   ```bash
   docker compose up -d
   ```

5. **Verify health:**

   ```bash
   docker compose ps
   ```

   Both services should show a `healthy` status once fully started.

## Connecting

| Database | Connection String |
|---|---|
| PostgreSQL (via PgBouncer) | `postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@localhost:5432/<POSTGRES_DB>` |

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

**Pool mode: `transaction`** — required for compatibility with SQLAlchemy's `autobegin=False` read sessions and the LangGraph checkpointer.

In transaction mode, a Postgres server connection is only held for the duration of a single transaction (or a single statement when `autobegin=False`). This allows many more concurrent clients than session mode, which holds a server connection for the entire client lifetime.

#### Sizing (4-5 devs + 20+ testers)

```
25 users × 5 concurrent API calls = 125 peak concurrent requests

With autobegin=False on reads, each SELECT holds a server connection
for ~1-10 ms (query execution only). Effective concurrency need: ~5-10
simultaneous server connections even under full load.

Budget against postgres max_connections=100:
  DEFAULT_POOL_SIZE (app):    40
  RESERVE_POOL_SIZE:          10
  LangGraph checkpointer:     10
  Postgres internal:           5
  Total:                      65 < 100 ✓
```

#### LangGraph checkpointer incompatibilities

LangGraph's `AsyncPostgresSaver` must use a **separate asyncpg pool** — not the SQLAlchemy engine. Configure it with:
- `prepared_statement_cache_size=0` — prepared statements are not supported in transaction pooling mode
- `statement_cache_size=0` — same reason

See `backend/app/db/session.py → get_langgraph_dsn()` for the connection string and a usage example.

| Variable | Default | Description |
|---|---|---|
| `PGBOUNCER_AUTH_TYPE` | `scram-sha-256` | Authentication method |
| `PGBOUNCER_POOL_MODE` | `transaction` | Pooling mode — do not change |
| `PGBOUNCER_MAX_CLIENT_CONN` | `500` | Maximum client connections (SQLAlchemy sockets + LangGraph + headroom) |
| `PGBOUNCER_DEFAULT_POOL_SIZE` | `40` | Server connections held per user/db pair |
| `PGBOUNCER_MIN_POOL_SIZE` | `10` | Connections kept warm at all times |
| `PGBOUNCER_RESERVE_POOL_SIZE` | `10` | Extra connections available during bursts |
| `PGBOUNCER_RESERVE_POOL_TIMEOUT` | `2` | Seconds before using reserve pool |
| `PGBOUNCER_SERVER_IDLE_TIMEOUT` | `600` | Close idle server connections after (seconds) — SQLAlchemy `pool_recycle` must be < this |
| `PGBOUNCER_CLIENT_IDLE_TIMEOUT` | `1800` | Close idle client connections after (seconds) |
| `PGBOUNCER_LOG_CONNECTIONS` | `0` | Disabled in prod — noisy at scale |
| `PGBOUNCER_LOG_DISCONNECTIONS` | `0` | Disabled in prod |
| `PGBOUNCER_LOG_STATS` | `1` | Log periodic stats |
| `PGBOUNCER_STATS_PERIOD` | `60` | Stats logging interval (seconds) |

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
```

## Data Volumes

Persistent data is stored in `./docker_volume/`:

```
docker_volume/
  postgres/data/          # PostgreSQL data directory (PGDATA)
  postgres/logs/          # PostgreSQL logs
  pgbouncer/pgbouncer.ini # PgBouncer config file (must exist before first start)
  pgbouncer/userlist.txt  # PgBouncer auth file (must exist before first start)
```

This directory is git-ignored.

## Redis

Password-authenticated via `--requirepass`. No anonymous connections.

**Persistence:** both RDB snapshots (900s/1 key, 300s/10 keys, 60s/10 000 keys) and AOF (`appendfsync everysec`) are enabled. Data survives container restarts via `./docker_volume/redis/data`.

**Eviction:** `allkeys-lru` — Redis evicts the least-recently-used keys when `maxmemory` is reached. Suitable for caching workloads where stale data expiring is acceptable.

**Connection string:** `redis://:${REDIS_PASSWORD}@<host>:6379/0`

| Variable | Default | Description |
|---|---|---|
| `REDIS_PASSWORD` | — | Required. Authentication password |
| `REDIS_PORT` | `6379` | Host port mapped to container port 6379 |
| `REDIS_MAXMEMORY` | `256mb` | Hard memory cap for Redis data |
| `REDIS_MAXMEMORY_POLICY` | `allkeys-lru` | Eviction policy when `maxmemory` is reached |

## Resource Limits

| Service | Memory Limit | Memory Reserved | CPU Limit | CPU Reserved |
|---|---|---|---|---|
| PostgreSQL | 1.5 GB | 512 MB | 2.0 | 0.5 |
| PgBouncer | 128 MB | 32 MB | 0.5 | 0.1 |
| Redis | 384 MB | 64 MB | 0.5 | 0.1 |

## Health Checks

| Service | Method | Interval | Timeout | Retries | Start Period |
|---|---|---|---|---|---|
| PostgreSQL | `pg_isready` | 10s | 5s | 5 | 30s |
| PgBouncer | `pg_isready` | 10s | 5s | 5 | 10s |

## Stopping

```bash
docker compose down
```

To also remove volumes:

```bash
docker compose down -v
```

---

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI) | [../backend/README.md](../backend/README.md) |
| Frontend (Next.js) | [../frontend/README.md](../frontend/README.md) |
| Deployment (AWS CodeDeploy) | [../deploy/README.md](../deploy/README.md) |
