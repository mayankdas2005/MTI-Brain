# MTI Brain

**MTI Brain** is an AI-powered conversational data analytics platform for enterprise treasury intelligence. Users ask natural-language questions and receive structured answers with data tables, charts, and follow-up suggestions streamed in real time via Server-Sent Events.

Built on a FastAPI backend, Next.js 16 frontend, and a PostgreSQL data layer with pgvector for semantic search. Fully responsive across desktop, tablet (iPad), and mobile (iPhone).

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

- **Conversation Management** — threads, projects, starring, renaming, bulk operations
- **Real-Time Streaming** — SSE-based streaming of answers and pipeline progress
- **Deep Analysis** — per-question toggle for extended multi-step reasoning (slower but more thorough); persists until the user turns it off
- **JWT Authentication** — username/password login with 8-hour JWT session tokens and per-user data isolation
- **Smart Search** — full-text (tsvector), fuzzy (trigram), and phonetic (dmetaphone) search over threads and messages
- **Feedback Loop** — thumbs-up/down feedback with pgvector embeddings for future retrieval-augmented generation
- **Data Visualization** — auto-generated charts (recharts), paginated data tables with sticky first column, SQL display
- **Dashboard Generation** — per-conversation HTML dashboards generated in the background and stored in S3; accessible via presigned URL
- **Playbook** — user-owned saved query templates; run them again from the sidebar without retyping
- **Pinned Metrics** — per-user metric cards pinned to the home page, each backed by a saved query
- **Thread Labels** — colored labels applied to threads for filtering and organization
- **Export** — download results as Excel (`.xlsx`) or PowerPoint (`.pptx`) directly from any answer
- **Knowledge Graph** — SPARQL queries against a Jena Fuseki endpoint; ontology-aware reasoning via rdflib
- **User Preferences** — per-user response tone (`analyst`, `manager`, `director`, `executive`), SQL/chart/reasoning visibility, persisted to localStorage
- **Fully Responsive** — mobile off-canvas sidebar, tablet icon-rail + overlay panel, desktop inline sidebar; safe-area-inset support for iOS
- **Keyboard Shortcuts** — power-user shortcuts for navigation, starring, search, copy, and more
- **PWA** — installable as a standalone app (Add to Home Screen on iOS/Android)
- **Circuit Breakers** — resilient external service calls with graceful degradation

## Project Structure

```
mti-brain/
├── docker-compose.yml   # Root orchestration: backend + frontend + nginx (joins external db_net)
├── appspec.yml          # AWS CodeDeploy spec
├── nginx/               # Reverse-proxy config (TLS termination, /api routing)
├── deploy/              # Deployment lifecycle hooks (see deploy/README.md)
│
├── backend/             # FastAPI backend (see backend/README.md)
│   ├── app/
│   │   ├── main.py       # FastAPI entry point, lifespan, middleware wiring
│   │   ├── api/          # Route handlers: health, auth, chat, projects, playbook, labels, pinned-metrics, dashboard
│   │   ├── core/         # Config, logging, middleware, circuit breakers, rate limiter
│   │   ├── db/           # SQLAlchemy async session factory
│   │   ├── models/       # ORM: User, Project, Thread, Message, Feedback, ExecutionLog, UserFeatures, Dashboard
│   │   ├── schemas/      # Pydantic request/response schemas
│   │   └── services/     # agents/ (LangGraph pipeline), chat/, user/, analysis/, health/, embeddings, dashboard
│   ├── alembic/          # Single baseline migration (extensions + mti_brain_* tables + triggers)
│   ├── Dockerfile        # Multi-stage Python 3.12 build
│   └── .env.example
│
├── frontend/            # Next.js 16 application (see frontend/README.md)
│   ├── app/              # App Router (login, /new, /chat, /chats, /projects, /starred, /settings)
│   ├── components/       # 40+ React components
│   ├── lib/              # API client, SSE parser, Zustand stores, auth, analytics
│   ├── hooks/            # Keyboard shortcuts, mobile/tablet viewport detection
│   ├── public/           # Static assets + service worker for PWA
│   ├── Dockerfile        # Multi-stage Node 20 build (standalone output)
│   └── .env.example
│
└── database/            # Data layer Docker Compose (see database/README.md)
    ├── docker-compose.yml  # PostgreSQL 18 + PgBouncer + Redis (publishes db_net)
    ├── docker_volume/      # Persistent data (git-ignored)
    └── .env.example
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19 (with React Compiler), TypeScript, Tailwind CSS 4, shadcn/ui, Zustand |
| Frontend extras | PostHog analytics, Dexie (IndexedDB) for composer drafts, Framer Motion, @tanstack/react-virtual, vaul, react-hotkeys-hook, pptxgenjs (PPT export), xlsx (Excel export), react-syntax-highlighter, cmdk, react-hook-form + zod |
| Backend | FastAPI + Gunicorn + Uvicorn (Python 3.12) |
| AI Pipeline | LangGraph (multi-node agentic graph), AWS Bedrock (Sonnet / Haiku / Opus), model routing |
| Knowledge Graph | Jena Fuseki (SPARQL), rdflib (ontology loading), sparql-based reasoning nodes |
| SQL Analysis | sqlglot (Snowflake dialect) for trust-strip source table extraction |
| Auth | Username/password → JWT (PyJWT, HS256, 8-hour expiry). **Okta OIDC migration planned** |
| App Database | PostgreSQL 18 + pgvector + SQLAlchemy (async) + Alembic |
| Postgres extensions | `pgvector`, `pg_trgm`, `fuzzystrmatch` (installed by the baseline migration) |
| Connection Pooling | PgBouncer (transaction mode) |
| Caching / Queues | Redis 8.4 (rate limiting, response cache, optional task queues) |
| Vector Search | pgvector (1536-dim) on feedback embeddings |
| Full-Text Search | tsvector + GIN + pg_trgm trigram + fuzzystrmatch (Levenshtein) |
| Streaming | SSE (sse-starlette) |
| Resilience | Circuit breakers (pybreaker) + retries (tenacity) |
| Reverse proxy | nginx 1.27-alpine (TLS termination, port 80/443) |
| Containerization | Docker + Docker Compose (root + database compose files) |

## API Summary

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
| POST | `/{thread_id}/ask` | Ask a question — accepts `deep_analysis: bool` (SSE streaming) |
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

### Playbook (`/api/v1/playbook`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | List saved queries |
| POST | `/` | Save a new query |
| PATCH | `/{query_id}` | Update a saved query |
| DELETE | `/{query_id}` | Delete a saved query |

### Pinned Metrics (`/api/v1/pinned-metrics`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | List pinned metric cards |
| POST | `/` | Pin a new metric |
| PATCH | `/{metric_id}` | Update a pinned metric |
| DELETE | `/{metric_id}` | Remove a pinned metric |

### Labels (`/api/v1/labels`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | List all labels for the current user |
| GET | `/thread/{thread_id}` | List labels on a thread |
| POST | `/thread/{thread_id}` | Add a label to a thread |
| DELETE | `/thread/{thread_id}/{label_id}` | Remove a label from a thread |

### Dashboard (`/api/v1/dashboard`)

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/generate/{conversation_id}` | Queue background dashboard generation; returns 202 |
| GET | `/{conversation_id}` | Get dashboard status + S3 presigned URL when ready |
| DELETE | `/{conversation_id}` | Remove dashboard from S3 and DB |

## Prerequisites

- Docker and Docker Compose
- (Manual mode only) Python 3.12+, Node.js 20+, PostgreSQL 15+ with `pgvector`, `pg_trgm`, `fuzzystrmatch`

## Getting Started

### Path A — Full Docker Compose (recommended)

```bash
git clone <repo-url>
cd mti-brain

# 1. Database layer
cd database
cp .env.example .env
# Edit .env: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
docker compose up -d
cd ..

# 2. Root .env
cp backend/.env.example .env
# Edit .env: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, JWT_SECRET

# 3. Bring up nginx + frontend + backend
docker compose up -d

# 4. Run migrations
docker compose run --rm backend alembic upgrade head
```

Open `http://localhost` and log in with `admin` / `admin123`.

> **Security:** Default credentials are hardcoded for development only. Change them before any shared or production deployment.

### Path B — Manual development

```bash
# 1. Database
docker network create db_net
cd database && cp .env.example .env && docker compose up -d && cd ..

# 2. Backend
cp backend/.env.example backend/.env
# Set POSTGRES_*, JWT_SECRET, CORS_ORIGINS=["http://localhost:3000"]
cd backend && pip install -r requirements.txt && alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Frontend (separate terminal)
cp frontend/.env.example frontend/.env
# Set NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
cd frontend && npm install --legacy-peer-deps && npm run dev
```

Open `http://localhost:3000` and log in with `admin` / `admin123`.

> **Windows dev note:** Use `127.0.0.1:8000` not `localhost:8000` in `NEXT_PUBLIC_API_URL`. VS Code / VS Code Insiders may intercept `localhost:8000` via its port-forwarding feature, silently blocking all backend traffic. Check VS Code's Ports panel (`View → Ports`) and stop any forwarding on port 8000 if it appears. Alternatively run the backend on `--port 8001` and update the env accordingly.

### Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","services":{"postgres":{"status":"ok"}},...}
```

## Services Overview

| Service | Port | Stack | Description |
|---------|------|-------|-------------|
| **nginx** | 80 / 443 | `nginx/` | Reverse proxy + TLS termination (Path A only) |
| **Frontend** | 3000 | `frontend/` | Next.js UI — login, chat, projects, settings, starred |
| **Backend** | 8000 | `backend/` | FastAPI REST + SSE streaming |
| **PgBouncer** | 5432 | `database/` | Connection pooler (transaction mode, SCRAM-SHA-256) |
| **PostgreSQL** | internal | `database/` | App database (pgvector, conversations, users) |

## Environment Variables

See each component for the full variable reference:

- [backend/.env.example](backend/.env.example) — App DB connection, JWT secret, CORS, pool, circuit breaker, SSL
- [database/.env.example](database/.env.example) — PostgreSQL credentials, PgBouncer tuning
- [frontend/.env.example](frontend/.env.example) — Backend API URL, dev HMR origins, optional PostHog keys

---

## Related Documentation

| Component | README |
|-----------|--------|
| Backend (FastAPI) | [backend/README.md](backend/README.md) |
| Frontend (Next.js) | [frontend/README.md](frontend/README.md) |
| Database (PostgreSQL + PgBouncer) | [database/README.md](database/README.md) |
| Deployment (AWS CodeDeploy) | [deploy/README.md](deploy/README.md) |
