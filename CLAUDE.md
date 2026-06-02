# mti-brain

AI analytics platform that lets users query business data via natural language. The backend runs a multi-node LangGraph pipeline against AWS Bedrock (Claude) to generate SQL, execute it against Redshift, and stream structured results back to the Next.js frontend.

---

## Monorepo map

| Directory | Purpose |
|-----------|---------|
| `backend/` | FastAPI REST + SSE streaming API, LangGraph agentic pipeline |
| `frontend/` | Next.js 16 (App Router) SPA — chat UI, projects, dashboards |
| `database/` | PostgreSQL 18 + PgBouncer + Redis 8.4 + Neo4j docker-compose stack |
| `nginx/` | Reverse proxy, TLS termination |
| `langfuse/` | Self-hosted LLM observability (traces, token usage, latency) |
| `semantic_model_generator/` | One-shot pipeline: Redshift schema → RDF → Neo4j knowledge graph |
| `deploy/` | AWS CodeDeploy lifecycle hook scripts |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, TypeScript (strict), Tailwind CSS 4, shadcn/ui, Zustand |
| Backend | FastAPI, Python 3.12, Uvicorn/Gunicorn |
| AI pipeline | LangGraph, AWS Bedrock (Claude Sonnet/Haiku/Opus), Cohere Embed v4 |
| App DB | PostgreSQL 18 + pgvector + SQLAlchemy async (asyncpg) + Alembic |
| Search | pgvector (1536-dim), pg_trgm, fuzzystrmatch |
| Graph DB | Neo4j (semantic intent routing, knowledge graph) |
| Cache | Redis 8.4 (rate limiting, response cache) |
| Connection pool | PgBouncer (transaction mode) |
| Auth | JWT (HS256, 8-hour expiry) — Okta OIDC migration in progress |
| Observability | Langfuse (self-hosted) |
| Proxy | nginx 1.27-alpine |
| Deployment | AWS CodeDeploy → EC2, Docker Compose |

---

## Local dev setup

### 1. Database stack (required first)

```bash
cd database
cp .env.example .env
# Edit: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, NEO4J_PASSWORD, REDIS_PASSWORD
docker compose up -d
cd ..
```

Creates the `db_net` Docker network that backend and root compose join.

### 2. Backend

```bash
cp backend/.env.example backend/.env
# Must set: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_DB,
#           JWT_SECRET, AWS_BEARER_TOKEN_BEDROCK, AWS_REGION,
#           AWS_BEDROCK_SONNET_ARN, AWS_BEDROCK_COHERE_EMBED_V4_ARN

cd backend
pip install -r requirements.txt
alembic upgrade head          # run migrations
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check: `curl http://localhost:8000/health`

### 3. Frontend

```bash
cp frontend/.env.example frontend/.env
# Must set: NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
# (use 127.0.0.1, not localhost, on Windows)

cd frontend
npm install --legacy-peer-deps
npm run dev
```

Open `http://localhost:3000` — default login: `admin` / `admin123`

---

## Running with Docker Compose

```bash
# Full stack (nginx on :80, frontend on :3000, backend on :8000)
docker compose up -d

# Database layer only
cd database && docker compose up -d

# Run migrations after first boot
docker compose run --rm backend alembic upgrade head

# Langfuse observability (separate)
cd langfuse && docker compose up -d
```

---

## Key entry points

### Backend

| File | Role |
|------|------|
| `backend/app/main.py` | FastAPI app, lifespan, middleware, router mount |
| `backend/app/api/v1/chat.py` | Conversation threads, SSE streaming endpoint |
| `backend/app/services/agents/graph.py` | LangGraph pipeline orchestration + SSE emission |
| `backend/app/services/agents/nodes/` | 21+ pipeline nodes (intake → domain → planner → executor → reflector …) |
| `backend/app/api/v1/auth.py` | Login, JWT issue, user profile |
| `backend/app/api/v1/dashboard.py` | Dashboard generation endpoint |
| `backend/config.yml` | Non-secret config (CORS, pool sizes, rate limits, model routing, feature toggles) |
| `backend/alembic/` | Migration scripts |

### Frontend

| File | Role |
|------|------|
| `frontend/app/page.tsx` | Login page |
| `frontend/app/layout.tsx` | Root layout, providers |
| `frontend/app/(authenticated)/chat/[chatId]/page.tsx` | Chat detail with SSE streaming |
| `frontend/lib/api/` | API client utilities |
| `frontend/lib/store/` | Zustand state stores |
| `frontend/components/chat-composer.tsx` | Message input, Deep Analysis toggle |
| `frontend/components/sidebar.tsx` | Navigation + thread list |

### Semantic model generator

| File | Role |
|------|------|
| `semantic_model_generator/graph/pipeline.py` | 12-step orchestration (extract → embed → load into Neo4j) |
| `semantic_model_generator/generate_semantic_model.py` | Redshift schema → RDF/R2RML extractor |

---

## Database migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration (from backend/)
alembic revision --autogenerate -m "description"

# Roll back one step
alembic downgrade -1
```

---

## Semantic model generator

Run once (or when the Redshift schema changes) to rebuild the Neo4j knowledge graph:

```bash
cd semantic_model_generator
cp .env.example .env
# Set: REDSHIFT_*, NEO4J_*, AWS_BEDROCK_* credentials

cd graph
pip install -r requirements.txt
python pipeline.py
```

The 12-step pipeline extracts schema → generates RDF → loads into Neo4j → enriches with LLM → embeds nodes. See `semantic_model_generator/README.md` for full details.

---

## Testing

No automated test suite exists. Verification is manual:

1. Run the stack locally (see dev setup above)
2. Hit `GET /health` to confirm backend is up
3. Log in via the frontend and submit a natural language query
4. Check Langfuse (`http://localhost:3000` of Langfuse stack) for pipeline traces

---

## Deployment

AWS CodeDeploy deploys to EC2 (build-on-host, not ECR). Hooks are defined in `appspec.yml`:

```
ApplicationStop → BeforeInstall → AfterInstall → ApplicationStart → ValidateService
```

Scripts live in `deploy/`. Production `.env` files must be pre-placed on the EC2 host before deployment. See `deploy/README.md` for the full runbook and rollback procedure.

---

## Conventions

- **Python**: 3.12, async throughout (asyncpg, async SQLAlchemy sessions), Loguru for structured logging, pybreaker for circuit breakers, tenacity for retries
- **TypeScript**: strict mode, no `any`, React Server Components where possible
- **UI components**: shadcn/ui (Radix UI primitives) + Tailwind CSS 4 — add new components with `npx shadcn@latest add <component>`
- **LangGraph nodes**: each node in `backend/app/services/agents/nodes/` is a pure async function `(state: AgentState) -> dict` that returns a partial state update
- **Streaming**: backend emits SSE via `sse-starlette`; frontend consumes via `fetch` + `ReadableStream` (not `EventSource`, to support POST bodies)
- **Model routing**: Haiku for fast/cheap tasks, Sonnet for default, Opus for complex reasoning — controlled by `config.yml` toggles
- **Secrets**: never commit `.env` files; all secrets via environment variables; non-secret config goes in `backend/config.yml`
