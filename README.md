# MTI Brain

**MTI Brain** is an AI-powered conversational data analytics platform. Users ask natural-language questions and receive structured answers with data tables, charts, and follow-up suggestions streamed in real time via Server-Sent Events.

Built on a FastAPI backend, Next.js frontend, and a PostgreSQL data layer with pgvector for semantic search.

## Architecture

```
                       Browser
                          |
                          | HTTP / SSE
                          v
                  +-----------------+
                  | nginx           |
                  | :80 / :443      |
                  +--------+--------+
                           |
              +------------+------------+
              v                         v
     +-----------------+      +-------------------+
     | Next.js         | REST | FastAPI Backend   |
     | Frontend :3000  |<---->| :8000 (SSE)       |
     +-----------------+      +---------+---------+
                                        |
                                        v
                              +-------------------+
                              | PgBouncer         |
                              | (transaction mode)|
                              +---------+---------+
                                        |
                                        v
                              +-------------------+
                              | PostgreSQL 18     |
                              | + pgvector        |
                              | + pg_trgm         |
                              | + fuzzystrmatch   |
                              +-------------------+
                              database/
```

The frontend authenticates via username/password, receives a JWT from the backend, and uses that token for all subsequent API calls. The backend stores conversation threads, messages, projects, and feedback in PostgreSQL via PgBouncer. nginx terminates TLS and routes traffic to frontend and backend.

> **Auth migration:** Okta OIDC migration is in progress — the `MTIBrainUser` model already carries `okta_id`, but `app/services/auth.py` and the frontend `/auth/callback` page are still on the legacy username/password flow. Plan around this when wiring the SSO IdP.

## Features

- **Conversation Management** - threads, projects, starring, renaming, bulk operations
- **Real-Time Streaming** - SSE-based streaming of answers and pipeline progress
- **JWT Authentication** - username/password login with 8-hour JWT session tokens and per-user data isolation
- **Smart Search** - full-text (tsvector), fuzzy (trigram), and phonetic (dmetaphone) search over threads and messages
- **Feedback Loop** - thumbs-up/down feedback with pgvector embeddings for future retrieval-augmented generation
- **Data Visualization** - auto-generated charts (recharts), paginated data tables, SQL display
- **User Preferences** - per-user response tone, SQL/chart/reasoning visibility, persisted to localStorage
- **Keyboard Shortcuts** - power-user shortcuts for navigation, starring, search, copy, and more
- **Circuit Breakers** - resilient external service calls with graceful degradation

## Project Structure

```
quest/
├── docker-compose.yml   # Root orchestration: backend + frontend + nginx (joins external db_net)
├── appspec.yml          # AWS CodeDeploy spec
├── nginx/               # Reverse-proxy config (TLS termination, /api routing)
├── deploy/              # Deployment configs (see deploy/README.md)
│
├── backend/      # FastAPI backend (see backend/README.md)
│   ├── app/
│   │   ├── main.py       # FastAPI entry point, lifespan, middleware wiring
│   │   ├── api/          # Route handlers: health, auth, chat, projects
│   │   ├── core/         # Config, logging, middleware, circuit breakers, rate limiter
│   │   ├── db/           # SQLAlchemy async session factory
│   │   ├── models/       # ORM: User, Project, Thread, Message, Feedback, ExecutionLog
│   │   ├── schemas/      # Pydantic request/response schemas
│   │   └── services/     # Auth, conversation CRUD, feedback, health, sql_analysis (trust strip)
│   ├── alembic/          # Single baseline migration (creates extensions + mti_brain_* tables + triggers)
│   ├── scripts/          # Utility scripts
│   ├── Dockerfile        # Multi-stage Python 3.12 build (includes MSSQL ODBC drivers)
│   └── .env.example
│
├── frontend/     # Next.js 16 application (see frontend/README.md)
│   ├── app/              # App Router (login, /new, /chat, /chats, /projects, /starred, /settings)
│   ├── components/       # 40+ React components (sidebar, composer, messages, dialogs, charts, trust strip, install prompt)
│   ├── lib/              # API client, SSE parser, 11 Zustand stores, auth, analytics, types
│   ├── hooks/            # Keyboard shortcuts, mobile detection
│   ├── public/           # Static assets + service worker (sw.js) for PWA install
│   ├── Dockerfile        # Multi-stage Node 20 build (standalone output)
│   └── .env.example
│
└── database/     # Data layer Docker Compose (see database/README.md)
    ├── docker-compose.yml  # PostgreSQL 18 + PgBouncer (publishes db_net)
    ├── docker_volume/      # Persistent data (git-ignored)
    └── .env.example
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19 (with React Compiler), TypeScript, Tailwind CSS 4, shadcn/ui, Zustand |
| Frontend extras | PostHog analytics, Dexie (IndexedDB) for composer drafts, Framer Motion, @tanstack/react-virtual, react-hotkeys-hook |
| Backend | FastAPI + Gunicorn + Uvicorn (Python 3.12) |
| Auth | Username/password → JWT (PyJWT, HS256, 8-hour expiry). **Okta OIDC migration planned** |
| App Database | PostgreSQL 18 + pgvector + SQLAlchemy (async) + Alembic |
| Postgres extensions | `pgvector`, `pg_trgm`, `fuzzystrmatch` (installed by the baseline migration) |
| Connection Pooling | PgBouncer (transaction mode) |
| Vector Search | pgvector (1536-dim) on feedback embeddings |
| Full-Text Search | tsvector + GIN + pg_trgm trigram + fuzzystrmatch (Levenshtein) |
| Streaming | SSE (sse-starlette) |
| Resilience | Circuit breakers (pybreaker) + retries (tenacity) |
| Reverse proxy | nginx 1.27-alpine (TLS termination, port 80/443) |
| Containerization | Docker + Docker Compose (root + database compose files) |

## API

All endpoints except `/health` and `POST /api/v1/auth/login` require `Authorization: Bearer <token>`.

### Auth (`/api/v1/auth`)

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/login` | Exchange username + password for JWT |
| GET | `/me` | Current user profile |

### Chat (`/api/v1/chat`)

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/new` | Create a new conversation thread |
| GET | `/recents` | List / search recent threads |
| POST | `/bulk/delete` | Bulk delete threads |
| POST | `/bulk/move` | Bulk move threads to a project |
| GET | `/{thread_id}` | Get thread with all messages |
| DELETE | `/{thread_id}` | Delete thread |
| PATCH | `/{thread_id}/star` | Toggle star |
| PATCH | `/{thread_id}/rename` | Rename thread |
| PATCH | `/{thread_id}/move` | Move thread to a project |
| POST | `/{thread_id}/ask` | Ask a question (SSE streaming) |
| POST | `/{thread_id}/retry` | Retry last response (SSE streaming) |
| POST | `/{thread_id}/edit` | Edit last question (SSE streaming) |
| POST | `/{thread_id}/stop` | Stop active stream |
| POST | `/{thread_id}/conversations/{id}/feedback` | Submit feedback |

### Projects (`/api/v1/projects`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | List all projects |
| POST | `/create` | Create a project |
| GET | `/{project_id}` | Get project with threads |
| PUT | `/{project_id}` | Update project |
| DELETE | `/{project_id}` | Delete project |
| PATCH | `/{project_id}/star` | Toggle project star |

### Health

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/health` | Postgres health check (200 healthy / 503 unhealthy) |

## Prerequisites

- Docker and Docker Compose
- (Path B only) Python 3.12+, Node.js 20+, and PostgreSQL 15+ already running with `pgvector`, `pg_trgm`, `fuzzystrmatch` available to the migrating role

## Getting Started

There are two operating modes — pick one.

### Path A — Full Docker Compose (recommended)

This brings up everything the app needs: nginx, frontend, backend, PgBouncer, and Postgres. The root [docker-compose.yml](docker-compose.yml) covers nginx + frontend + backend; [database/docker-compose.yml](database/docker-compose.yml) covers PgBouncer + Postgres.

```bash
git clone <repo-url>
cd quest

# 1. Database layer (PgBouncer + Postgres on the shared db_net network)
cd database
cp .env.example .env
# Edit .env: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
docker compose up -d
cd ..

# 2. Root .env for backend secrets and orchestration
cp backend/.env.example .env
# Edit .env: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, JWT_SECRET (mandatory)
# (Note: POSTGRES_HOST is overridden to "pgbouncer" by docker-compose.yml)

# 3. Bring up nginx + frontend + backend
docker compose up -d

# 4. Run migrations once (Path A doesn't run them automatically on container start)
docker compose run --rm backend alembic upgrade head
```

Open `http://localhost` (nginx, port 80) and log in with `admin` / `admin123`.

> **Security:** Default credentials are hardcoded for development only. Change them before any shared or production deployment.

### Path B — Manual / active development

Use this when you want hot-reload on the backend or are iterating fast on the frontend.

#### 1. Start the database layer

```bash
docker network create db_net
cd database
cp .env.example .env
# Edit .env: set POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
docker compose up -d
cd ..
```

Wait for both services to report `healthy` (`docker compose ps`).

#### 2. Configure the backend

```bash
cp backend/.env.example backend/.env
# Edit backend/.env (required):
#   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_DB, JWT_SECRET
#   CORS_ORIGINS must include the frontend origin (e.g. ["http://localhost:3000"])
```

#### 3. Configure the frontend

```bash
cp frontend/.env.example frontend/.env
# Edit frontend/.env: NEXT_PUBLIC_API_URL (e.g. http://localhost:8000)
```

#### 4. Run database migrations

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
cd ..
```

The single baseline migration ([backend/alembic/versions/0001_baseline.py](backend/alembic/versions/0001_baseline.py)) installs `pg_trgm`, `fuzzystrmatch`, and `vector` extensions, creates all `mti_brain_*` tables, and installs the `search_vector` triggers. The migrating role needs `CREATE EXTENSION` privilege; if it doesn't, install the three extensions once as a superuser before running alembic — the migration's `IF NOT EXISTS` guards make this safe.

#### 5. Start the services

**Backend:**
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend (separate terminal):**
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` and log in with username `admin` / password `admin123`.

### Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","services":{"postgres":{"status":"ok"}},...}
```

## Services Overview

| Service | Port | Stack | Description |
|---------|------|-------|-------------|
| **nginx** | 80 / 443 | root `nginx/` | Reverse proxy + TLS termination (Path A only) |
| **Frontend** | 3000 | `frontend/` | Next.js UI - login, chat, projects, settings, starred |
| **Backend** | 8000 | `backend/` | FastAPI REST + SSE streaming |
| **PgBouncer** | 5432 | `database/` | Connection pooler (transaction mode, SCRAM-SHA-256) |
| **PostgreSQL** | internal | `database/` | App database (pgvector, conversations, users) |

## Environment Variables

See each component for the full variable reference:

- [backend/.env.example](backend/.env.example) - App DB connection, JWT secret, CORS, connection pool, circuit breaker, SSL
- [database/.env.example](database/.env.example) - PostgreSQL credentials, PgBouncer tuning
- [frontend/.env.example](frontend/.env.example) - Backend API URL, dev HMR origins, optional PostHog keys

For Path A, the root [docker-compose.yml](docker-compose.yml) also reads `.env` at the repo root for orchestration overrides (`EC2_PUBLIC_IP`, `ENVIRONMENT`, `LOG_LEVEL`, etc.).

## Component READMEs

- [backend/README.md](backend/README.md) - API endpoints, database models, middleware, migrations, Dockerfile, env vars
- [frontend/README.md](frontend/README.md) - Routes, components, state management, auth flow, SSE streaming, preferences, keyboard shortcuts
- [database/README.md](database/README.md) - PostgreSQL tuning, PgBouncer config, volumes, health checks, resource limits
- [deploy/README.md](deploy/README.md) - Deployment configs (CodeDeploy / EC2)
