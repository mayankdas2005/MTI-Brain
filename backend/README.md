# MTI Brain Backend

FastAPI backend for **MTI Brain** — an AI-powered conversational data analytics platform for enterprise treasury intelligence. It exposes JWT-authenticated REST APIs with Server-Sent Events (SSE) streaming, backed by a multi-node LangGraph analytics pipeline that translates natural-language questions into SQL, executes them against Redshift, and streams back synthesized answers with chart specifications.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI + Uvicorn / Gunicorn |
| Language | Python 3.12 |
| Analytics Pipeline | LangGraph (13 nodes) |
| LLM Provider | AWS Bedrock — Claude Sonnet, Haiku, Opus |
| Embeddings | Cohere Embed v4 via AWS Bedrock (1536-dim) |
| Knowledge Graph | Neo4j (schema graph — table/column/join discovery) |
| Analytics DB | Amazon Redshift |
| App Database | PostgreSQL + SQLAlchemy async + Alembic |
| Vector Search | pgvector (1536-dim) on feedback embeddings |
| Full-Text Search | PostgreSQL `tsvector` + GIN + `pg_trgm` |
| Caching | Redis |
| Streaming | SSE via sse-starlette |
| Auth | Username/password → JWT (PyJWT, HS256, 8-hour expiry) |
| Resilience | Circuit breakers (pybreaker) + retries (tenacity) |
| Logging | Loguru (structured, request-ID, timing) |
| Observability | Langfuse (self-hosted) — LLM tracing, token usage, latency per node |
| Containerization | Docker (multi-stage, non-root, Python 3.12-slim) |

---

## Project Structure

```
backend/
├── app/
│   ├── main.py                          # FastAPI entry point + lifespan (warmup / shutdown)
│   ├── api/
│   │   ├── health.py                    # GET /health, POST /admin/cache/flush
│   │   └── v1/
│   │       ├── __init__.py              # Router aggregation
│   │       ├── auth.py                  # POST /login, GET /me
│   │       ├── chat.py                  # Thread CRUD + SSE ask/retry/edit/stop
│   │       ├── project.py               # Project CRUD + star/move
│   │       ├── playbook.py              # Saved queries CRUD
│   │       ├── pinned_metrics.py        # Pinned metric cards CRUD
│   │       ├── labels.py                # Thread label apply/remove
│   │       ├── dashboard.py             # Dashboard generate/retrieve/delete/download
│   │       ├── graph_context.py         # Graph context generate/retrieve/delete/download
│   │       └── deps.py                  # JWT dependency injection (get_current_user)
│   ├── core/
│   │   ├── config.py                    # Pydantic Settings (config.yml + .env merge)
│   │   ├── logger.py                    # Loguru configuration
│   │   ├── middleware.py                # RequestID, Timing, SecurityHeaders (pure ASGI)
│   │   ├── circuit_breaker.py           # pybreaker instances (LLM, embeddings, Neo4j, Redis, Postgres)
│   │   ├── rate_limit.py                # slowapi limiter (5/min login, 30/min ask)
│   │   └── langfuse_integration.py      # Langfuse lifecycle + callback handler
│   ├── db/
│   │   ├── session.py                   # SQLAlchemy async engine + session factories
│   │   ├── base.py                      # Declarative ORM base
│   │   └── __init__.py
│   ├── models/
│   │   ├── user.py                      # MTIBrainUser
│   │   ├── conversation.py              # Project, Thread, Message, Feedback, Dashboard, GraphContext
│   │   ├── user_features.py             # SavedQuery, PinnedMetric, ThreadLabel
│   │   └── execution_log.py             # MTIBrainExecutionLog (pipeline telemetry)
│   ├── schemas/
│   │   ├── chat.py                      # AskRequest, RetryRequest, EditRequest, ResponseTone
│   │   ├── project.py                   # Project/Thread schemas
│   │   └── user_features.py             # Playbook, PinnedMetric, Label schemas
│   └── services/
│       ├── auth.py                      # JWT creation/decode, user upsert
│       ├── embeddings.py                # Cohere Embed v4 via AWS Bedrock
│       ├── dashboard_builder.py         # HTML generation + S3 upload/presign
│       ├── dashboard_prompt.py          # Prompt templates for dashboard synthesis
│       ├── graph_context_builder.py     # Graph visualization generation + S3
│       ├── agents/                      # LangGraph analytics pipeline
│       │   ├── state.py                 # AnalyticsState TypedDict
│       │   ├── graph.py                 # Graph compilation + lifecycle
│       │   ├── bedrock.py               # AWS Bedrock LLM wrappers
│       │   ├── pipeline.py              # SSE streaming + active stream registry
│       │   ├── prompts.py               # All LLM prompt templates
│       │   ├── routing.py               # Conditional edge routing logic
│       │   ├── redis_client.py          # Redis cache client
│       │   ├── neo4j_client.py          # Neo4j driver + connection pooling
│       │   ├── redshift_client.py       # Redshift connection + query execution
│       │   ├── nodes/                   # 13 pipeline node implementations + post-processing helpers
│       │   │   ├── intake_classifier.py
│       │   │   ├── general_chat.py
│       │   │   ├── context_fetcher.py
│       │   │   ├── intent_resolver.py
│       │   │   ├── ir_builder.py
│       │   │   ├── query_compiler.py
│       │   │   ├── filter_resolver.py
│       │   │   ├── sql_generator.py
│       │   │   ├── sql_validator.py
│       │   │   ├── executor.py
│       │   │   ├── synthesis.py
│       │   │   ├── chart_agent.py
│       │   │   ├── error_response.py
│       │   │   ├── compress.py
│       │   │   ├── zero_row_probe.py
│       │   │   ├── repair.py
│       │   │   ├── audit.py
│       │   │   └── confidence.py        # Post-processing confidence scorer
│       │   ├── neo4j/                   # Neo4j schema exploration helpers
│       │   │   ├── client.py
│       │   │   ├── table_search.py
│       │   │   ├── column_search.py
│       │   │   ├── join_resolution.py
│       │   │   ├── hub_detection.py
│       │   │   ├── template_search.py
│       │   │   └── write.py
│       │   ├── context/                 # Context enrichment
│       │   │   ├── fetcher.py
│       │   │   ├── table_discovery.py
│       │   │   ├── column_loader.py
│       │   │   ├── cross_domain.py
│       │   │   └── helpers.py
│       │   ├── memory/                  # Conversation memory
│       │   │   ├── short_term.py
│       │   │   └── long_term.py
│       │   └── ir/                      # Intermediate representation
│       │       └── validation.py
│       ├── chat/
│       │   ├── conversation.py          # Thread CRUD, 3-layer search
│       │   └── feedback.py              # Feedback storage + similarity
│       ├── user/
│       │   ├── playbook.py
│       │   ├── pinned_metrics.py
│       │   └── labels.py
│       ├── analysis/
│       │   └── sql.py                   # sqlglot trust-strip (source table extraction)
│       └── health/
│           └── service.py               # Postgres / Neo4j / Redis health checks
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_baseline.py             # Full schema baseline migration
├── scripts/
│   └── render_graph.py                  # Export pipeline DAG diagram
├── alembic.ini
├── config.yml                           # Non-secret configuration (committed)
├── .env.example                         # Secrets template (not committed)
├── Dockerfile
├── requirements.txt
└── node_names.yml                       # Node label overrides for graph rendering
```

---

## Getting Started

### Prerequisites

- Python 3.12
- PostgreSQL 15+ with extensions: `pgvector`, `pg_trgm`, `fuzzystrmatch`
- Neo4j 5+ (bolt)
- Redis 7+
- Amazon Redshift (or compatible endpoint)
- AWS credentials with Bedrock access (Claude + Cohere Embed v4)
- AWS S3 bucket for dashboard/graph-context storage

### Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Configuration is split into two files:

| File | Contains | Committed? |
|------|----------|-----------|
| `config.yml` | Non-secret settings (ports, pool sizes, feature flags) | Yes |
| `.env` | Secrets (passwords, API keys, ARNs) | No — use `.env.example` as template |

Copy the secrets template and fill in your values:

```bash
cp .env.example .env
```

### Run Locally

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs available at `http://localhost:8000/docs`.

### Database Migrations

```bash
alembic upgrade head
```

### Run with Docker

```bash
docker build -t mti-brain-backend .
docker run --env-file .env -p 8000:8000 mti-brain-backend
```

---

## Configuration Reference

### `.env` — Secrets

| Variable | Description |
|----------|-------------|
| `POSTGRES_USER` | PostgreSQL username |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_HOST` | PostgreSQL host (or PgBouncer host) |
| `POSTGRES_DB` | PostgreSQL database name |
| `JWT_SECRET` | JWT signing secret (minimum 32 characters) |
| `AWS_BEARER_TOKEN_BEDROCK` | AWS Bedrock bearer token |
| `AWS_REGION` | AWS region (e.g. `us-west-2`) |
| `AWS_BEDROCK_SONNET_ARN` | Claude Sonnet cross-region inference profile ARN |
| `AWS_BEDROCK_HAIKU_ARN` | Claude Haiku cross-region inference profile ARN |
| `AWS_BEDROCK_OPUS_ARN` | Claude Opus cross-region inference profile ARN |
| `AWS_BEDROCK_COHERE_EMBED_V4_ARN` | Cohere Embed v4 ARN |
| `AWS_ACCESS_KEY_ID` | AWS access key ID |
| `AWS_SECRET_ACCESS_KEY` | AWS secret access key |
| `AWS_BOTO3_BUCKET_NAME` | S3 bucket for dashboards and graph context files |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key |
| `LANGFUSE_BASE_URL` | Langfuse server URL (default: `http://localhost:3100`) |
| `NEO4J_URI` | Neo4j bolt URL (e.g. `bolt://localhost:7687`) |
| `NEO4J_USER` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |
| `NEO4J_DB` | Neo4j database name |
| `REDSHIFT_USER` | Redshift username |
| `REDSHIFT_PASSWORD` | Redshift password |
| `REDSHIFT_HOST` | Redshift cluster host |
| `REDSHIFT_PORT` | Redshift port (default: `5439`) |
| `REDSHIFT_DB` | Redshift database name |
| `REDSHIFT_SCHEMA` | Redshift schema |
| `REDIS_HOST` | Redis host |
| `REDIS_PASSWORD` | Redis password |
| `REDIS_PORT` | Redis port (default: `6379`) |
| `REDIS_URL` | Full Redis connection URL |

### `config.yml` — Non-Secret Settings

| Key | Default | Description |
|-----|---------|-------------|
| `ENVIRONMENT` | `development` | `development` or `production` |
| `DEBUG` | `false` | Enable debug mode |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed browser origins |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `DATABASE_SSL_MODE` | `disable` | `disable`, `require`, `verify-ca`, `verify-full` |
| `DATABASE_SSL_ROOT_CERT` | `""` | Path to CA certificate |
| `DB_POOL_SIZE` | `2` | SQLAlchemy base pool size |
| `DB_MAX_OVERFLOW` | `8` | Max overflow connections |
| `DB_POOL_RECYCLE` | `500` | Recycle connections older than N seconds |
| `DB_POOL_TIMEOUT` | `30` | Wait timeout for free connection (seconds) |
| `CHECKPOINT_POOL_MIN` | `1` | LangGraph checkpoint psycopg3 pool minimum |
| `CHECKPOINT_POOL_MAX` | `5` | LangGraph checkpoint psycopg3 pool maximum |
| `CB_FAIL_MAX` | `5` | Circuit breaker failure threshold |
| `CB_RESET_TIMEOUT` | `30` | Circuit breaker reset timeout (seconds) |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `JWT_EXPIRY_HOURS` | `8` | JWT token lifetime (hours) |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | `5` | Login rate limit per IP |
| `RATE_LIMIT_ASK_PER_MINUTE` | `30` | Ask/retry/edit rate limit per IP |
| `LLM_ROUTING_ENABLED` | `true` | Enable model-tier routing in the pipeline |
| `AWS_BEDROCK_PROMPT_CACHE` | `true` | Enable Bedrock prompt caching |
| `PIPELINE_RECURSION_LIMIT` | `80` | LangGraph recursion limit |
| `NEO4J_MAX_POOL_SIZE` | `10` | Neo4j driver connection pool size |
| `NEO4J_CONNECTION_TIMEOUT` | `10.0` | Neo4j connection timeout (seconds) |
| `NEO4J_ACQUISITION_TIMEOUT` | `10.0` | Neo4j pool acquisition timeout (seconds) |
| `REDIS_MAX_CONNECTIONS` | `20` | Redis connection pool size |
| `REDIS_HEALTH_CHECK_INTERVAL` | `30` | Redis health check interval (seconds) |
| `LANGFUSE_ENABLED` | `false` | Enable Langfuse LLM tracing |

---

## API Reference

All endpoints under `/api/v1/*` require `Authorization: Bearer <token>` except `/api/v1/auth/login`.

### Health & Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Readiness probe — returns status of Postgres, Neo4j, Redis, and circuit breakers |
| `POST` | `/admin/cache/flush` | Yes | Flush all Redis keys |

**Health response:**
```json
{
  "status": "ok",
  "postgres": "ok",
  "neo4j": "ok",
  "redis": "ok",
  "circuit_breakers": { "postgres": "closed", "llm": "closed", ... }
}
```

---

### Auth — `/api/v1/auth`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/auth/login` | No | Exchange credentials for JWT |
| `GET` | `/api/v1/auth/me` | Yes | Get current user profile |

**Login request:**
```json
{ "username": "admin@milestone.tech", "password": "..." }
```

**Login response:**
```json
{
  "token": "<jwt>",
  "user": { "user_id": "...", "email": "...", "name": "...", "groups": [...] }
}
```

---

### Chat — `/api/v1/chat`

#### Thread Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/chat/new` | Create a new conversation thread |
| `GET` | `/api/v1/chat/recents` | List/search recent threads (`?search=&project_id=&limit=&offset=`) |
| `GET` | `/api/v1/chat/{thread_id}` | Get thread with all messages |
| `DELETE` | `/api/v1/chat/{thread_id}` | Delete a thread |
| `PATCH` | `/api/v1/chat/{thread_id}/star` | Toggle thread star |
| `PATCH` | `/api/v1/chat/{thread_id}/rename` | Rename thread |
| `PATCH` | `/api/v1/chat/{thread_id}/move` | Move thread to a project |
| `POST` | `/api/v1/chat/bulk/delete` | Bulk delete threads |
| `POST` | `/api/v1/chat/bulk/move` | Bulk move threads to a project |

#### SSE Streaming

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/chat/{thread_id}/ask` | Ask a question — SSE streaming response |
| `POST` | `/api/v1/chat/{thread_id}/retry` | Retry the last question — SSE streaming |
| `POST` | `/api/v1/chat/{thread_id}/edit` | Edit the last question — SSE streaming |
| `POST` | `/api/v1/chat/{thread_id}/stop` | Cancel an active stream |

**Ask request body:**
```json
{
  "question": "What were total cash inflows last quarter?",
  "response_tone": "analyst",
  "max_rows": 100,
  "deep_analysis": false,
  "source_conversation_id": null,
  "prior_sql": null
}
```

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `question` | string | 1–2000 chars | required |
| `response_tone` | string | `analyst` \| `manager` \| `director` \| `executive` | `analyst` |
| `max_rows` | integer | 10–500 | `100` |
| `deep_analysis` | boolean | — | `false` |
| `source_conversation_id` | uuid \| null | For version branching | `null` |
| `prior_sql` | string \| null | SQL to refine (Refine this query flow) | `null` |

**SSE event stream:**

| Event | Payload | Description |
|-------|---------|-------------|
| `context` | `{ "tables": [...] }` | Schema context resolved from Neo4j |
| `status` | `{ "message": "..." }` | Pipeline progress updates |
| `token` | `{ "text": "..." }` | Streamed answer tokens |
| `chart` | `{ "spec": {...} }` | Vega-Lite chart specification |
| `confidence` | `{ "score": <0-100>, "label": "<High\|Medium\|Low\|Very Low>", "explanation": "<string>" }` | Answer confidence score — fires after `done` (see [Confidence Scoring](#confidence-scoring)) |
| `done` | `{ "answer": "...", "sql": "...", "rows": [...], "confidence": {...}, "langfuse_trace_id": "..." }` | Final result |

#### Feedback

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/chat/{thread_id}/conversations/{conversation_id}/feedback` | Submit thumbs up/down |

**Feedback request:**
```json
{ "liked": true, "comment": "Accurate breakdown by entity." }
```

---

### Projects — `/api/v1/projects`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/projects/` | List all projects (`?search=`) |
| `POST` | `/api/v1/projects/create` | Create a project |
| `GET` | `/api/v1/projects/{project_id}` | Get project with all threads |
| `PUT` | `/api/v1/projects/{project_id}` | Update name/description |
| `DELETE` | `/api/v1/projects/{project_id}` | Delete project (threads unlinked, not deleted) |
| `PATCH` | `/api/v1/projects/{project_id}/star` | Toggle project star |

---

### Playbook — `/api/v1/playbook`

Saved queries for re-use.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/playbook/` | List saved queries |
| `POST` | `/api/v1/playbook/` | Create saved query `{ "name": "...", "query_text": "..." }` |
| `PATCH` | `/api/v1/playbook/{query_id}` | Update saved query |
| `DELETE` | `/api/v1/playbook/{query_id}` | Delete saved query |

---

### Pinned Metrics — `/api/v1/pinned-metrics`

Home-page metric cards.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/pinned-metrics/` | List pinned metrics |
| `POST` | `/api/v1/pinned-metrics/` | Create metric `{ "label": "...", "source_query": "...", "position": 1 }` |
| `PATCH` | `/api/v1/pinned-metrics/{metric_id}` | Update label or position |
| `DELETE` | `/api/v1/pinned-metrics/{metric_id}` | Remove metric |

---

### Labels — `/api/v1/labels`

Per-thread color labels.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/labels/` | List all labels applied by the current user |
| `GET` | `/api/v1/labels/thread/{thread_id}` | List labels on a specific thread |
| `POST` | `/api/v1/labels/thread/{thread_id}` | Apply label `{ "label": "...", "color": "#RRGGBB" }` |
| `DELETE` | `/api/v1/labels/{label_id}` | Remove label |

---

### Dashboard — `/api/v1/dashboard`

Generates an HTML analytics dashboard from a conversation's query results, stored in S3.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/dashboard/generate/{conversation_id}` | Queue dashboard generation → **202 Accepted** |
| `GET` | `/api/v1/dashboard/{conversation_id}` | Poll status + retrieve presigned URL |
| `DELETE` | `/api/v1/dashboard/{conversation_id}` | Remove dashboard from S3 + DB |
| `GET` | `/api/v1/dashboard/{conversation_id}/download` | Stream dashboard HTML as attachment |

**Status response:**
```json
{ "status": "pending|ready|error", "message": "...", "url": "https://s3.presigned..." }
```

---

### Graph Context — `/api/v1/graph-context`

Generates an HTML graph visualization of the Neo4j schema context relevant to a conversation.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/graph-context/generate/{conversation_id}` | Queue graph context generation → **202 Accepted** |
| `GET` | `/api/v1/graph-context/{conversation_id}` | Poll status + retrieve presigned URL |
| `DELETE` | `/api/v1/graph-context/{conversation_id}` | Remove from S3 + DB |
| `GET` | `/api/v1/graph-context/{conversation_id}/download` | Stream graph context HTML as attachment |

Generation is idempotent: if a `ready` result already exists for the conversation, it is returned immediately without re-running. Stale `pending` rows (older than 120 seconds) are reset and re-queued.

---

## Analytics Pipeline

The pipeline is a LangGraph DAG compiled at startup (`app/services/agents/graph.py`) and run per question. State is checkpointed to PostgreSQL via `AsyncPostgresSaver`.

### Node DAG

```
START → intake_classifier
          ├─[general]──→ general_chat ──→ END
          └─[analytics]─→ context_fetcher
                            └→ intent_resolver
                               └→ query_compiler
                                  └→ filter_resolver
                                     └→ sql_generator
                                        └→ sql_validator
                                           └→ executor
                                              ├─[zero rows]─→ zero_row_probe
                                              ├─[error]─────→ repair (max 2×) ──→ executor
                                              └─[ok]────────→ synthesis
                                                               └→ chart_agent ──→ END
          [any node error] ──→ error_response ──→ END
```

Additional cross-cutting nodes: `compress` (context truncation), `audit` (execution log write).

### Node Descriptions

| Node | Model | Role |
|------|-------|------|
| `intake_classifier` | Haiku | Classify question: `general_chat` or `analytics` |
| `general_chat` | Haiku | Answer non-analytics questions directly |
| `context_fetcher` | — | Semantic table/column discovery from Neo4j |
| `intent_resolver` | Sonnet | Extract structured intent + domain routing |
| `query_compiler` | — | Pattern matching + intermediate representation (IR) compilation |
| `filter_resolver` | Sonnet | Resolve dynamic filters (dates, entities, thresholds) |
| `sql_generator` | Opus | Generate SQL from IR + Neo4j schema context |
| `sql_validator` | — | Validate SQL syntax and schema against Neo4j graph |
| `executor` | — | Execute SQL on Redshift, paginate results |
| `zero_row_probe` | Sonnet | Diagnose and explain empty result sets |
| `repair` | Sonnet | Rewrite failing SQL based on error (max 2 attempts) |
| `synthesis` | Sonnet | Synthesize natural-language answer from query results |
| `chart_agent` | Sonnet | Generate Vega-Lite chart spec + alternative specs |
| `compress` | Sonnet | Truncate state context when approaching token limits |
| `audit` | — | Write execution telemetry to `mti_brain_execution_log` |
| `error_response` | — | Produce user-facing error message |

### Pipeline State (`AnalyticsState`)

Key fields in the `TypedDict`:

| Group | Fields |
|-------|--------|
| Conversation | `messages`, `user_id`, `thread_id`, `persona`, `question` |
| Routing | `question_type`, `needs_clarification`, `clarification_count` |
| Pipeline | `semantic_context`, `resolved_intent`, `semantic_ir_list`, `sql_list` |
| Execution | `result_list`, `query_summary`, `no_data`, `reliability_flags`, `repair_count` |
| Output | `answer`, `chart_spec`, `chart_type`, `alternative_chart_specs`, `follow_ups` |
| Memory | `feedback_context`, `summary` |
| Audit | `user_email`, `pipeline_start_ms`, `pattern_matched`, `pattern_name`, `is_retry` |
| Control | `error`, `execution_error`, `stopped`, `deep_analysis`, `max_rows` |

### Confidence Scoring

After the LangGraph pipeline completes, `pipeline.py` calls `compute_confidence()` from `nodes/confidence.py`. This is **not a graph node** — it is a post-processing step that makes a single Haiku LLM call and returns a confidence score (0–100) displayed below the response in the UI.

#### How it works

```
astream_events loop runs  (all 13 nodes complete, answer already streamed token-by-token)
          ↓
_done_rows / _done_cols computed          ← row/col extraction, shared with done event
          ↓
await compute_confidence(state)           ← single Haiku call, ~200–400 ms
          ↓
yield {"event": "confidence", "data": {score, label, explanation}}
          ↓
yield {"event": "done", "data": {..., "confidence": {score, label, explanation}}}
```

The answer is fully visible to the user before confidence is computed (streamed via `answer.delta` during synthesis). The confidence badge appears just before the `done` event fires. `confidence` is included in both the dedicated SSE event and the `done` payload so the UI can choose which to use.

`general_chat` questions always return `null` — no badge is applicable since there is no data to ground against.

#### What is sent to the LLM

| Input | Source | Purpose |
|-------|--------|---------|
| `question` | User's original question (injected from `stream_pipeline` closure — not in node state) | Verify the answer addresses what was asked |
| `semantic_context` | `state["semantic_context"]` — intents, business terms, query patterns detected by `context_fetcher` | What domain concepts were resolved |
| `resolved_intent` | `state["resolved_intent"]` — intent label, anchor tables, template ID from `intent_resolver` | Which tables and intent drove the SQL |
| `no_data` | `state["no_data"]` | Whether the query returned zero rows |
| `total_corrections` | `repair_count + recompile_count` | Number of times SQL had to be auto-corrected |
| `reliability_flags` | `state["reliability_flags"]` | Pipeline-detected quality concerns (e.g. filter approximated) |
| `error` | `state["error"]` or `state["execution_error"]` | Any SQL or pipeline error message |
| `data_profile` | `helpers._build_data_profile(cols, rows, query_summary)` | Column types + stats + NULL-free spread sample (up to 20 rows) |
| `answer` | `state["answer"]` — full text, no truncation | The narrative to be scored |

`data_profile` is produced by `helpers._build_data_profile()`: computes column dtypes, min/max/mean/median for numerics, distinct counts and top values for strings, date ranges for dates, and uses a spread sample of up to 20 non-null rows. NULL rows are filtered before sampling. Same profile used by the synthesis and chart nodes.

#### Score labels

| Score range | Label |
|-------------|-------|
| 80–100 | High |
| 60–79 | Medium |
| 40–59 | Low |
| 0–39 | Very Low |

#### Database storage

`confidence` is saved in the `metadata` JSONB column of `mti_brain_message` (field: `metadata.confidence`) when the assistant message is persisted in `chat.py`. Value is `null` when confidence was not computed.

#### Prompt location

`CONFIDENCE_JUDGE_PROMPT` is defined in `app/services/agents/prompts.py` and imported by `nodes/confidence.py`.

---

### Checkpoint Storage

LangGraph state is persisted using `AsyncPostgresSaver` with a dedicated psycopg3 connection pool (`CHECKPOINT_POOL_MIN`/`CHECKPOINT_POOL_MAX`). The pool reconnects after each node to stay compatible with PgBouncer transaction-mode pooling.

---

## Database Schema

### PostgreSQL Tables

| Table | Description |
|-------|-------------|
| `mti_brain_user` | Authenticated users (email, name, groups JSONB, keycloak_sub, last_login) |
| `mti_brain_project` | Thread collections (user_id FK, name, description, starred, search_vector) |
| `mti_brain_thread` | Conversation threads (user_id FK, project_id FK, title, starred, search_vector) |
| `mti_brain_message` | Messages (thread_id FK, conversation_id, role, content, reasoning, metadata JSONB) |
| `mti_brain_feedback` | Thumbs up/down (message_id FK, liked bool, comment, embedding pgvector 1536) |
| `mti_brain_dashboard` | Dashboard jobs (conversation_id, status, s3_key, s3_url, error_msg) |
| `mti_brain_graph_context` | Graph context jobs (conversation_id, thread_id, user_id FK, status, s3_key, s3_url) |
| `mti_brain_execution_log` | Pipeline telemetry (user_id FK, thread_id FK, conversation_id, response_tone, deep_analysis, duration_ms, token_usage, langfuse_trace_id) |
| `mti_brain_saved_query` | Playbook entries (user_id FK, name, query_text) |
| `mti_brain_pinned_metric` | Pinned metric cards (user_id FK, label, source_query, position) |
| `mti_brain_thread_label` | Thread labels (thread_id FK, user_id FK, name, color) |

### PostgreSQL Extensions

| Extension | Purpose |
|-----------|---------|
| `pgvector` | 1536-dim embedding storage and similarity search on feedback |
| `pg_trgm` | Trigram GIN indexes for fuzzy thread/message search |
| `fuzzystrmatch` | Levenshtein matching for user-facing search |

### Search Vectors

Full-text `tsvector` columns are maintained automatically by database triggers:

| Column | Indexed content |
|--------|----------------|
| `mti_brain_thread.search_vector` | Thread title (English) |
| `mti_brain_message.search_vector` | Message content (English) |
| `mti_brain_project.search_vector` | Project name (weight A) + description (weight B) |

### Thread Search — 3-Layer Strategy

The `/chat/recents` search runs three passes and merges results:

1. **Full-text** — `search_vector @@ to_tsquery(...)` with GIN index
2. **Trigram** — `title % query` using `gin_trgm_ops` GIN index (fuzzy)
3. **Semantic** — pgvector cosine similarity on feedback embeddings

### Neo4j Knowledge Graph

Neo4j stores the relational schema as a property graph: tables and columns as nodes, join paths as edges, with synonym/alias relationships. The `context_fetcher` node queries Neo4j to discover relevant tables and columns for a given question. The `sql_validator` node checks the generated SQL against the same graph to catch schema mismatches before execution.

---

## Architecture Notes

### Middleware Stack

Registered in this order (outermost first):

1. **SlowAPIMiddleware** — Rate limiting via slowapi
2. **SecurityHeadersMiddleware** — CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`
3. **RequestIDMiddleware** — Propagates or generates `X-Request-ID`
4. **TimingMiddleware** — Logs `METHOD /path → STATUS (Nms)`, adds `X-Response-Time`
5. **CORSMiddleware** — Origin allowlist, credentials enabled
6. **TrustedHostMiddleware** — Wildcard in development

All custom middleware is implemented as pure ASGI callables (no `BaseHTTPMiddleware`) for Windows compatibility.

### Circuit Breakers

| Breaker | Fail threshold | Reset timeout |
|---------|---------------|---------------|
| `postgres_breaker` | 5 | 30s |
| `llm_breaker` | 3 | 60s |
| `embedding_breaker` | 3 | 60s |
| `external_api_breaker` | 3 | 60s |
| `neo4j_breaker` | 3 | 30s |
| `redis_breaker` | 10 | 10s |

State changes are logged via `LoggingListener`. The `/health` endpoint exposes current breaker states.

### Rate Limiting

| Endpoint group | Limit |
|----------------|-------|
| `POST /api/v1/auth/login` | 5 per minute per IP |
| `POST .../ask`, `.../retry`, `.../edit` | 30 per minute per IP |

### Dashboard & Graph Context — Async S3 Flow

1. Client calls `POST .../generate/{conversation_id}` → **202 Accepted**, DB row set to `pending`
2. Background task generates HTML and uploads to `s3://{bucket}/{user_id}/{conversation_id}.html`
3. DB row updated to `ready` with S3 key
4. Client polls `GET .../{conversation_id}` → receives presigned URL (1-hour TTL) when status is `ready`
5. Client calls `GET .../{conversation_id}/download` to stream the file directly

---

## Deployment

### Docker

The `Dockerfile` uses a multi-stage build:

- **Stage 1 (builder):** installs dependencies into a virtual environment
- **Stage 2 (runtime):** Python 3.12-slim, copies venv, runs as non-root `appuser`

Built-in `HEALTHCHECK` hits `GET /health` every 120 seconds.

```bash
docker build -t mti-brain-backend .
docker run --env-file .env -p 8000:8000 mti-brain-backend
```

### Production (Gunicorn + Uvicorn)

```bash
gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 2 --threads 2 \
  --timeout 480 --graceful-timeout 30 \
  --max-requests 1000 --max-requests-jitter 50 \
  --forwarded-allow-ips="*" \
  -b 0.0.0.0:8000
```

### PgBouncer Requirements

When using PgBouncer in **transaction-mode pooling**:

- Disable prepared statements: set `statement_cache_size=0` in asyncpg
- Pool size: 2–8 connections per app worker
- `DB_POOL_RECYCLE` must be less than PgBouncer's `SERVER_IDLE_TIMEOUT`
- The LangGraph checkpoint pool uses psycopg3 (not asyncpg) and also reconnects per-node for transaction-mode compatibility

---

## Observability

### Langfuse Tracing

Enable by setting `LANGFUSE_ENABLED=true` in `config.yml`.

Each pipeline invocation creates a single Langfuse trace:

| Trace attribute | Value |
|----------------|-------|
| `session_id` | `thread_id` |
| `user_id` | `user_email` |
| Observations | One per LLM call (prompt, response, model ARN, token counts, latency) |

The `langfuse_trace_id` is included in the SSE `done` event and stored in `mti_brain_execution_log`.

### Execution Log

Every pipeline run writes a row to `mti_brain_execution_log` with:

- `response_tone`, `deep_analysis` flags
- `duration_ms`, `token_usage`
- `langfuse_trace_id` for cross-linking to Langfuse
- `thread_id`, `conversation_id`, `user_id`

### Pipeline Graph Export

To export a visual diagram of the LangGraph DAG:

```bash
python scripts/render_graph.py
```
