# MTI Brain Backend

FastAPI backend for **MTI Brain** — an AI-powered conversational data analytics platform. Provides JWT-authenticated REST APIs for user login, conversation thread management, and project organization, with Server-Sent Events (SSE) streaming support for real-time responses.

The backend uses an async SQLAlchemy stack against PostgreSQL (with pgvector for embeddings and full-text search) and is designed to front a pluggable NL-to-SQL agent pipeline (planned: LangGraph + AWS Bedrock + Neo4j knowledge graph).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI + Uvicorn / Gunicorn |
| Language | Python 3.12 |
| App Database | PostgreSQL + SQLAlchemy (async) + Alembic |
| Connection Driver | asyncpg |
| Vector Search | pgvector (1536-dim) on feedback embeddings |
| Full-Text Search | PostgreSQL `tsvector` + GIN + `pg_trgm` + `dmetaphone` |
| Streaming | SSE via sse-starlette |
| Auth | Username/password → JWT (PyJWT, HS256, 8-hour expiry) |
| Resilience | Circuit breakers (pybreaker) + retries (tenacity) |
| Logging | Loguru (structured, with request-ID and timing context) |
| Containerization | Docker (multi-stage, non-root, Python 3.12-slim) |
| **Planned** | LangGraph agent pipeline, AWS Bedrock (Claude Sonnet + Cohere Embed), Neo4j knowledge graph, Redis cache, Okta OIDC, Langfuse observability |

## Project Structure

```
backend/
├── app/
│   ├── main.py                  # FastAPI entry point + lifespan (pool warmup / dispose)
│   ├── api/
│   │   ├── health.py            # GET /health
│   │   └── v1/
│   │       ├── __init__.py      # v1 router (/api/v1)
│   │       ├── auth.py          # POST /auth/login, GET /auth/me
│   │       ├── chat.py          # Thread management + SSE streaming
│   │       ├── project.py       # Project CRUD
│   │       └── deps.py          # CurrentUser dependency (JWT validation)
│   ├── core/
│   │   ├── config.py            # Pydantic Settings (all env vars)
│   │   ├── logger.py            # Loguru configuration
│   │   ├── middleware.py        # RequestIDMiddleware + TimingMiddleware
│   │   └── circuit_breaker.py   # pybreaker instances for external services
│   ├── db/
│   │   ├── session.py           # SQLAlchemy async engine + session factory + pool warmup
│   │   └── base.py              # Declarative ORM base
│   ├── models/
│   │   ├── conversation.py      # QuestProject / QuestThread / QuestMessage / QuestFeedback
│   │   ├── user.py              # QuestUser
│   │   └── execution_log.py     # QuestExecutionLog (per-run telemetry)
│   ├── schemas/
│   │   ├── chat.py              # Pydantic request/response schemas for chat
│   │   └── project.py           # Pydantic schemas for projects
│   └── services/
│       ├── auth.py              # Credential validation + JWT issue/decode + user upsert
│       ├── conversation.py      # Thread/message/project CRUD + 3-layer search
│       ├── feedback.py          # Feedback storage + pgvector similarity
│       └── health/
│           └── service.py       # Circuit-breaker-protected Postgres health check
├── alembic/                     # Database migrations (alembic upgrade head)
├── scripts/
│   └── render_graph.py          # Utility: export pipeline graph diagram
├── requirements.txt
├── Dockerfile
├── alembic.ini
└── .env.example
```

## API Endpoints

All endpoints except `/health` and `POST /api/v1/auth/login` require a valid JWT in the `Authorization: Bearer <token>` header.

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Returns `200 healthy` or `503 unhealthy` based on Postgres status |

**Response:**
```json
{
  "status": "healthy",
  "services": {
    "postgres": { "status": "ok" }
  },
  "timestamp": "2026-04-29T12:00:00Z"
}
```

`status` is `"healthy"`, `"degraded"` (circuit open), or `"unhealthy"` (Postgres unreachable → HTTP 503).

### Auth (`/api/v1/auth`)

| Method | Path | Auth required | Description |
|--------|------|---------------|-------------|
| POST | `/login` | No | Exchange username + password for JWT |
| GET | `/me` | Yes | Current user profile |

**Login request:**
```json
{ "username": "admin", "password": "admin123" }
```

**Login response:**
```json
{
  "token": "eyJ0eXAiOiJKV1Qi...",
  "user": {
    "user_id": "uuid",
    "email": "admin@milestone.tech",
    "name": "Admin User",
    "groups": []
  }
}
```

> **Development note:** Credentials are currently hardcoded in `app/services/auth.py`. This is intentionally dev-only — do not deploy with the default credentials in production.

### Chat (`/api/v1/chat`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/new` | Create a new conversation thread |
| GET | `/recents` | List / search recent threads (`search`, `project_id`, `limit`, `offset`) |
| POST | `/bulk/delete` | Bulk delete threads |
| POST | `/bulk/move` | Bulk move threads to a project |
| GET | `/{thread_id}` | Get thread with all messages |
| DELETE | `/{thread_id}` | Delete a thread |
| PATCH | `/{thread_id}/star` | Toggle thread star |
| PATCH | `/{thread_id}/rename` | Rename thread |
| PATCH | `/{thread_id}/move` | Move thread to a project |
| POST | `/{thread_id}/conversations/{conversation_id}/feedback` | Submit thumbs-up/down + comment |

### Projects (`/api/v1/projects`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List all projects (`search` query param) |
| POST | `/create` | Create a project |
| GET | `/{project_id}` | Get project with its threads |
| PUT | `/{project_id}` | Update name / description |
| DELETE | `/{project_id}` | Delete project (threads are unlinked, not deleted) |
| PATCH | `/{project_id}/star` | Toggle project star |

### API Docs

Available at runtime:

| URL | Description |
|-----|-------------|
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/openapi.json` | OpenAPI spec |

## Database Models

All tables live in the app PostgreSQL database.

| Model | Table | Purpose |
|-------|-------|---------|
| **QuestUser** | `quest_user` | User record keyed by email. Fields: `id`, `okta_id`, `email`, `name`, `groups` (JSONB), `organization`, `last_login`, `created_at`. |
| **QuestProject** | `quest_project` | Named collection of threads. Fields: `id`, `user_id`, `name`, `description`, `starred`, timestamps. |
| **QuestThread** | `quest_thread` | Conversation thread (= LangGraph `thread_id`). Fields: `id`, `user_id`, `project_id`, `title`, `starred`, `search_vector` (tsvector GIN). |
| **QuestMessage** | `quest_message` | Individual user or assistant message. Fields: `id`, `thread_id`, `conversation_id`, `parent_conversation_id`, `role`, `content`, `reasoning`, `metadata` (JSONB: `sql`, `chart_spec`, `intent`, `columns`, `rows`, `follow_ups`, etc.), `search_vector`. |
| **QuestFeedback** | `quest_feedback` | Thumbs-up/down + optional comment. Fields: `id`, `message_id`, `thread_id`, `liked`, `comment`, `embedding` (Vector 1536 — pgvector). |
| **QuestExecutionLog** | `quest_execution_log` | Per-run telemetry. Fields: `question`, `question_type`, `schema_fqn`, `sql`, `row_count`, `retry_count`, `valid`, `exec_error`, `duration_ms`, `pattern_matched`, implicit/explicit feedback flags, user context. |

## Middleware

| Middleware | Purpose |
|-----------|---------|
| **RequestIDMiddleware** | Generates and attaches `X-Request-ID` to every request/response |
| **TimingMiddleware** | Logs request duration; attaches `X-Response-Time` header |
| **CORSMiddleware** | Origins controlled by `CORS_ORIGINS`; credentials enabled |
| **TrustedHostMiddleware** | Host header validation (wildcard in dev) |

## Resilience

- **Circuit breakers** (pybreaker) on Postgres health — prevents cascading failures when the DB is unavailable.
- **Tenacity retries** available for transient external service errors.
- **Graceful pool warmup** — 3 connections pre-opened at startup; engine disposed cleanly on shutdown.
- **Pool pre-ping** disabled (aggressive recycle via `DB_POOL_RECYCLE` instead) to avoid checkout latency.

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL 15+ with `pgvector`, `pg_trgm`, `fuzzystrmatch` extensions
- (Optional) Docker and Docker Compose

### Local setup

```bash
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux / macOS

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — at minimum set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_DB, JWT_SECRET

# 4. Run database migrations
alembic upgrade head

# 5. Start the development server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker

The Dockerfile is a two-stage build (builder + runtime). The runtime image includes:
- **Microsoft ODBC Driver 17 and 18** for SQL Server (modern T-SQL datasources)
- **FreeTDS + pymssql** for legacy SQL Server 2008 / 2008 R2
- Custom OpenSSL config for TLS 1.0/1.1 support when required by older SQL Server instances
- Non-root `appuser` for container security

```bash
docker build -t mti-brain-backend .
docker run -p 8000:8000 --env-file .env mti-brain-backend
```

### Production

```bash
gunicorn app.main:app \
  --bind 0.0.0.0:8000 \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 \
  --timeout 180
```

## Environment Variables

Copy `.env.example` to `.env` and fill in the required values.

### Required

| Variable | Description |
|----------|-------------|
| `POSTGRES_USER` | App database username |
| `POSTGRES_PASSWORD` | App database password |
| `POSTGRES_HOST` | App database host |
| `POSTGRES_DB` | App database name |
| `JWT_SECRET` | Secret key for signing JWT tokens — **change in production** |
| `CORS_ORIGINS` | JSON array or comma-separated list of allowed frontend origins (e.g. `["http://localhost:3000"]`) |

### Optional / defaults

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DEBUG` | `false` | FastAPI debug mode |
| `POSTGRES_PORT` | `5432` | App database port |
| `DATABASE_SSL_MODE` | `disable` | `disable` / `require` / `verify-ca` / `verify-full` |
| `DATABASE_SSL_ROOT_CERT` | `""` | Path to SSL root certificate |
| `DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Max overflow connections above pool size |
| `DB_POOL_RECYCLE` | `1800` | Recycle connections after N seconds |
| `DB_POOL_TIMEOUT` | `30` | Timeout to acquire a connection (seconds) |
| `CB_FAIL_MAX` | `5` | Circuit breaker failures before opening |
| `CB_RESET_TIMEOUT` | `30` | Seconds before circuit half-opens |

