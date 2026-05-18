# MTI Brain Backend

FastAPI backend for **MTI Brain** — an AI-powered conversational data analytics platform for enterprise treasury intelligence. Provides JWT-authenticated REST APIs for user login, conversation thread management, and project organization, with Server-Sent Events (SSE) streaming support for real-time responses.

The backend uses an async SQLAlchemy stack against PostgreSQL (with pgvector for embeddings and full-text search).

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
│   │       ├── playbook.py      # Saved queries (Playbook) CRUD
│   │       ├── pinned_metrics.py # Pinned metric cards CRUD
│   │       ├── labels.py        # Thread label apply/remove
│   │       ├── dashboard.py     # Per-conversation HTML dashboard (S3-backed)
│   │       └── deps.py          # CurrentUser dependency (JWT validation)
│   ├── core/
│   │   ├── config.py            # Pydantic Settings (all env vars)
│   │   ├── logger.py            # Loguru configuration
│   │   ├── middleware.py        # RequestIDMiddleware + TimingMiddleware + SecurityHeadersMiddleware
│   │   ├── circuit_breaker.py   # pybreaker instances for external services
│   │   └── rate_limit.py        # Shared rate limiter used by route handlers
│   ├── db/
│   │   ├── session.py           # SQLAlchemy async engine + session factory + pool warmup + LangGraph DSN helper
│   │   └── base.py              # Declarative ORM base
│   ├── models/
│   │   ├── conversation.py      # MTIBrainProject / MTIBrainThread / MTIBrainMessage / MTIBrainFeedback / MTIBrainDashboard
│   │   ├── user.py              # MTIBrainUser
│   │   ├── user_features.py     # UserSavedQuery / UserPinnedMetric / ThreadLabel
│   │   └── execution_log.py     # MTIBrainExecutionLog (per-run telemetry, stores response_tone)
│   ├── schemas/
│   │   ├── chat.py              # Pydantic request/response schemas for chat
│   │   ├── project.py           # Pydantic schemas for projects
│   │   └── user_features.py     # Schemas for Playbook / PinnedMetrics / Labels
│   └── services/
│       ├── auth.py              # Credential validation + JWT issue/decode + user upsert
│       ├── embeddings.py        # pgvector embedding helpers (Cohere Embed v4 via Bedrock)
│       ├── dashboard_builder.py # HTML dashboard generation + S3 upload/presign/delete
│       ├── dashboard_prompt.py  # Prompt templates for dashboard content
│       ├── agents/              # LangGraph pipeline
│       │   ├── graph.py         # Graph construction, SSE streaming, active-stream registry
│       │   ├── state.py         # Shared pipeline State TypedDict
│       │   ├── bedrock.py       # AWS Bedrock LLM client wrappers (Sonnet/Haiku/Opus)
│       │   ├── data_pool.py     # asyncpg / psycopg pool for LangGraph checkpointer
│       │   ├── ontology_loader.py # Fuseki ontology loading at startup
│       │   ├── fuseki_client.py # Async SPARQL HTTP client
│       │   ├── prompts.py       # LLM prompt templates
│       │   ├── helpers.py       # SectionStreamer / MultiSectionStreamer SSE helpers
│       │   ├── validators.py    # Schema validation helpers
│       │   └── nodes/           # One file per pipeline node
│       │       ├── intake.py         # intake_classify / general_chat / rejected
│       │       ├── domain.py         # domain_specialist_node
│       │       ├── plan.py           # plan_node
│       │       ├── plan_validator.py # plan_validator_node
│       │       ├── executor.py       # executor_node (runs inner graph per sub-question)
│       │       ├── step_reflector.py # step_reflector_node
│       │       ├── final_reflector.py # final_reflector_node
│       │       ├── repairer.py       # repairer_node
│       │       ├── governance.py     # governance_gate_node
│       │       ├── brain.py          # brain_retrieval_node
│       │       ├── compress.py       # compress_node (context compression)
│       │       ├── graph_reasoning.py # graph_reasoning_node
│       │       ├── human_loop.py     # human_in_loop_node
│       │       ├── ontology.py       # ontology_lookup_node
│       │       ├── sparql_gen.py     # sparql_gen_node
│       │       ├── sparql_validate.py # sparql_validate_node
│       │       ├── sparql_execute.py  # sparql_execute_node
│       │       ├── verifier.py       # verifier_node
│       │       ├── synthesis.py      # answer_synthesis_node
│       │       └── visualization.py  # visualization_node
│       ├── chat/
│       │   ├── conversation.py  # Thread/message/project CRUD + 3-layer search
│       │   └── feedback.py      # Feedback storage + pgvector similarity
│       ├── user/
│       │   ├── labels.py        # Thread label CRUD
│       │   ├── pinned_metrics.py # Pinned metric CRUD
│       │   └── playbook.py      # Saved query CRUD
│       ├── analysis/
│       │   └── sql.py           # Trust-strip: source table extraction via sqlglot (Snowflake dialect)
│       └── health/
│           └── service.py       # Circuit-breaker-protected Postgres health check
├── alembic/
│   ├── env.py                   # Imports all model modules so autogenerate sees every table
│   └── versions/
│       └── 0001_baseline.py     # Single baseline: extensions + tables + search_vector triggers
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

### Auth (`/api/v1/auth`)

> **Okta OIDC migration is planned.** The `MTIBrainUser` model already carries `okta_id` (unique, nullable), but the active flow is still credential-based via `app/services/auth.py`.

| Method | Path | Auth required | Description |
|--------|------|---------------|-------------|
| POST | `/login` | No | Exchange username + password for JWT |
| GET | `/me` | Yes | Current user profile |

**Login request:**
```json
{ "username": "admin", "password": "admin123" }
```

> **Development note:** Credentials are currently hardcoded in `app/services/auth.py`. Do not deploy with default credentials in production.

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
| POST | `/{thread_id}/ask` | Ask a question (SSE streaming) |
| POST | `/{thread_id}/retry` | Retry last response (SSE streaming) |
| POST | `/{thread_id}/edit` | Edit last question (SSE streaming) |
| POST | `/{thread_id}/stop` | Stop active stream |
| POST | `/{thread_id}/conversations/{conversation_id}/feedback` | Submit thumbs-up/down + comment |

#### Ask Request Body (`POST /{thread_id}/ask`)

```json
{
  "question": "What is our total cash balance as of yesterday?",
  "response_tone": "analyst",
  "max_rows": 100,
  "deep_analysis": false,
  "source_conversation_id": null,
  "prior_sql": null
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | required | User's natural-language question (1–2000 chars) |
| `response_tone` | string | `"analyst"` | One of `analyst`, `manager`, `director`, `executive` |
| `max_rows` | int | `100` | Result row limit (10–500) |
| `deep_analysis` | bool | `false` | When `true`, the pipeline uses extended multi-step reasoning (slower, more thorough) |
| `source_conversation_id` | UUID \| null | `null` | ID of the prior assistant turn this question follows from (for version branching) |
| `prior_sql` | string \| null | `null` | SQL from a specific prior answer to refine ("Refine this query" flow) |

### Projects (`/api/v1/projects`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List all projects (`search` query param) |
| POST | `/create` | Create a project |
| GET | `/{project_id}` | Get project with its threads |
| PUT | `/{project_id}` | Update name / description |
| DELETE | `/{project_id}` | Delete project (threads are unlinked, not deleted) |
| PATCH | `/{project_id}/star` | Toggle project star |

### Playbook (`/api/v1/playbook`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List saved queries for the current user |
| POST | `/` | Create a saved query |
| PATCH | `/{query_id}` | Update a saved query |
| DELETE | `/{query_id}` | Delete a saved query |

### Pinned Metrics (`/api/v1/pinned-metrics`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List pinned metric cards for the current user |
| POST | `/` | Pin a new metric |
| PATCH | `/{metric_id}` | Update a pinned metric (label, position, source query) |
| DELETE | `/{metric_id}` | Remove a pinned metric |

### Labels (`/api/v1/labels`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List all labels the user has applied across all threads |
| GET | `/thread/{thread_id}` | List labels applied to a specific thread |
| POST | `/thread/{thread_id}` | Apply a label to a thread |
| DELETE | `/thread/{thread_id}/{label_id}` | Remove a label from a thread |

### Dashboard (`/api/v1/dashboard`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate/{conversation_id}` | Queue background HTML dashboard generation; returns `202` immediately |
| GET | `/{conversation_id}` | Poll status (`pending` / `ready` / `error`) and retrieve S3 presigned URL |
| DELETE | `/{conversation_id}` | Remove dashboard from S3 and the database |

### API Docs

| URL | Description |
|-----|-------------|
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/openapi.json` | OpenAPI spec |

## Schema Reference

### ResponseTone

```python
ResponseTone = Literal["analyst", "manager", "director", "executive"]
```

| Value | Meaning |
|-------|---------|
| `analyst` | Data-driven, detailed breakdowns (default) |
| `manager` | Actionable insights with context |
| `director` | Strategic summaries with key metrics |
| `executive` | High-level, decision-ready answers |

> **Note:** The tone is passed through to the pipeline and stored in `execution_log.response_tone`. The backend does not currently branch pipeline logic on this value — it is available for the LLM prompt to use.

### Deep Analysis

When `deep_analysis: true` is sent, `_build_sse_generator` receives the flag and can activate extended reasoning in the LLM call. The flag is stored in `execution_log` for telemetry. Currently the flag is wired end-to-end but pipeline branching on it is pending LLM integration work.

## Database Models

| Model | Table | Purpose |
|-------|-------|---------|
| **MTIBrainUser** | `mti_brain_user` | User record keyed by email. Fields: `id`, `okta_id`, `email`, `name`, `groups` (JSONB), `organization`, `last_login`, `created_at`. |
| **MTIBrainProject** | `mti_brain_project` | Named collection of threads. Fields: `id`, `user_id`, `name`, `description`, `starred`, `search_vector` (tsvector GIN), timestamps. |
| **MTIBrainThread** | `mti_brain_thread` | Conversation thread. Fields: `id`, `user_id`, `project_id`, `title`, `starred`, `search_vector` (tsvector GIN). |
| **MTIBrainMessage** | `mti_brain_message` | Individual user or assistant message. Fields: `id`, `thread_id`, `conversation_id`, `parent_conversation_id`, `role`, `content`, `reasoning`, `metadata` (JSONB: `sql`, `chart_spec`, `intent`, `columns`, `rows`, `follow_ups`, etc.), `search_vector`. |
| **MTIBrainFeedback** | `mti_brain_feedback` | Thumbs-up/down + optional comment. Fields: `id`, `message_id`, `thread_id`, `liked`, `comment`, `embedding` (Vector 1536). |
| **MTIBrainDashboard** | `mti_brain_dashboard` | Per-conversation HTML dashboard record. Fields: `id`, `conversation_id`, `user_id`, `status` (`pending`/`ready`/`error`), `s3_key`, `s3_url`, `error_msg`, timestamps. |
| **MTIBrainExecutionLog** | `mti_brain_execution_log` | Per-run telemetry. Includes `response_tone` (string, max 30 chars) and `deep_analysis` fields for analytics. |
| **UserSavedQuery** | `mti_brain_saved_query` | Playbook entry. Fields: `id`, `user_id`, `name` (max 255), `query_text`, timestamps. |
| **UserPinnedMetric** | `mti_brain_pinned_metric` | Home-page metric card. Fields: `id`, `user_id`, `label`, `source_query`, `position`, timestamps. |
| **ThreadLabel** | `mti_brain_thread_label` | Colored label applied to a thread. Fields: `id`, `thread_id`, `user_id`, `name`, `color`, `created_at`. |

## AI Pipeline (LangGraph)

The pipeline is a compiled LangGraph state machine in `app/services/agents/graph.py`. Two graphs share the same `AsyncPostgresSaver` checkpoint pool:

- **`_main_graph`** — starts at `intake_classify`; routes to `general_chat`, `rejected`, the simple/complex domain path, or the advanced Plan/Execute/Reflect loop.
- **`_inner_graph`** — starts at `domain_specialist`; used by `executor_node` to process each sub-question in a complex plan.

### Pipeline nodes

| Node | Purpose |
|------|---------|
| `intake_classify` | Classify question type (`kg_query`, `general_chat`, `rejected`) and complexity (`simple`, `complex`, `advanced`) |
| `general_chat` | Handle non-analytics questions (greetings, clarifications) |
| `rejected` | Return a rejection message for out-of-scope questions |
| `domain_specialist` | Select the relevant knowledge domain and identify the correct ontology classes |
| `ontology_lookup` | Look up entity IRIs and property paths from the loaded ontology |
| `sparql_gen` | Generate a SPARQL query for the Fuseki endpoint |
| `sparql_validate` | Validate the generated SPARQL syntax before execution |
| `sparql_execute` | Execute the SPARQL query against Fuseki and return result bindings |
| `verifier` | Verify result quality; trigger repair if results are empty or malformed |
| `graph_reasoning` | Apply graph-level reasoning over SPARQL results |
| `brain_retrieval` | Retrieve relevant prior context from the LangGraph memory store |
| `compress` | Compress accumulated context when it exceeds the token budget |
| `plan` | Decompose a complex question into an ordered list of sub-questions |
| `plan_validator` | Validate the generated plan before execution |
| `executor` | Run the inner graph for each sub-question in the plan |
| `step_reflector` | Reflect on each executed step; decide whether to continue or repair |
| `final_reflector` | Final quality check over the completed plan execution |
| `repairer` | Rewrite a failing SPARQL query or re-plan after repeated step failure |
| `governance_gate` | Enforce data-access governance rules before synthesis |
| `human_in_loop` | Pause for optional human review in long-running plans |
| `answer_synthesis` | Synthesise all sub-results into a single structured answer |
| `visualization` | Produce chart spec and table data from the synthesised answer |

### Deep Analysis

When `deep_analysis: true` is sent, the pipeline activates extended multi-step reasoning (typically the advanced Plan/Execute/Reflect/Repair loop). The flag is stored in `MTIBrainExecutionLog` for telemetry.

## Middleware

`app/core/middleware.py` (pure ASGI — no `BaseHTTPMiddleware` to avoid Windows ProactorEventLoop deadlocks):

| Middleware | Purpose |
|-----------|---------|
| **RequestIDMiddleware** | Generates and attaches `X-Request-ID` to every request/response |
| **TimingMiddleware** | Logs `METHOD /path → STATUS (Nms)` for every non-health request; attaches `X-Response-Time` |
| **SecurityHeadersMiddleware** | Adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`; strict CSP on API responses (skipped for `/docs`, `/redoc`, `/openapi.json`) |

Starlette built-ins wired in `app/main.py`:

| Middleware | Purpose |
|-----------|---------|
| **CORSMiddleware** | Origins controlled by `CORS_ORIGINS`; credentials enabled |
| **TrustedHostMiddleware** | Wildcard in dev (intentional — team works across many laptops/VMs) |

## Resilience

- **Circuit breakers** (pybreaker) on Postgres health — prevents cascading failures when the DB is unavailable.
- **Tenacity retries** available for transient external service errors.
- **Graceful pool warmup** — 3 connections pre-opened at startup; engine disposed cleanly on shutdown.
- **Rate limiting** — `5/minute` on `POST /auth/login` via slowapi.

## Migrations

Single baseline at [`alembic/versions/0001_baseline.py`](alembic/versions/0001_baseline.py):

- Installs `pg_trgm`, `fuzzystrmatch`, and `vector` extensions (idempotent).
- Creates every `mti_brain_*` table with FKs and GIN trigram indexes.
- Installs `search_vector` trigger functions and triggers on `mti_brain_thread`, `mti_brain_message`, and `mti_brain_project`.

> **Trigger functions are not autogenerated.** If you change `search_vector` semantics, hand-edit the new revision to also `CREATE OR REPLACE FUNCTION` the trigger.

The migrating role needs `CREATE EXTENSION` privilege. If it doesn't, install the three extensions once as a superuser first — the migration's `IF NOT EXISTS` guards make repeat runs safe.

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

# 3. Configure secrets
cp .env.example .env
# Edit .env — fill in: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_DB, JWT_SECRET
# Non-secret settings (CORS, pool sizes, log level, etc.) live in config.yml — edit there if needed

# 4. Run database migrations
alembic upgrade head

# 5. Start the development server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> **Windows / VS Code note:** If `http://localhost:8000/docs` doesn't load and there are no backend logs, check VS Code's Ports panel (`View → Ports`). VS Code Insiders can auto-forward port 8000 and intercept all connections. Stop the forwarding or run on a different port (`--port 8001`) and update `NEXT_PUBLIC_API_URL` in the frontend accordingly.

### Docker

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
  --threads 2 \
  --timeout 480 \
  --graceful-timeout 30 \
  --keep-alive 5 \
  --max-requests 1000 \
  --max-requests-jitter 50 \
  --forwarded-allow-ips "*" \
  --access-logfile - \
  --error-logfile - \
  --log-level info
```

## Configuration

Configuration is split across two files:

| File | Purpose | Committed? |
|------|---------|------------|
| `config.yml` | All non-secret settings (timeouts, pool sizes, log level, CORS, JWT expiry, etc.) | **Yes** |
| `.env` | Secrets only (DB credentials, JWT secret, API keys) | **No** — git-ignored |

**Adding a new config value:**
1. Add it under the relevant section in `config.yml` with a sensible default.
2. Add the matching `Field` to `Settings` in `app/core/config.py`, reading from `_yml`.
3. If it needs to be a secret, add it to `.env.example` and `Settings` as a required field with no default.

**Override at deploy-time:** any `config.yml` value can be overridden without editing the file by setting the matching environment variable (e.g. `CORS_ORIGINS=["https://app.yourdomain.com"]` in CodeDeploy or the shell). Priority: env var > `.env` file > `config.yml` default.

### `.env` — Secrets (required)

Copy `.env.example` to `.env` and fill in real values.

| Variable | Description |
|----------|-------------|
| `POSTGRES_USER` | Database username |
| `POSTGRES_PASSWORD` | Database password |
| `POSTGRES_HOST` | Database host |
| `POSTGRES_DB` | Database name |
| `JWT_SECRET` | Secret for signing JWTs — generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `AWS_REGION` | AWS region for Bedrock (e.g. `us-west-2`) |
| `AWS_BEDROCK_SONNET_ARN` | ARN for the Claude Sonnet model on Bedrock |
| `AWS_BEDROCK_HAIKU_ARN` | ARN for the Claude Haiku model on Bedrock |
| `AWS_BEDROCK_OPUS_ARN` | ARN for the Claude Opus model on Bedrock |
| `AWS_BEDROCK_COHERE_EMBED_V4_ARN` | ARN for the Cohere Embed v4 model on Bedrock |
| `AWS_ACCESS_KEY_ID` | AWS access key (used for S3 dashboard storage and Bedrock) |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_BOTO3_BUCKET_NAME` | S3 bucket name for storing generated dashboards |
| `FUSEKI_URL` | Jena Fuseki base URL (e.g. `http://localhost:3030`) |
| `FUSEKI_DATASET` | Fuseki dataset name for the main knowledge graph |
| `TRIBAL_GRAPH_URL` | Fuseki base URL for the tribal/secondary graph |
| `TRIBAL_GRAPH_DATASET` | Dataset name for the tribal graph |

Optional secret overrides (uncomment in `.env` when needed):

| Variable | Description |
|----------|-------------|
| `DATABASE_SSL_ROOT_CERT` | Path to CA cert when `ssl_mode` is `verify-ca` or `verify-full` |
| `ENVIRONMENT` | Override `app.environment` from `config.yml` (`development` or `production`) |
| `LOG_LEVEL` | Override `app.log_level` from `config.yml` |
| `CORS_ORIGINS` | Override `server.cors_origins` from `config.yml` (JSON array or comma-separated) |

### `config.yml` — Non-secret config

| Section → key | Default | Description |
|---------------|---------|-------------|
| `app.environment` | `development` | `development` or `production` |
| `app.debug` | `false` | FastAPI + SQLAlchemy echo mode |
| `app.log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `server.cors_origins` | `[localhost:3000, 127.0.0.1:3000]` | Allowed browser origins |
| `database.port` | `5432` | Postgres port |
| `database.ssl_mode` | `disable` | `disable` / `require` / `verify-ca` / `verify-full` |
| `database.pool.size` | `2` | Base connection pool size (kept small — PgBouncer handles concurrency) |
| `database.pool.max_overflow` | `8` | Max extra connections above pool size |
| `database.pool.recycle_seconds` | `500` | Recycle connections older than N seconds (must be < PgBouncer `SERVER_IDLE_TIMEOUT`) |
| `database.pool.timeout_seconds` | `30` | Seconds to wait for a connection |
| `circuit_breaker.fail_max` | `5` | Consecutive failures before opening circuit |
| `circuit_breaker.reset_timeout_seconds` | `30` | Seconds before circuit half-opens |
| `jwt.algorithm` | `HS256` | JWT signing algorithm |
| `jwt.expiry_hours` | `8` | JWT token lifetime |
| `rate_limit.login_per_minute` | `5` | Max login attempts per IP per minute |
| `rate_limit.ask_per_minute` | `30` | Max `/ask`, `/retry`, `/edit` requests per IP per minute |
| `model_routing.llm_routing_enabled` | `true` | Enable per-question Haiku / Sonnet / Opus routing |
| `prompt_cache.aws_bedrock_prompt_cache` | `true` | Enable AWS Bedrock prompt caching |
| `fuseki.timeout_seconds` | `60` | HTTP timeout for SPARQL queries against Fuseki |
| `pipeline.recursion_limit` | `80` | LangGraph recursion limit (complex multi-step queries need headroom) |

---

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Frontend (Next.js) | [../frontend/README.md](../frontend/README.md) |
| Database (PostgreSQL + PgBouncer) | [../database/README.md](../database/README.md) |
| Deployment (AWS CodeDeploy) | [../deploy/README.md](../deploy/README.md) |
