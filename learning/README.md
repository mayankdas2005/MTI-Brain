# learning/

Experimental and exploratory resources for the MTI Brain project. Nothing in this folder is part of the production system — these files are research spikes and proof-of-concept implementations used to evaluate patterns before (or instead of) integrating them into the main application.

## Contents

### `neo4j-memory.py`

A standalone Python script that evaluates the [`neo4j_agent_memory`](https://pypi.org/project/neo4j-agent-memory/) library as a long-term memory backend for the analytics pipeline.

**What it does:**

1. Connects to Neo4j using credentials from `../backend/.env`
2. Configures a `MemoryClient` with:
   - **LLM:** AWS Bedrock Claude Haiku (via LiteLLM adapter)
   - **Embeddings:** AWS Bedrock Cohere Embed v4 (1536-dim, via LiteLLM)
   - **Entity extraction:** spaCy (`en_core_web_lg`) + GLiNER (`gliner_large-v2.5`) pipeline
3. Adds a sample user question and assistant response to a test session
4. Retrieves the conversation and runs a semantic search over it
5. Logs timing for each operation to `neo4j-memory-debug.log`

**When to use it:** Run this to benchmark `neo4j_agent_memory` latency or test a new Neo4j database configuration before wiring it into `backend/app/services/agents/memory/`.

**Prerequisites:**

```bash
pip install neo4j-agent-memory python-dotenv litellm spacy
python -m spacy download en_core_web_lg
# GLiNER model is downloaded automatically on first run
```

**Run:**

```bash
cd learning
python neo4j-memory.py
# Logs written to learning/neo4j-memory-debug.log
```

The script reads `../backend/.env` for `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK`, `AWS_BEDROCK_HAIKU_ARN`, and `AWS_BEDROCK_COHERE_EMBED_V4_ARN`. It writes to the `graphacademy` Neo4j database — not the production `neo4j` database — so it is safe to run against a shared instance.

---

### `neo4j-agent-memory.ipynb`

A Jupyter notebook companion to `neo4j-memory.py`. Explores the same `neo4j_agent_memory` library interactively, with cell-by-cell execution for easier inspection of responses, entity graphs, and memory retrieval results.

**Run:**

```bash
cd learning
jupyter notebook neo4j-agent-memory.ipynb
```

---

## Relationship to Production Code

The production long-term memory implementation lives at:

```
backend/app/services/agents/memory/
  short_term.py   — session-scoped message history
  long_term.py    — PostgresStore-backed semantic memory (Cohere Embed v4)
```

The `learning/` scripts explore Neo4j as an alternative memory store. If the approach is promoted to production, it would replace or supplement `long_term.py`.

---

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI + LangGraph pipeline) | [../backend/README.md](../backend/README.md) |
| Assets (pipeline diagrams) | [../assets/README.md](../assets/README.md) |
