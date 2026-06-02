# Semantic Model Generator

Transforms Redshift data warehouse metadata and RDF/R2RML ontology mappings into a richly annotated Neo4j knowledge graph. The graph powers semantic search, text-to-SQL intent routing, and automated documentation for the treasury and payments data warehouse (`lpp` schema).

---

## Architecture

A 12-step pipeline that reads from Redshift, enriches with AWS Bedrock LLMs and Cohere embeddings, and writes into Neo4j. Steps are idempotent (all writes use `MERGE + SET`) and can be run individually or in any subset.

```
EXTRACT → INFER → LOAD → WCC → GDS → ENRICH → PATHS → ROLLUP → INTENTS → GLOSSARY → TEMPLATES → EMBED
```

---

## Setup

### Dependencies

```
pip install -r graph/requirements.txt
```

| Package | Version |
|---------|---------|
| neo4j | >= 6.2.0 |
| boto3 | >= 1.43.3 |
| pydantic | >= 2.12.5 |
| pydantic-settings | >= 2.13.1 |
| PyYAML | >= 6.0.1 |
| rdflib | >= 7.6.0 |
| tenacity | >= 9.1.4 |
| tqdm | >= 4.67.3 |
| redshift-connector | >= 2.1.13 |

### Environment Variables

Create a `.env` file in the `semantic_model_generator/` directory:

```ini
# Redshift
REDSHIFT_HOST=<host>
REDSHIFT_PORT=5439
REDSHIFT_DB=<database>
REDSHIFT_USER=<user>
REDSHIFT_PASSWORD=<password>
REDSHIFT_SCHEMA=lpp

# Neo4j
NEO4J_URI=bolt://<host>:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<password>
NEO4J_DB=neo4j

# AWS Bedrock
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGION=us-west-2
AWS_BEDROCK_SONNET_ARN=<Claude Sonnet cross-region inference profile ARN>
AWS_BEDROCK_HAIKU_ARN=<Claude Haiku cross-region inference profile ARN>
AWS_BEDROCK_COHERE_EMBED_V4_ARN=<Cohere Embed v4 model ARN>
```

---

## Running the Pipeline

```bash
python -m semantic_model_generator.graph.pipeline [--steps STEPS] [--dry-run] [--reset-checkpoint]
```

### Flags

| Flag | Description |
|------|-------------|
| `--steps` | Comma-separated step names or `all` (default: `all`) |
| `--dry-run` | Parse and infer without writing to Neo4j |
| `--reset-checkpoint` | Delete `graph_enrichment_cache.json` and re-run enrichment from scratch |
| `--reset-templates` | Clear only the templates cache from the checkpoint so templates re-enrich |
| `--reset-glossary` | Clear only the glossary cache from the checkpoint so glossary re-enriches |
| `--reset-communities` | Clear only the community descriptions cache so communities re-enrich |

### Examples

```bash
# Full pipeline
python -m semantic_model_generator.graph.pipeline

# Dry run — validate extraction and inference only
python -m semantic_model_generator.graph.pipeline --steps extract,infer --dry-run

# Re-run only the enrichment and embedding steps
python -m semantic_model_generator.graph.pipeline --steps enrich,embed

# Resume after a crash (checkpoint is preserved automatically)
python -m semantic_model_generator.graph.pipeline --steps enrich,glossary,templates,embed

# Force re-enrichment from scratch
python -m semantic_model_generator.graph.pipeline --steps enrich,embed --reset-checkpoint
```

---

## Pipeline Steps

### 1. `extract`

Reads source metadata and ontology mappings.

- Parses `output/semantic_model.yml` (R2RML) to build `TableMeta`, `ColumnMeta`, and semantic FK edges (`sme_edges`)
- Runs 16 Redshift queries (Q1–Q16) scoped to the tables defined in the YML:
  - Table stats: row count, size, distribution style, sort key (Q1)
  - Column definitions: data type, ordinal position, nullability (Q2)
  - pg_stats: null fraction, n_distinct (Q3)
  - PK/FK constraints (Q4)
  - Query history (STL): join patterns and frequency (Q5–Q7)
  - Sample values for low-cardinality columns; frequency-ranked top values (Q8, Q_topvals)
  - Shared column names across tables (Q15)

**Outputs in memory**: `tables_meta`, `columns_meta`, `sme_edges`, `raw` dict

---

### 2. `infer`

Enriches metadata with statistical inference.

- Updates `TableMeta` stats (row count, size, diststyle, sortkey) from Redshift Q1 results
- Infers table type using 8 signals (row count, column patterns, sortkey, encoding, distribution): `fact`, `dimension`, `bridge`, `reference`, `staging`, `derived`
- Merges Redshift column stats (null_frac, n_distinct, data type) into YML-defined `ColumnMeta`
- Infers FK edges using 3-tier confidence scoring:
  - Tier 1 (0.90–0.99): confirmed joins from query history
  - Tier 2 (0.85): exact column name + type + cardinality match
  - Tier 3 (0.75+): normalized name match + integer type
- Computes source hashes on tables and columns for change detection

**Outputs in memory**: enriched `tables_meta`, `columns_meta`, `col_map`, `all_fk_edges`

---

### 3. `load`

Applies Neo4j schema and writes all nodes and edges.

- Creates uniqueness constraints, range indexes, composite indexes, TEXT indexes, full-text indexes, and vector indexes (see [Neo4j Schema](#neo4j-schema))
- MERGE-writes `Table`, `Column`, `Domain` nodes and `HAS_COLUMN`, `JOINS_TO` edges in batches of 500
- On MATCH: bumps `version` and sets `enrichment_status = 'stale'` if `source_hash` changed
- Initializes default property values on all node types

---

### 4. `wcc`

Identifies isolated tables and bridges them into the main connected component.

- Projects a GDS in-memory graph on `JOINS_TO` edges
- Runs Weakly Connected Components (WCC) and reports isolated tables, small clusters, and pendants
- Bridges isolated tables using shared column names from Q15 (Redshift query history), skipping generic PK column names; confidence = 0.80, source = `wcc_shared_column`
- Re-runs WCC after bridging to verify connectivity
- Sets `is_isolated = true` on tables that remain unconnected

---

### 5. `gds`

Runs Graph Data Science algorithms for centrality and community detection.

- **PageRank**: table centrality score
- **Betweenness Centrality**: table bridge importance
- **Leiden community detection** (gamma=1.2; retried at gamma=1.5 if quality check fails): groups tables into business communities; creates `Community` nodes linked via `CONTAINS_TABLE`
- **SCC (Strongly Connected Components)**: identifies strongly connected table clusters
- **Triangle Count**: structural density metric per table
- **Degree Centrality**: in/out degree score per table
- **FastRP + Node Similarity**: structural similarity between tables (cutoff=0.5, top_k=10)

---

### 6. `enrich`

LLM-powered enrichment using AWS Bedrock Claude Sonnet via LangChain structured output.

Runs in 3 phases with checkpoint caching (`graph_enrichment_cache.json`). Only processes nodes with `enrichment_status IN ['pending', 'stale', 'failed']`.

**Phase 1 — Column enrichment** (batched by table, flushed every 5 tables):
- For each column: `description`, `semantic_type` (identifier/measure/dimension/date/flag/amount/code/free_text/percentage/ratio), `synonyms`, `is_pii`, `pii_type`, `temporal_grain`, `default_aggregation`, `value_aliases` (for code/flag columns with n_distinct ≤ 20), `value_scale`

**Phase 2 — Table enrichment** (batched 5 tables per Bedrock call):
- Uses pre-enriched column descriptions from Phase 1 as context
- For each table: `description`, `business_domain`, `grain`, `synonyms`, `table_type_override`
- Business domains: banking, cash_and_liquidity, forecasting, payments, card_acquiring, working_capital, erp_reconciliation, corporate, debt_and_capital, fx_and_hedging, investments, knowledge_graph, fraud, benchmarking, reference, staging
- Creates `Domain` nodes and `BELONGS_TO` edges from enriched `business_domain` values

**Phase 3 — Domain voting + Community + Domain enrichment**:
- Domain voting: updates `dominant_domain` and `dominant_domain_confidence` on each `Community` node using PageRank-weighted table domains
- Community enrichment: generates `description` and `query_patterns` (3 example questions per community)
- Domain enrichment: generates a 2-3 sentence `description` per `Domain` node

---

### 7. `paths`

Precomputes join paths for query planning.

- Runs `run_post_enrich_passes()` first to derive `typical_join_role`, `is_time_series`, `natural_dimensions`, `recommended_join_type`, `ambiguity_risk`, and `is_canonical` on edges
- **Dijkstra all-pairs** (max 6 hops): shortest join paths between all table pairs
- **Yen's k-shortest** (k=3, max 6 hops): top 3 alternative paths per pair
- **Cross-community paths** (max 6 hops): paths spanning different Leiden communities
- Computes quality scores on all `JoinPath` nodes

---

### 8. `rollup`

Detects aggregate/rollup tables and marks subquery anchors.

- Identifies rollup candidates from graph structure (many-to-one join patterns)
- Validates candidates using Claude Sonnet with table descriptions
- Creates `ROLLUP_OF` edges with `window_type`, `window_days`, and `confidence`
- Sets `is_rollup = true` and `rollup_base_fqn` on rollup tables
- Sets `is_subquery_anchor = true` on high-betweenness fact tables (above 75th percentile)

---

### 9. `intents`

Builds the intent routing layer from `output/intent_classes.json`.

- Loads 16 intent classes (e.g., `balance_and_policy`, `cash_position`, `code_lookup`) mapped to ontology class names
- Generates LLM descriptions for each intent using enriched table context
- Creates `Intent` nodes and `RELEVANT_TO` edges (Table → Intent) with confidence scores
- Sets `intent_tags`, `intent_tags_text`, and `intent_tags_scored` on each `Table` node

---

### 10. `glossary`

Extracts business terminology from the enriched graph.

- Scans column `synonyms`, `value_aliases`, `value_vocabulary`, and `semantic_type` from Neo4j (up to 300 context lines)
- Batches context in groups of 15 and calls Claude Sonnet to identify business terms: financial abbreviations, entity aliases, product/value synonyms, unit terms, treasury-specific metrics
- Creates `BusinessTerm` nodes with `variants`, `term_type` (abbreviation/entity_alias/unit/metric/product), and `description`
- Fully resumable: tracks `glossary_context_offset` in the checkpoint file

---

### 11. `templates`

Enriches query templates from `output/Questions.txt`.

- Loads questions (one per line) and enriches each with: `description`, `primary_intent`, `intent_scores` (dict of intent → confidence), `complexity` (simple/complex/advanced), `anchor_table_fqns`, `cte_steps`, `required_aggregations`, `required_filters`, `time_windowed`, `sql_pattern`, `is_cross_domain`, `min_cte_count`, `max_cte_count`
- Creates `QueryTemplate` nodes (id format: `qt_NNN`) linked to `Intent` nodes via `CLASSIFIED_AS` and to `Table` nodes via `REQUIRES_TABLE`
- Batched in groups of 10; resumable via `query_templates` key in checkpoint

---

### 12. `embed`

Generates Cohere Embed v4 embeddings (1536-dim) for all semantic entities.

Embedding text construction per entity type:

| Entity | Text |
|--------|------|
| Column | `name description synonyms_text value_vocabulary` |
| Table | `name business_domain description synonyms_text top_col_names` |
| Intent | `name description` |
| Community | `id dominant_domain description` |
| Domain | `name description` |
| BusinessTerm | `term description` |
| QueryTemplate | `question_text` |

After embedding, runs KNN on column embeddings (cutoff=0.88, k=10) to create `SEMANTICALLY_SIMILAR` edges between semantically related columns.

---

## Neo4j Schema

### Node Labels

| Label | Unique Key | Description |
|-------|-----------|-------------|
| `Table` | `fqn` | Redshift table (`schema.table_name`) |
| `Column` | `id` | Column (`schema.table.column`) |
| `Domain` | `name` | Business domain |
| `Community` | `id` | Leiden community cluster |
| `Intent` | `name` | Analytical intent class |
| `BusinessTerm` | `term` | Business glossary entry |
| `QueryTemplate` | `id` | Enriched query template (`qt_NNN`) |
| `JoinPath` | `id` | Precomputed join path between two tables |
| `PipelineRun` | `run_id` | Audit record for each pipeline execution |

### Relationship Types

| Relationship | From → To | Key Properties |
|-------------|-----------|----------------|
| `HAS_COLUMN` | Table → Column | — |
| `JOINS_TO` | Table → Table | `from_col`, `to_col`, `confidence`, `source`, `is_declared`, `is_ontology`, `is_canonical`, `recommended_join_type`, `ambiguity_risk` |
| `BELONGS_TO` | Table → Domain | — |
| `CONTAINS_TABLE` | Community → Table | — |
| `RELEVANT_TO` | Table → Intent | `confidence` |
| `ROLLUP_OF` | Table → Table | `window_type`, `window_days`, `confidence` |
| `SEMANTICALLY_SIMILAR` | Column → Column | KNN cosine similarity |
| `CLASSIFIED_AS` | QueryTemplate → Intent | `confidence` |
| `REQUIRES_TABLE` | QueryTemplate → Table | — |

### Indexes

**Uniqueness constraints**: Table.fqn, Column.id, Domain.name, Community.id, JoinPath.id, PipelineRun.run_id, Intent.name, BusinessTerm.term, QueryTemplate.id

**Range indexes — Table**: business_domain, table_type, community_id, wcc_component_id, is_dimension_hub, enrichment_status, is_time_series, typical_join_role, is_subquery_anchor, is_rollup, intent_tags

**Range indexes — Column**: table_fqn, name, semantic_type, is_pii, is_pk, enrichment_status, is_groupable, is_measurable, temporal_grain

**Composite**: Column(table_fqn, name)

**TEXT indexes**: Table.name, Column.name

**Range indexes — QueryTemplate**: primary_intent, complexity

**Relationship indexes — JOINS_TO**: confidence, is_ontology, source, is_canonical, has_nullable_fk, from_table, to_table

**Full-text indexes** (English analyzer, array properties indexed natively):
- `table_ft_extended`: Table(name, description, synonyms, business_domain, grain, intent_tags, natural_dimensions, natural_measures)
- `col_ft_extended`: Column(name, description, synonyms, semantic_type, value_vocabulary)
- `querytemplate_ft`: QueryTemplate(question_text, description, required_aggregations, required_filters)
- `businessterm_ft`: BusinessTerm(term, variants, description)
- `intent_ft`: Intent(name, description)
- `community_ft`: Community(description, dominant_domain)
- `querypattern_ft`: QueryPattern(question_text, filter_summary)

**Vector indexes** (1536-dim, cosine similarity):
- `col_cohere_embedding`, `tbl_cohere_embedding`, `querytemplate_cohere`, `businessterm_cohere`, `intent_cohere`, `community_cohere`, `domain_cohere`

---

## Output Files

| File | Description |
|------|-------------|
| `output/semantic_model.yml` | R2RML mappings — 159 subdomains with ontology classes and column-level predicate mappings. Source of truth for table/column definitions passed to the pipeline. |
| `output/intent_classes.json` | 16 intent classes mapped to lists of ontology class names. Input for the `intents` and `templates` steps. |
| `output/Questions.txt` | One business question per line. Enriched into `QueryTemplate` nodes by the `templates` step. |
| `graph_enrichment_cache.json` | LLM + embedding checkpoint. Keys: `tables`, `columns`, `communities`, `glossary_terms`, `glossary_context_offset`, `query_templates`. Delete with `--reset-checkpoint` to start fresh. |

---

## Key Source Files

| File | Role |
|------|------|
| `graph/pipeline.py` | Main orchestration — all 12 steps, CLI entry point |
| `graph/config.py` | Pydantic-settings config classes for Redshift, Neo4j, and Bedrock |
| `graph/models.py` | `TableMeta`, `ColumnMeta`, `FKEdge` dataclasses with source hashing |
| `graph/extract/redshift.py` | 16 Redshift queries + sample value extraction |
| `graph/extract/yml_parser.py` | R2RML `semantic_model.yml` → `TableMeta`/`ColumnMeta`/`FKEdge` |
| `graph/infer/fk_infer.py` | 3-tier FK confidence scoring |
| `graph/infer/table_type.py` | 8-signal statistical table type classification |
| `graph/load/neo4j_loader.py` | Neo4j MERGE writes, schema DDL, post-enrich Cypher passes |
| `graph/gds/algorithms.py` | WCC, PageRank, Betweenness, Leiden, FastRP, Node Similarity, KNN |
| `graph/gds/join_paths.py` | JoinPath precomputation (Dijkstra, Yen's, cross-community) |
| `graph/enrich/llm_enricher.py` | Bedrock enrichment with Pydantic structured output + checkpoint |
| `graph/enrich/embeddings.py` | Cohere Embed v4 (batch size 96) |
| `graph/enrich/intents.py` | Intent node and RELEVANT_TO edge construction |
| `graph/enrich/rollup.py` | ROLLUP_OF edge detection and LLM validation |
| `generate_new_semantic_model.py` | Standalone RDF/R2RML extractor — generates `semantic_model.yml` from TTL ontology files |

---

## Troubleshooting

**Enrichment crashed mid-run**

The checkpoint at `graph_enrichment_cache.json` records all completed LLM and embedding calls. Re-run the same steps — already-processed nodes are skipped automatically.

**Want to re-enrich specific tables**

Set `enrichment_status = 'stale'` on the target Table nodes in Neo4j, then re-run `--steps enrich,embed`.

**Checkpoint is corrupt or stale**

```bash
python -m semantic_model_generator.graph.pipeline --steps enrich,embed --reset-checkpoint
```

**GDS projection fails**

The `gds` step drops and re-projects the `join_graph` in-memory graph before running algorithms. If GDS errors on a missing projection, re-running the step is safe.

**Leiden community quality warning**

If the quality check fails at gamma=1.2, the pipeline retries at gamma=1.5 automatically. A second failure logs a warning but does not abort — downstream steps use whatever communities were assigned.

**Tables missing embeddings after embed step**

The embed step only processes nodes where `cohere_embedding IS NULL OR enrichment_status = 'stale'`. Re-run `--steps embed` after confirming Bedrock ARN and credentials are correct.