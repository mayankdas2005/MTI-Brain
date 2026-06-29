"""
Single source of truth for all Neo4j write Cypher.

Both the ingestion pipeline (Neo4jLoader.apply_schema) and the backend
(neo4j_client._bootstrap_indexes) import SCHEMA_DDL and write functions
from this module. Any schema change in one place is automatically required
in the other.

Cohere Embed v4 = 1536 dimensions.
"""

from __future__ import annotations

from typing import Callable

_VEC = "OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}"

# ── Schema DDL — ALL constraints, FTS indexes, range indexes, vector indexes ──

SCHEMA_DDL: list[str] = [
    # ── Uniqueness constraints ────────────────────────────────────────────
    "CREATE CONSTRAINT tbl_fqn_unique        IF NOT EXISTS FOR (t:Table)        REQUIRE t.fqn     IS UNIQUE",
    "CREATE CONSTRAINT col_id_unique         IF NOT EXISTS FOR (c:Column)       REQUIRE c.id      IS UNIQUE",
    "CREATE CONSTRAINT community_id_unique   IF NOT EXISTS FOR (c:Community)    REQUIRE c.id      IS UNIQUE",
    "CREATE CONSTRAINT domain_name_unique    IF NOT EXISTS FOR (d:Domain)       REQUIRE d.name    IS UNIQUE",
    "CREATE CONSTRAINT intent_name_unique    IF NOT EXISTS FOR (i:Intent)       REQUIRE i.name    IS UNIQUE",
    "CREATE CONSTRAINT businessterm_unique   IF NOT EXISTS FOR (b:BusinessTerm) REQUIRE b.term    IS UNIQUE",
    "CREATE CONSTRAINT querytemplate_id      IF NOT EXISTS FOR (q:QueryTemplate) REQUIRE q.id     IS UNIQUE",
    "CREATE CONSTRAINT joinpath_id_unique    IF NOT EXISTS FOR (j:JoinPath)     REQUIRE j.id      IS UNIQUE",
    "CREATE CONSTRAINT querypattern_id_unique        IF NOT EXISTS FOR (q:QueryPattern) REQUIRE q.id        IS UNIQUE",
    "CREATE CONSTRAINT antipattern_id_unique         IF NOT EXISTS FOR (a:AntiPattern)  REQUIRE a.id        IS UNIQUE",
    "CREATE CONSTRAINT antipattern_merge_key_unique  IF NOT EXISTS FOR (a:AntiPattern)  REQUIRE a.merge_key IS UNIQUE",

    # ── Range indexes — Table ─────────────────────────────────────────────
    "CREATE INDEX table_domain          IF NOT EXISTS FOR (t:Table)  ON (t.business_domain)",
    "CREATE INDEX table_type            IF NOT EXISTS FOR (t:Table)  ON (t.table_type)",
    "CREATE INDEX table_community       IF NOT EXISTS FOR (t:Table)  ON (t.community_id)",
    "CREATE INDEX table_pagerank        IF NOT EXISTS FOR (t:Table)  ON (t.pagerank_score)",
    "CREATE INDEX table_join_role       IF NOT EXISTS FOR (t:Table)  ON (t.typical_join_role)",
    "CREATE INDEX table_is_time_series  IF NOT EXISTS FOR (t:Table)  ON (t.is_time_series)",
    "CREATE INDEX table_is_hub          IF NOT EXISTS FOR (t:Table)  ON (t.is_dimension_hub)",
    "CREATE INDEX table_enrich          IF NOT EXISTS FOR (t:Table)  ON (t.enrichment_status)",

    # ── Range indexes — Column ────────────────────────────────────────────
    "CREATE INDEX col_table             IF NOT EXISTS FOR (c:Column) ON (c.table_fqn)",
    "CREATE INDEX col_name              IF NOT EXISTS FOR (c:Column) ON (c.name)",
    "CREATE INDEX col_semantic          IF NOT EXISTS FOR (c:Column) ON (c.semantic_type)",
    "CREATE INDEX col_measurable        IF NOT EXISTS FOR (c:Column) ON (c.is_measurable)",
    "CREATE INDEX col_groupable         IF NOT EXISTS FOR (c:Column) ON (c.is_groupable)",
    "CREATE INDEX col_selectivity       IF NOT EXISTS FOR (c:Column) ON (c.filter_selectivity)",
    "CREATE INDEX col_surrogate         IF NOT EXISTS FOR (c:Column) ON (c.is_surrogate_key)",
    "CREATE INDEX col_enrich            IF NOT EXISTS FOR (c:Column) ON (c.enrichment_status)",
    "CREATE INDEX col_table_name        IF NOT EXISTS FOR (c:Column) ON (c.table_fqn, c.name)",

    # ── Range indexes — JoinPath ──────────────────────────────────────────
    "CREATE INDEX jp_quality            IF NOT EXISTS FOR (j:JoinPath) ON (j.quality_score)",
    "CREATE INDEX jp_hop_count          IF NOT EXISTS FOR (j:JoinPath) ON (j.hop_count)",
    "CREATE INDEX jp_from_fqn           IF NOT EXISTS FOR (j:JoinPath) ON (j.from_fqn)",
    "CREATE INDEX jp_to_fqn             IF NOT EXISTS FOR (j:JoinPath) ON (j.to_fqn)",

    # ── Relationship indexes — JOINS_TO ───────────────────────────────────
    "CREATE INDEX joins_confidence      IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.confidence)",
    "CREATE INDEX joins_source          IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.source)",
    "CREATE INDEX joins_canonical       IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.is_canonical)",
    "CREATE INDEX joins_from_table      IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.from_table)",
    "CREATE INDEX joins_to_table        IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.to_table)",
    "CREATE INDEX joins_declared        IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.is_declared)",

    # ── QueryPattern / AntiPattern ────────────────────────────────────────────
    "CREATE INDEX querypattern_first_seen IF NOT EXISTS FOR (q:QueryPattern) ON (q.first_seen)",
    "CREATE INDEX querypattern_last_seen  IF NOT EXISTS FOR (q:QueryPattern) ON (q.last_seen)",
    "CREATE INDEX antipattern_first_seen  IF NOT EXISTS FOR (a:AntiPattern)  ON (a.first_seen)",
    "CREATE INDEX antipattern_last_seen   IF NOT EXISTS FOR (a:AntiPattern)  ON (a.last_seen)",

    # ── QueryTemplate ─────────────────────────────────────────────────────
    "CREATE INDEX querytemplate_intent     IF NOT EXISTS FOR (q:QueryTemplate) ON (q.primary_intent)",
    "CREATE INDEX querytemplate_complexity IF NOT EXISTS FOR (q:QueryTemplate) ON (q.complexity)",
    "CREATE INDEX querytemplate_confidence IF NOT EXISTS FOR (q:QueryTemplate) ON (q.template_confidence)",

    # ── Vector indexes — Cohere Embed v4 @ 1536 dims ──────────────────────
    # (NOT for QueryPattern/AntiPattern — those are runtime nodes, created lazily)
    f"CREATE VECTOR INDEX tbl_cohere_embedding  IF NOT EXISTS FOR (t:Table)        ON (t.cohere_embedding)  {_VEC}",
    f"CREATE VECTOR INDEX col_cohere_embedding  IF NOT EXISTS FOR (c:Column)       ON (c.cohere_embedding)  {_VEC}",
    f"CREATE VECTOR INDEX querytemplate_cohere  IF NOT EXISTS FOR (q:QueryTemplate) ON (q.cohere_embedding) {_VEC}",
    f"CREATE VECTOR INDEX businessterm_cohere   IF NOT EXISTS FOR (b:BusinessTerm) ON (b.cohere_embedding)  {_VEC}",
    f"CREATE VECTOR INDEX intent_cohere         IF NOT EXISTS FOR (i:Intent)       ON (i.cohere_embedding)  {_VEC}",
    f"CREATE VECTOR INDEX community_cohere      IF NOT EXISTS FOR (c:Community)    ON (c.cohere_embedding)  {_VEC}",
    f"CREATE VECTOR INDEX domain_cohere         IF NOT EXISTS FOR (d:Domain)       ON (d.cohere_embedding)  {_VEC}",
]

# Fulltext indexes are recreated (dropped first) by the pipeline's apply_schema().
# The backend _bootstrap_indexes also creates them using IF NOT EXISTS.
FULLTEXT_DDL: list[str] = [
    """CREATE FULLTEXT INDEX table_ft_extended IF NOT EXISTS
       FOR (t:Table) ON EACH [t.name, t.description, t.synonyms, t.business_domain,
                              t.grain, t.intent_tags, t.natural_dimensions, t.natural_measures]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}""",
    """CREATE FULLTEXT INDEX col_ft_extended IF NOT EXISTS
       FOR (c:Column) ON EACH [c.name, c.description, c.synonyms, c.semantic_type,
                               c.value_vocabulary]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}""",
    """CREATE FULLTEXT INDEX querytemplate_ft IF NOT EXISTS
       FOR (q:QueryTemplate) ON EACH [q.question_text, q.description,
                                      q.required_aggregations, q.required_filters]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}""",
    """CREATE FULLTEXT INDEX businessterm_ft IF NOT EXISTS
       FOR (b:BusinessTerm) ON EACH [b.term, b.variants, b.description]""",
    """CREATE FULLTEXT INDEX intent_ft IF NOT EXISTS
       FOR (i:Intent) ON EACH [i.name, i.description]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}""",
    """CREATE FULLTEXT INDEX community_ft IF NOT EXISTS
       FOR (c:Community) ON EACH [c.description, c.dominant_domain]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}""",
    """CREATE FULLTEXT INDEX querypattern_ft IF NOT EXISTS
       FOR (q:QueryPattern) ON EACH [q.question_text, q.filter_summary]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}""",
]


# ── Write functions — all take a run_fn(cypher, **params) callable ────────────

def apply_schema(run_fn: Callable) -> None:
    """Apply all schema DDL. run_fn is the caller's Neo4j session executor."""
    for stmt in SCHEMA_DDL + FULLTEXT_DDL:
        try:
            run_fn(stmt)
        except Exception:
            pass  # IF NOT EXISTS makes these idempotent; log in caller if needed


def write_join_path(run_fn: Callable, path_data: dict) -> None:
    run_fn(
        "MERGE (jp:JoinPath {id: $id}) SET jp += $props",
        id=path_data["id"], props=path_data,
    )


def write_query_pattern(run_fn: Callable, pattern_data: dict, is_update: bool = False) -> None:
    if is_update:
        run_fn(
            """
            MATCH (qp:QueryPattern {id: $id})
            SET
              qp.occurrence_count = coalesce(qp.occurrence_count, 0) + 1,
              qp.last_seen        = datetime(),
              qp.sql_cte_outline  = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $sql_cte_outline  ELSE qp.sql_cte_outline  END,
              qp.join_outline     = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $join_outline     ELSE qp.join_outline     END,
              qp.filter_summary   = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $filter_summary   ELSE qp.filter_summary   END,
              qp.tables_used      = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $tables_used      ELSE qp.tables_used      END,
              qp.confidence_score = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $confidence_score ELSE qp.confidence_score END,
              qp.repair_count     = CASE WHEN $repair_count     < coalesce(qp.repair_count,     9999) THEN $repair_count     ELSE qp.repair_count     END,
              qp.recompile_count  = CASE WHEN $recompile_count  < coalesce(qp.recompile_count,  9999) THEN $recompile_count  ELSE qp.recompile_count  END
            """,
            **pattern_data,
        )
    else:
        run_fn(
            """
            MERGE (qp:QueryPattern {id: $id})
            ON CREATE SET
              qp.question_text    = $question_text,
              qp.sql_cte_outline  = $sql_cte_outline,
              qp.join_outline     = $join_outline,
              qp.filter_summary   = $filter_summary,
              qp.tables_used      = $tables_used,
              qp.intent           = $intent,
              qp.complexity       = $complexity,
              qp.user_id          = $user_id,
              qp.cohere_embedding = $cohere_embedding,
              qp.confidence_score = $confidence_score,
              qp.row_count        = $row_count,
              qp.recompile_count  = $recompile_count,
              qp.repair_count     = $repair_count,
              qp.promotion_status = 'active',
              qp.is_enabled       = true,
              qp.liked_count      = 0,
              qp.disliked_count   = 0,
              qp.first_seen       = datetime(),
              qp.occurrence_count = 1
            ON MATCH SET
              qp.occurrence_count = coalesce(qp.occurrence_count, 0) + 1,
              qp.last_seen        = datetime(),
              qp.sql_cte_outline  = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $sql_cte_outline  ELSE qp.sql_cte_outline  END,
              qp.join_outline     = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $join_outline     ELSE qp.join_outline     END,
              qp.filter_summary   = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $filter_summary   ELSE qp.filter_summary   END,
              qp.tables_used      = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $tables_used      ELSE qp.tables_used      END,
              qp.confidence_score = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $confidence_score ELSE qp.confidence_score END,
              qp.repair_count     = CASE WHEN $repair_count     < coalesce(qp.repair_count,     9999) THEN $repair_count     ELSE qp.repair_count     END,
              qp.recompile_count  = CASE WHEN $recompile_count  < coalesce(qp.recompile_count,  9999) THEN $recompile_count  ELSE qp.recompile_count  END
            """,
            **pattern_data,
        )


def write_anti_pattern(run_fn: Callable, pattern_data: dict) -> None:
    run_fn(
        """
        MERGE (ap:AntiPattern {merge_key: $merge_key})
        ON CREATE SET
          ap.id               = $id,
          ap.question_text    = $question_text,
          ap.sql_fragment     = $sql_fragment,
          ap.error_type       = $error_type,
          ap.error_summary    = $error_summary,
          ap.failing_element  = $failing_element,
          ap.tables_involved  = $tables_involved,
          ap.intent           = $intent,
          ap.complexity       = $complexity,
          ap.cohere_embedding = $cohere_embedding,
          ap.is_enabled       = true,
          ap.first_seen       = datetime(),
          ap.occurrence_count = 1
        ON MATCH SET
          ap.occurrence_count = ap.occurrence_count + 1,
          ap.last_seen        = datetime(),
          ap.sql_fragment     = $sql_fragment,
          ap.error_summary    = $error_summary,
          ap.failing_element  = CASE WHEN $failing_element <> '' THEN $failing_element ELSE ap.failing_element END
        """,
        **pattern_data,
    )


def write_promote_query_pattern_to_template(run_fn: Callable, qp_data: dict) -> None:
    """Promote a runtime QueryPattern to a validated QueryTemplate after sufficient executions."""
    qt_id = "runtime_" + qp_data["id"]
    run_fn(
        """
        MERGE (qt:QueryTemplate {id: $qt_id})
        SET qt.question_text           = $question_text,
            qt.source                  = 'runtime_promoted',
            qt.is_validated            = true,
            qt.template_confidence     = 1.0,
            qt.anchor_fqns_verified    = true,
            qt.anchor_table_fqns       = $tables_used,
            qt.anchor_table_fqns_resolved = $tables_used,
            qt.primary_intent          = $intent,
            qt.complexity              = $complexity,
            qt.cte_steps               = [],
            qt.cohere_embedding        = null
        WITH qt
        MATCH (qp:QueryPattern {id: $qp_id})
        MERGE (qt)-[:PROMOTED_FROM]->(qp)
        """,
        qt_id=qt_id,
        question_text=qp_data.get("question_text", ""),
        tables_used=qp_data.get("tables_used", []),
        intent=qp_data.get("intent", ""),
        complexity=qp_data.get("complexity", "complex"),
        qp_id=qp_data["id"],
    )
