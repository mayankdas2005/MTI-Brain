# MTI Brain Database

Docker Compose stack providing the data layer for MTI Brain.

## Services

| Service | Image | Exposed Port | Purpose |
|---|---|---|---|
| **PostgreSQL** | `pgvector/pgvector:0.8.1-pg18` | None (internal only) | Primary relational store with pgvector extension for vector similarity search |
| **PgBouncer** | `edoburu/pgbouncer:v1.25.1-p0` | `5432` | Connection pooler in front of PostgreSQL (transaction pooling mode). Depends on PostgreSQL being healthy. |
| **Redis** | `redis:8.4-alpine` | `6379` | In-memory store for caching, rate limiting, session data, and Celery/task queues |
| **Neo4j** | `neo4j:2026.04-enterprise` | `7474` (HTTP), `7687` (Bolt) | Graph database with APOC and Graph Data Science plugins for semantic graph workloads |

All services communicate via the `db_net` bridge network. PostgreSQL is internal-only; PgBouncer, Redis, and Neo4j are accessible from outside on their respective ports.

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
   - `NEO4J_USER` (must be `neo4j`), `NEO4J_PASSWORD`
   - `REDIS_PASSWORD`

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

   All services should show a `healthy` status once fully started. Neo4j takes up to 2 minutes on first boot (plugin installation).

## Connecting

| Database | Connection String |
|---|---|
| PostgreSQL (via PgBouncer) | `postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@localhost:5432/<POSTGRES_DB>` |
| Neo4j (Bolt) | `bolt://localhost:7687` — auth `neo4j` / `<NEO4J_PASSWORD>` |
| Neo4j (Browser) | `http://localhost:7474` |
| Redis | `redis://:${REDIS_PASSWORD}@localhost:6379/0` |

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

### Redis

Password-authenticated via `--requirepass`. No anonymous connections.

**Persistence:** both RDB snapshots (900s/1 key, 300s/10 keys, 60s/10 000 keys) and AOF (`appendfsync everysec`) are enabled. Data survives container restarts via `./docker_volume/redis/data`.

**Eviction:** controlled by `REDIS_MAXMEMORY_POLICY`. Set to `noeviction` by default — Redis returns an error rather than silently dropping data when `maxmemory` is reached. Use `allkeys-lru` if Redis is used purely as a cache where eviction is acceptable.

| Variable | Default | Description |
|---|---|---|
| `REDIS_PASSWORD` | — | Required. Authentication password |
| `REDIS_PORT` | `6379` | Host port mapped to container port 6379 |
| `REDIS_MAXMEMORY` | `256mb` | Hard memory cap for Redis data |
| `REDIS_MAXMEMORY_POLICY` | `noeviction` | Eviction policy when `maxmemory` is reached |

### Neo4j

Enterprise edition with APOC and Graph Data Science (GDS) plugins. Plugins are installed automatically on first start from the bundled jars — no manual download needed.

**Important:** `NEO4J_USER` must be `neo4j`. Neo4j's `NEO4J_AUTH` env var only sets the password for the built-in `neo4j` admin; the username cannot be changed via environment variable.

**`env_file` is intentionally omitted** from the Neo4j service. Neo4j treats every `NEO4J_*` environment variable as a configuration key, so passing the full `.env` file would leak `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` as spurious config entries. Only the vars listed in the `environment:` block are passed.

#### Memory budget

| Component | Size |
|---|---|
| Heap (initial + max) | 3 GB |
| Pagecache | 1.5 GB |
| JVM overhead | ~512 MB |
| **Total** | **~5 GB** (container limit: 6 GB) |

Sized for GDS algorithms (FastRP, K-NN, Louvain, PageRank) on ~320 nodes without OOM. If GDS projections still OOM, raise the container memory limit and increase heap/pagecache proportionally.

#### JVM tuning

G1GC configured for a 3 GB heap:
- `G1HeapRegionSize=16m` — right-sizes regions for the heap
- `InitiatingHeapOccupancyPercent=35` — triggers concurrent GC earlier to reduce stop-the-world pauses
- `ParallelRefProcEnabled` — parallel reference processing during GC
- GC logs rotate at 5 × 20 MB files in `/logs/gc.log`
- Heap dump written to `/logs/` on OOM

#### Key settings

| Setting | Value | Notes |
|---|---|---|
| `server.memory.heap.max_size` | `3g` | Fixed heap (initial = max avoids resize pauses) |
| `server.memory.pagecache.size` | `1500m` | Graph data cache |
| `db.transaction.concurrent.maximum` | `16` | Max concurrent transactions |
| `dbms.memory.transaction.total.max` | `1500m` | Total transaction memory across all tx |
| `db.memory.transaction.max` | `768m` | Per-transaction memory ceiling |
| `db.transaction.timeout` | `30m` | GDS algorithm timeout |
| `db.lock.acquisition.timeout` | `30s` | Prevents deadlock starvation |
| `server.bolt.thread_pool_min_size` | `10` | Bolt min threads |
| `server.bolt.thread_pool_max_size` | `40` | Bolt max threads — tune to concurrent client count |
| `server.config.strict_validation.enabled` | `false` | Allows unknown settings to warn rather than hard-fail |

| Variable | Description |
|---|---|
| `NEO4J_USER` | Must be `neo4j` |
| `NEO4J_PASSWORD` | Admin password |
| `NEO4J_URI` | Bolt URI for app connections (e.g. `bolt://neo4j:7687`) |

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

# ─── Redis ───
REDIS_PASSWORD=your_redis_password
REDIS_PORT=6379
REDIS_MAXMEMORY=256mb
REDIS_MAXMEMORY_POLICY=noeviction

# ─── Neo4j ───
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password
```

## Data Volumes

Persistent data is stored in `./docker_volume/`:

```
docker_volume/
  postgres/data/          # PostgreSQL data directory (PGDATA)
  postgres/logs/          # PostgreSQL logs
  pgbouncer/pgbouncer.ini # PgBouncer config file (must exist before first start)
  pgbouncer/userlist.txt  # PgBouncer auth file (must exist before first start)
  redis/data/             # Redis RDB + AOF persistence
  neo4j/data/             # Neo4j graph data
  neo4j/logs/             # Neo4j logs and GC log (gc.log)
  neo4j/import/           # CSV/files for LOAD CSV imports
  neo4j/plugins/          # Installed plugin jars (apoc, graph-data-science)
```

This directory is git-ignored.

## Resource Limits

| Service | Memory Limit | Memory Reserved | CPU Limit | CPU Reserved |
|---|---|---|---|---|
| PostgreSQL | 1.5 GB | 512 MB | 2.0 | 0.5 |
| PgBouncer | 128 MB | 32 MB | 0.5 | 0.1 |
| Redis | 384 MB | 64 MB | 0.5 | 0.1 |
| Neo4j | 6 GB | 2 GB | 4.0 | 1.0 |

## Health Checks

| Service | Method | Interval | Timeout | Retries | Start Period |
|---|---|---|---|---|---|
| PostgreSQL | `pg_isready` | 10s | 5s | 5 | 30s |
| PgBouncer | `pg_isready` | 10s | 5s | 5 | 10s |
| Redis | `redis-cli ping` | 10s | 5s | 5 | 10s |
| Neo4j | HTTP `/db/neo4j/cluster/available` | 30s | 10s | 5 | 120s |

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