# mti-brain

AI analytics platform that lets users query business data via natural language. The backend runs a multi-node LangGraph pipeline against AWS Bedrock (Claude) to generate SQL, execute it against Redshift, and stream structured results back to the Next.js frontend.

---

## Monorepo Map

| Directory | Purpose |
|-----------|---------|
| `backend/` | FastAPI REST + SSE streaming API, LangGraph agentic pipeline |
| `frontend/` | Next.js 16 (App Router) SPA — chat UI, projects, dashboards |
| `database/` | PostgreSQL + PgBouncer + redis/redis-stack:7.4.0-v8 + Neo4j docker-compose stack |
| `nginx/` | Reverse proxy, TLS termination |
| `langfuse/` | Self-hosted LLM observability (traces, token usage, latency) |
| `semantic_model_generator/` | One-shot pipeline: Redshift schema → RDF → Neo4j knowledge graph |
| `deploy/` | AWS CodeDeploy lifecycle hook scripts |
| `learning/` | Experimental resources — Neo4j agent-memory patterns, pipeline diagrams |
| `assets/` | Static assets — Mermaid source and PNG of the LangGraph pipeline DAG |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16.2.3, React 19.2.4, TypeScript 5.7.3 (strict), Tailwind CSS 4, shadcn/ui, Zustand |
| Backend | FastAPI, Python 3.12, Uvicorn/Gunicorn |
| AI pipeline | LangGraph, AWS Bedrock (Claude Sonnet/Haiku/Opus), Cohere Embed v4 |
| App DB | PostgreSQL + pgvector + SQLAlchemy async (asyncpg) + Alembic |
| Search | pgvector (1536-dim), pg_trgm, fuzzystrmatch |
| Graph DB | Neo4j (semantic intent routing, knowledge graph) |
| Cache | redis/redis-stack:7.4.0-v8 (rate limiting, response cache) |
| Connection pool | PgBouncer (transaction mode) |
| Auth | JWT (HS256, 8-hour expiry) — Okta OIDC migration in progress |
| Observability | Langfuse (self-hosted) |
| Proxy | nginx 1.27-alpine |
| Deployment | AWS CodeDeploy → EC2, Docker Compose |

---

## Local Dev Setup

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

Open `http://localhost:3000` and log in with an existing DB user.

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

## Key Entry Points

### Backend

| File | Role |
|------|------|
| `backend/app/main.py` | FastAPI app, lifespan, middleware, router mount |
| `backend/app/services/agents/graph.py` | LangGraph pipeline wiring, graph compilation, init/shutdown |
| `backend/app/services/agents/pipeline.py` | SSE streaming entry point |
| `backend/app/services/agents/routing.py` | All routing decisions between nodes |
| `backend/app/services/agents/state.py` | `AgentState` TypedDict definition |
| `backend/app/services/agents/node_names.py` | Canonical node name constants |
| `backend/app/api/v1/chat.py` | Conversation threads, SSE streaming endpoint |
| `backend/app/api/v1/auth.py` | Login, JWT issue, user profile |
| `backend/app/api/v1/dashboard.py` | Dashboard generation endpoint |
| `backend/app/api/v1/project.py` | Project management |
| `backend/app/api/v1/graph_context.py` | Graph context API |
| `backend/app/api/v1/labels.py` | Labels API |
| `backend/app/api/v1/pinned_metrics.py` | Pinned metrics API |
| `backend/app/api/v1/playbook.py` | Playbook API |
| `backend/config.yml` | Non-secret config (CORS, pool sizes, rate limits, model routing, feature toggles) |
| `backend/alembic/` | Migration scripts |

### Backend: agents service layout

```
backend/app/services/agents/
  graph.py                 # graph wiring + compilation
  pipeline.py              # streaming execution entry point
  routing.py               # conditional edge functions
  state.py                 # AgentState definition
  node_names.py            # node name constants
  bedrock.py               # AWS Bedrock client wrapper
  redis_client.py          # Redis client
  neo4j_client.py          # Neo4j Bolt client
  redshift_client.py       # Redshift connection
  prompts.py               # shared prompt templates
  helpers.py               # shared utilities
  ir_utils.py              # intent-representation utilities
  semantic_ir.py           # semantic IR helpers
  filter_resolver_logic.py # filter resolution logic
  sql_validator_logic.py   # SQL validation logic
  result_summarizer.py     # result post-processing
  token_tracker.py         # Bedrock token accounting
  nodes/                   # one file per pipeline node (see below)
  context/                 # context-fetching helpers
  memory/                  # long-term memory (lt_memory)
  neo4j/                   # Neo4j query helpers
  ir/                      # intent-representation sub-modules
```

### Backend: pipeline nodes (`nodes/`)

The authoritative DAG diagram is in [`assets/analytics_graph.mmd`](assets/analytics_graph.mmd) (Mermaid source) and [`assets/analytics_graph.png`](assets/analytics_graph.png) (rendered).

The pipeline flows: `intake → context_fetcher → tribal_retrieval → anchor_resolver → query_planner → schema_enricher` (fan-out via Send API) `→ [measure_specialist | filter_specialist | dimension_specialist]` (parallel Haiku) `→ intent_assembler → directive_writer → schema_gap_resolver → query_compiler → [filter_resolver →] sql_generator → sql_validator → executor → data_quality_checker → synthesis → chart_agent`

Fallback: `intent_assembler` can route back to `intent_resolver` for error recovery. `executor` can route to `repair` (SQL fix) or `intent_resolver` (full recompile) on failure.

| Node file | Role |
|-----------|------|
| `intake_classifier.py` | Classify query type; route to general_chat or analytics path |
| `context_fetcher.py` | Phase 1 Neo4j fetch — tables only |
| `anchor_resolver.py` | Haiku: select anchor tables |
| `schema_enricher.py` | Deterministic: load full columns for anchor tables |
| `intent_dispatcher.py` | Fan-out via Send API to three specialists |
| `measure_specialist.py` | Parallel Haiku: identify measures |
| `filter_specialist.py` | Parallel Haiku: identify filters |
| `dimension_specialist.py` | Parallel Haiku: identify dimensions |
| `intent_assembler.py` | Waits for all 3 specialists; assembles intent |
| `intent_resolver.py` | Error-recovery fallback |
| `directive_writer.py` | Sonnet: write COMPUTATION/SCHEMA_GAP directives |
| `query_compiler.py` | Compile query plan from directives |
| `filter_resolver.py` | Resolve filter values |
| `sql_generator.py` / `sql_generator_node.py` | Generate SQL |
| `sql_validator.py` | Validate generated SQL |
| `executor.py` | Execute SQL against Redshift |
| `data_quality_checker.py` | Check result data quality |
| `synthesis.py` | Synthesize narrative response |
| `chart_agent.py` | Generate chart spec |
| `general_chat.py` | Non-analytics conversational responses |
| `tribal_retrieval.py` | Deep analysis only: fetch policy/limit/decision facts from Neo4j tribal graph (Policy, Limit, Decision, Commitment, Watchlist nodes); non-fatal |
| `query_planner.py` | Haiku: extract structured output contract (expected columns, groupings, time period, explicit entities) before schema enrichment; graceful-degrades to `None` on failure |
| `schema_gap_resolver.py` | Deterministic: parse `SCHEMA_GAP_*` directive lines; load missing columns and join paths from Neo4j so `sql_generator` has complete schema coverage |
| `compress.py` | Compress conversation history |
| `error_response.py` | Format error responses |
| `clarification.py` | Request user clarification |
| `confidence.py` | Confidence scoring |
| `audit.py` | Audit logging |
| `repair.py` | SQL repair logic |
| `ir_builder.py` | Build intent representation |
| `schema_context.py` | Schema context helpers |
| `zero_row_probe.py` | Probe for zero-row result handling |

### Frontend

| File | Role |
|------|------|
| `frontend/app/page.tsx` | Login page |
| `frontend/app/layout.tsx` | Root layout, providers |
| `frontend/app/(authenticated)/chat/[chatId]/page.tsx` | Chat detail with SSE streaming |
| `frontend/lib/api/` | API client utilities |
| `frontend/components/chat-composer.tsx` | Message input, Deep Analysis toggle |
| `frontend/components/sidebar.tsx` | Navigation + thread list |

### Frontend: Zustand stores (`frontend/lib/store/`)

| Store file | State managed |
|-----------|--------------|
| `auth.ts` | Auth state, current user, JWT |
| `threads.ts` | Chat thread list |
| `agents.ts` | Agent pipeline status |
| `activity.ts` | Activity feed |
| `dashboard.ts` | Dashboard panels |
| `drafts.ts` | Message drafts |
| `graph_context.ts` | Graph context visibility |
| `install.ts` | Install/onboarding state |
| `labels.ts` | Label management |
| `pinned-metrics.ts` | Pinned metric cards |
| `playbook.ts` | Playbook entries |
| `preferences.ts` | User preferences |
| `projects.ts` | Project list |
| `search.ts` | Search query state |
| `thinking.ts` | Thinking/reasoning display |
| `ui.ts` | Global UI state (sidebar, modals) |

### Semantic model generator

| File | Role |
|------|------|
| `semantic_model_generator/graph/pipeline.py` | 12-step orchestration (extract → embed → load into Neo4j) |
| `semantic_model_generator/generate_semantic_model.py` | Redshift schema → RDF/R2RML extractor |

---

## Database Migrations

```bash
# Apply all pending migrations (run from backend/)
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"

# Roll back one step
alembic downgrade -1
```

---

## Conventions

- **Config split**: secrets in `.env`; non-secret config in `backend/config.yml` (committed to git). Override any `config.yml` value at deploy-time via a matching env var.
- **LANGFUSE_ENABLED**: default `true` (`core_toggles.langfuse_enabled: true` in `config.yml`).
- **Python**: 3.12, async throughout (asyncpg, async SQLAlchemy sessions), Loguru for structured logging, pybreaker for circuit breakers, tenacity for retries.
- **LangGraph nodes**: each node in `nodes/` is a pure async function `(state: AgentState) -> dict` returning a partial state update.
- **Streaming**: backend emits SSE via `sse-starlette`; frontend consumes via `fetch` + `ReadableStream` (not `EventSource`, to support POST bodies).
- **Model routing**: Haiku for fast/cheap tasks, Sonnet for default, Opus for complex reasoning — controlled by `config.yml` `model_routing.llm_routing_enabled`.
- **TypeScript**: strict mode, no `any`, React Server Components where possible.
- **UI components**: shadcn/ui (Radix UI primitives) + Tailwind CSS 4 — add new components with `npx shadcn@latest add <component>`.
- **Secrets**: never commit `.env` files; all secrets via environment variables.

---

## Deployment

AWS CodeDeploy deploys to EC2 (build-on-host, not ECR). Hooks are defined in `appspec.yml`:

```
ApplicationStop → BeforeInstall → AfterInstall → ApplicationStart → ValidateService
```

Scripts live in `deploy/`. Production `.env` files must be pre-placed on the EC2 host before deployment. See `deploy/README.md` for the full runbook and rollback procedure.
