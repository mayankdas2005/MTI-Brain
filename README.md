# MTI Brain

**MTI Brain** is an AI-powered conversational data analytics platform. Users ask natural-language questions and receive structured answers with data tables, charts, and follow-up suggestions streamed in real time via Server-Sent Events.

Built on a FastAPI backend, Next.js frontend, and a PostgreSQL data layer with pgvector for semantic search.

## Architecture

```
   Browser
      |
      | HTTP / SSE
      v
 +-----------+        +-------------------+
 | Next.js   |  REST  | FastAPI Backend   |
 | Frontend  | <----> | :8000             |
 | :3000     |   SSE  |                   |
 +-----------+        +--------+----------+
                               |
                               v
                       +---------------+
                       | PostgreSQL 18  |
                       | + pgvector     |
                       | + PgBouncer    |
                       | :5432          |
                       +---------------+
                       database/
```

The frontend authenticates via username/password, receives a JWT from the backend, and uses that token for all subsequent API calls. The backend stores conversation threads, messages, projects, and feedback in PostgreSQL.

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
├── backend/      # FastAPI backend (see backend/README.md)
│   ├── app/
│   │   ├── api/       # Route handlers: health, auth, chat, projects
│   │   ├── core/      # Config, logging, middleware, circuit breakers
│   │   ├── db/        # SQLAlchemy async session factory
│   │   ├── models/    # ORM: User, Project, Thread, Message, Feedback, ExecutionLog
│   │   ├── schemas/   # Pydantic request/response schemas
│   │   └── services/  # Auth, conversation CRUD, feedback, health
│   ├── alembic/       # Database migrations
│   ├── scripts/       # Utility scripts
│   ├── Dockerfile     # Multi-stage Python 3.12 build (includes MSSQL ODBC drivers)
│   └── .env.example
│
├── frontend/     # Next.js application (see frontend/README.md)
│   ├── app/           # App Router pages (login, chat, projects)
│   ├── components/    # 30+ React components (sidebar, composer, messages, dialogs)
│   ├── lib/           # API client, SSE parser, Zustand stores, auth, types
│   ├── hooks/         # Keyboard shortcuts, mobile detection, toast
│   ├── Dockerfile     # Multi-stage Node 20 build (standalone output)
│   └── .env.example
│
└── database/     # Data layer Docker Compose (see database/README.md)
    ├── docker-compose.yml  # PostgreSQL + PgBouncer
    ├── docker_volume/      # Persistent data (git-ignored)
    └── .env.example
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4, shadcn/ui, Zustand |
| Backend | FastAPI + Gunicorn + Uvicorn (Python 3.12) |
| Auth | Username/password → JWT (PyJWT, HS256, 8-hour expiry) |
| App Database | PostgreSQL 18 + pgvector + SQLAlchemy (async) + Alembic |
| Connection Pooling | PgBouncer (transaction mode) |
| Vector Search | pgvector (1536-dim) on feedback embeddings |
| Full-Text Search | PostgreSQL tsvector + GIN + pg_trgm + dmetaphone |
| Streaming | SSE (sse-starlette) |
| Resilience | Circuit breakers (pybreaker) + retries (tenacity) |
| Containerization | Docker + Docker Compose |

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
- (For local dev without Docker) Python 3.12+, Node.js 20+, PostgreSQL 15+ with pgvector

## Getting Started

### 1. Clone and configure

```bash
git clone <repo-url>
cd quest
```

### 2. Start the database layer

```bash
docker network create db_net
cd database
cp .env.example .env
# Edit .env: set POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
docker compose up -d
cd ..
```

Wait for both services to report `healthy` (`docker compose ps`).

### 3. Configure the backend

```bash
cp backend/.env.example backend/.env
# Edit backend/.env:
#   Required: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_DB, JWT_SECRET
#   CORS_ORIGINS must include the frontend origin (e.g. ["http://localhost:3000"])
```

### 4. Configure the frontend

```bash
cp frontend/.env.example frontend/.env
# Edit frontend/.env:
#   NEXT_PUBLIC_API_URL: backend URL (e.g. http://localhost:8000)
```

### 5. Run database migrations

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
cd ..
```

### 6. Start the services

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

> **Security:** Default credentials are hardcoded for development only. Change them before any shared or production deployment.

### 7. Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","services":{"postgres":{"status":"ok"}},...}
```

## Services Overview

| Service | Port | Stack | Description |
|---------|------|-------|-------------|
| **Frontend** | 3000 | `frontend/` | Next.js UI - login, chat, projects, settings |
| **Backend** | 8000 | `backend/` | FastAPI REST + SSE streaming |
| **PostgreSQL** | internal | `database/` | App database (pgvector, conversations, users) |
| **PgBouncer** | 5432 | `database/` | Connection pooler (transaction mode, SCRAM-SHA-256) |

## Environment Variables

See each component for the full variable reference:

- [backend/.env.example](backend/.env.example) - App DB connection, JWT secret, CORS, connection pool, circuit breaker, SSL
- [database/.env.example](database/.env.example) - PostgreSQL credentials, PgBouncer tuning
- [frontend/.env.example](frontend/.env.example) - Backend API URL, dev HMR origins

## Component READMEs

- [backend/README.md](backend/README.md) - API endpoints, database models, middleware, Dockerfile, env vars
- [frontend/README.md](frontend/README.md) - Routes, components, state management, auth flow, SSE streaming, preferences, keyboard shortcuts
- [database/README.md](database/README.md) - PostgreSQL tuning, PgBouncer config, volumes, health checks, resource limits
