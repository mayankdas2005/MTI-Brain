"""
Neo4j loader — apply schema (constraints/indexes) and MERGE all nodes + edges.

All writes use MERGE + SET so the pipeline is idempotent.
Batched transactions of 500 rows; retried up to 3× on transient errors.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from neo4j import GraphDatabase
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import ColumnMeta, FKEdge, TableMeta
from ..utils import is_uuid_col

log = logging.getLogger(__name__)

_BATCH = 500
_NOW = lambda: datetime.now(timezone.utc).isoformat()


class Neo4jLoader:
    def __init__(self, uri: str, user: str, password: str, db: str):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = db

    def close(self):
        self._driver.close()

    # ── Internal helpers ───────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _run(self, cypher: str, **params) -> list[dict]:
        with self._driver.session(database=self._db) as s:
            return s.run(cypher, **params).data()

    def _batch_write(self, cypher: str, rows: list[dict], **kwargs) -> int:
        written = 0
        for i in range(0, len(rows), _BATCH):
            chunk = rows[i : i + _BATCH]
            self._run(cypher, rows=chunk, **kwargs)
            written += len(chunk)
        return written

    # ── Schema setup ───────────────────────────────────────────────────────

    def apply_schema(self):
        log.info("Applying Neo4j constraints and indexes …")
        ddl = [
            # Uniqueness constraints
            "CREATE CONSTRAINT table_fqn_unique      IF NOT EXISTS FOR (t:Table)        REQUIRE t.fqn IS UNIQUE",
            "CREATE CONSTRAINT column_id_unique      IF NOT EXISTS FOR (c:Column)       REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT domain_name_unique    IF NOT EXISTS FOR (d:Domain)       REQUIRE d.name IS UNIQUE",
            "CREATE CONSTRAINT community_id_unique   IF NOT EXISTS FOR (c:Community)    REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT joinpath_id_unique    IF NOT EXISTS FOR (j:JoinPath)     REQUIRE j.id IS UNIQUE",
            "CREATE CONSTRAINT intent_name_unique    IF NOT EXISTS FOR (i:Intent)       REQUIRE i.name IS UNIQUE",
            "CREATE CONSTRAINT businessterm_unique   IF NOT EXISTS FOR (b:BusinessTerm) REQUIRE b.term IS UNIQUE",
            "CREATE CONSTRAINT querytemplate_id      IF NOT EXISTS FOR (q:QueryTemplate) REQUIRE q.id IS UNIQUE",

            "CREATE CONSTRAINT tbl_fqn_unique IF NOT EXISTS FOR (t:Table) REQUIRE t.fqn IS UNIQUE",
            "CREATE CONSTRAINT col_id_unique IF NOT EXISTS FOR (c:Column) REQUIRE c.id IS UNIQUE",
            
            # RANGE indexes — Table
            "CREATE INDEX table_domain          IF NOT EXISTS FOR (t:Table)  ON (t.business_domain)",
            "CREATE INDEX table_type            IF NOT EXISTS FOR (t:Table)  ON (t.table_type)",
            "CREATE INDEX table_community       IF NOT EXISTS FOR (t:Table)  ON (t.community_id)",
            "CREATE INDEX table_wcc             IF NOT EXISTS FOR (t:Table)  ON (t.wcc_component_id)",
            "CREATE INDEX table_is_dimension_hub IF NOT EXISTS FOR (t:Table)  ON (t.is_dimension_hub)",
            "CREATE INDEX table_enrich          IF NOT EXISTS FOR (t:Table)  ON (t.enrichment_status)",
            "CREATE INDEX table_is_time_series  IF NOT EXISTS FOR (t:Table)  ON (t.is_time_series)",
            "CREATE INDEX table_join_role       IF NOT EXISTS FOR (t:Table)  ON (t.typical_join_role)",
            "CREATE INDEX table_subq_anchor     IF NOT EXISTS FOR (t:Table)  ON (t.is_subquery_anchor)",
            "CREATE INDEX table_rollup          IF NOT EXISTS FOR (t:Table)  ON (t.is_rollup)",
            "CREATE INDEX table_intent_tags     IF NOT EXISTS FOR (t:Table)  ON (t.intent_tags)",
            # RANGE indexes — Column
            "CREATE INDEX col_table             IF NOT EXISTS FOR (c:Column) ON (c.table_fqn)",
            "CREATE INDEX col_name              IF NOT EXISTS FOR (c:Column) ON (c.name)",
            "CREATE INDEX col_semantic          IF NOT EXISTS FOR (c:Column) ON (c.semantic_type)",
            "CREATE INDEX col_pii               IF NOT EXISTS FOR (c:Column) ON (c.is_pii)",
            "CREATE INDEX col_pk                IF NOT EXISTS FOR (c:Column) ON (c.is_pk)",
            "CREATE INDEX col_enrich            IF NOT EXISTS FOR (c:Column) ON (c.enrichment_status)",
            "CREATE INDEX col_groupable         IF NOT EXISTS FOR (c:Column) ON (c.is_groupable)",
            "CREATE INDEX col_measurable        IF NOT EXISTS FOR (c:Column) ON (c.is_measurable)",
            "CREATE INDEX col_temporal_grain    IF NOT EXISTS FOR (c:Column) ON (c.temporal_grain)",
            # Composite
            "CREATE INDEX col_table_name        IF NOT EXISTS FOR (c:Column) ON (c.table_fqn, c.name)",
            # TEXT indexes
            "CREATE TEXT INDEX table_name_text  IF NOT EXISTS FOR (t:Table)  ON (t.name)",
            "CREATE TEXT INDEX column_name_text IF NOT EXISTS FOR (c:Column) ON (c.name)",
            # Relationship indexes
            "CREATE INDEX joins_confidence      IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.confidence)",
            "CREATE INDEX joins_is_ontology     IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.is_ontology)",
            "CREATE INDEX joins_source          IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.source)",
            "CREATE INDEX joins_canonical       IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.is_canonical)",
            "CREATE INDEX joins_null_fk         IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.has_nullable_fk)",
            "CREATE INDEX joins_from_table      IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.from_table)",
            "CREATE INDEX joins_to_table        IF NOT EXISTS FOR ()-[r:JOINS_TO]-() ON (r.to_table)",
            # JoinPath
            "CREATE INDEX joinpath_cross        IF NOT EXISTS FOR (j:JoinPath) ON (j.is_cross_community)",
            # QueryTemplate
            "CREATE INDEX querytemplate_intent     IF NOT EXISTS FOR (q:QueryTemplate) ON (q.primary_intent)",
            "CREATE INDEX querytemplate_complexity IF NOT EXISTS FOR (q:QueryTemplate) ON (q.complexity)",
        ]
        for stmt in ddl:
            self._run(stmt)

        # Full-text indexes — drop and recreate so property sets stay current.
        # Neo4j 5.x indexes array properties natively — no synonyms_text flat strings needed.
        for idx_name in [
            "table_fulltext", "column_fulltext",
            "table_ft_extended", "col_ft_extended",
            "querytemplate_ft", "intent_ft", "community_ft", "querypattern_ft",
        ]:
            try:
                self._run(f"DROP INDEX {idx_name} IF EXISTS")
            except Exception:
                pass

        self._run("""
            CREATE FULLTEXT INDEX table_ft_extended IF NOT EXISTS
            FOR (t:Table) ON EACH [t.name, t.description, t.synonyms, t.business_domain,
                                   t.grain, t.intent_tags, t.natural_dimensions, t.natural_measures]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)
        self._run("""
            CREATE FULLTEXT INDEX col_ft_extended IF NOT EXISTS
            FOR (c:Column) ON EACH [c.name, c.description, c.synonyms, c.semantic_type,
                                    c.value_vocabulary]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)
        self._run("""
            CREATE FULLTEXT INDEX querytemplate_ft IF NOT EXISTS
            FOR (q:QueryTemplate) ON EACH [q.question_text, q.description,
                                           q.required_aggregations, q.required_filters]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)
        self._run("""
            CREATE FULLTEXT INDEX businessterm_ft IF NOT EXISTS
            FOR (b:BusinessTerm) ON EACH [b.term, b.variants, b.description]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)
        self._run("""
            CREATE FULLTEXT INDEX intent_ft IF NOT EXISTS
            FOR (i:Intent) ON EACH [i.name, i.description]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)
        self._run("""
            CREATE FULLTEXT INDEX community_ft IF NOT EXISTS
            FOR (c:Community) ON EACH [c.description, c.dominant_domain]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)
        self._run("""
            CREATE FULLTEXT INDEX querypattern_ft IF NOT EXISTS
            FOR (q:QueryPattern) ON EACH [q.question_text, q.filter_summary]
            OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}
        """)

        # Vector indexes — Cohere Embed v4 uses 1536 dimensions
        _VEC_OPTS = "OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}"
        for vec_ddl in [
            f"CREATE VECTOR INDEX col_cohere_embedding IF NOT EXISTS FOR (c:Column) ON (c.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX tbl_cohere_embedding IF NOT EXISTS FOR (t:Table) ON (t.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX querytemplate_cohere IF NOT EXISTS FOR (q:QueryTemplate) ON (q.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX businessterm_cohere IF NOT EXISTS FOR (b:BusinessTerm) ON (b.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX intent_cohere IF NOT EXISTS FOR (i:Intent) ON (i.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX community_cohere IF NOT EXISTS FOR (c:Community) ON (c.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX domain_cohere IF NOT EXISTS FOR (d:Domain) ON (d.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX querypattern_cohere_embedding IF NOT EXISTS FOR (q:QueryPattern) ON (q.cohere_embedding) {_VEC_OPTS}",
            f"CREATE VECTOR INDEX antipattern_cohere_embedding IF NOT EXISTS FOR (a:AntiPattern) ON (a.cohere_embedding) {_VEC_OPTS}",
        ]:
            self._run(vec_ddl)

        self._cleanup_stale_patterns()
        log.info("Schema applied.")

    def _cleanup_stale_patterns(self):
        """Delete runtime pattern nodes written by old code that lacked required properties."""
        deleted_qp = self._run(
            "MATCH (qp:QueryPattern) WHERE qp.tables_used IS NULL DETACH DELETE qp RETURN count(*) AS n"
        )
        deleted_ap = self._run(
            "MATCH (ap:AntiPattern) WHERE ap.error_type IS NULL DETACH DELETE ap RETURN count(*) AS n"
        )
        n_qp = deleted_qp[0]["n"] if deleted_qp else 0
        n_ap = deleted_ap[0]["n"] if deleted_ap else 0
        if n_qp or n_ap:
            log.info("Cleaned up stale pattern nodes: QueryPattern=%d AntiPattern=%d", n_qp, n_ap)

    # ── Domain nodes ───────────────────────────────────────────────────────

    def load_domains(self, domain_names: list[str]):
        now = _NOW()
        rows = [{"name": d, "created_at": now} for d in domain_names]
        n = self._batch_write("""
            UNWIND $rows AS r
            MERGE (d:Domain {name: r.name})
            ON CREATE SET d.created_at = r.created_at,
                          d.updated_at = r.created_at
        """, rows)
        log.info("Loaded %d Domain nodes.", n)

    # ── Table nodes ────────────────────────────────────────────────────────

    def load_tables(self, tables: list[TableMeta]):
        now = _NOW()
        rows = []
        for t in tables:
            rows.append({
                "fqn":                  t.fqn,
                "name":                 t.name,
                "schema":               t.schema,
                "ontology_class":       t.ontology_class,
                "table_type":           t.table_type,
                "business_domain":      t.business_domain,
                "description":          t.description,
                "row_count":            t.row_count,
                "sortkey1":             t.sortkey1,
                # Structural properties (derived, no LLM)
                "grain":                t.grain,
                "pk_columns":           t.pk_columns,
                "column_count":         t.column_count,
                "typical_join_role":    t.typical_join_role,
                "is_time_series":       t.is_time_series,
                "natural_dimensions":   t.natural_dimensions,
                "natural_measures":     t.natural_measures,
                "is_view":              t.is_view,
                "is_dimension_hub":     t.is_dimension_hub,
                "hub_join_col":         t.hub_join_col,
                "has_seasonality_pattern": t.has_seasonality_pattern,
                "typical_lookback_days":t.typical_lookback_days,
                # Pipeline internal
                "source_hash":          t.source_hash,
                "version":              t.version,
                "enrichment_status":    t.enrichment_status,
                "created_at":           now,
                "updated_at":           now,
            })

        n = self._batch_write("""
            UNWIND $rows AS r
            MERGE (t:Table {fqn: r.fqn})
            ON CREATE SET
                t.name                  = r.name,
                t.schema                = r.schema,
                t.ontology_class        = r.ontology_class,
                t.table_type            = r.table_type,
                t.business_domain       = r.business_domain,
                t.description           = r.description,
                t.row_count             = r.row_count,
                t.sortkey1              = r.sortkey1,
                t.grain                 = r.grain,
                t.pk_columns            = r.pk_columns,
                t.column_count          = r.column_count,
                t.typical_join_role     = r.typical_join_role,
                t.is_time_series        = r.is_time_series,
                t.natural_dimensions    = r.natural_dimensions,
                t.natural_measures      = r.natural_measures,
                t.is_view               = r.is_view,
                t.is_dimension_hub      = r.is_dimension_hub,
                t.hub_join_col          = r.hub_join_col,
                t.has_seasonality_pattern = r.has_seasonality_pattern,
                t.typical_lookback_days = r.typical_lookback_days,
                t.source_hash           = r.source_hash,
                t.version               = r.version,
                t.enrichment_status     = r.enrichment_status,
                t.created_at            = r.created_at,
                t.updated_at            = r.updated_at
            ON MATCH SET
                t.name                  = r.name,
                t.ontology_class        = CASE WHEN r.ontology_class <> '' THEN r.ontology_class ELSE t.ontology_class END,
                t.table_type            = CASE WHEN r.table_type <> '' THEN r.table_type ELSE t.table_type END,
                t.business_domain       = CASE WHEN r.business_domain <> '' THEN r.business_domain ELSE t.business_domain END,
                t.row_count             = r.row_count,
                t.sortkey1              = r.sortkey1,
                t.grain                 = CASE WHEN r.grain <> '' THEN r.grain ELSE t.grain END,
                t.pk_columns            = r.pk_columns,
                t.column_count          = r.column_count,
                t.typical_join_role     = r.typical_join_role,
                t.is_time_series        = r.is_time_series,
                t.natural_dimensions    = r.natural_dimensions,
                t.natural_measures      = r.natural_measures,
                t.is_view               = r.is_view,
                t.is_dimension_hub      = r.is_dimension_hub,
                t.hub_join_col          = r.hub_join_col,
                t.has_seasonality_pattern = r.has_seasonality_pattern,
                t.typical_lookback_days = r.typical_lookback_days,
                t.version               = CASE WHEN r.source_hash <> t.source_hash THEN t.version + 1 ELSE t.version END,
                t.enrichment_status     = CASE WHEN r.source_hash <> t.source_hash THEN 'stale' ELSE t.enrichment_status END,
                t.source_hash           = r.source_hash,
                t.updated_at            = r.updated_at
        """, rows)
        log.info("Loaded/updated %d Table nodes.", n)

    def link_tables_to_domains(self, tables: list[TableMeta]):
        now = _NOW()
        rows = [
            {"fqn": t.fqn, "domain": t.business_domain, "created_at": now}
            for t in tables if t.business_domain
        ]
        if not rows:
            return
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (t:Table {fqn: r.fqn})
            MATCH (d:Domain {name: r.domain})
            MERGE (t)-[rel:BELONGS_TO]->(d)
            ON CREATE SET rel.created_at = r.created_at
        """, rows)
        log.info("Linked %d tables to domains.", len(rows))

    # ── Column nodes ───────────────────────────────────────────────────────

    def load_columns(self, columns: list[ColumnMeta]):
        now = _NOW()
        rows = []
        for c in columns:
            rows.append({
                "id":                    c.id,
                "table_fqn":             c.table_fqn,
                "name":                  c.name,
                "data_type":             c.data_type,
                "ordinal_position":      c.ordinal_position,
                "is_nullable":           c.is_nullable,
                "is_pk":                 c.is_pk,
                # FK and surrogate key flags
                "is_foreign_key":        c.is_foreign_key,
                "is_surrogate_key":      c.is_surrogate_key,
                "is_surrogate_fk":       c.is_surrogate_fk,
                "referenced_table_fqn":  c.referenced_table_fqn,
                "referenced_column":     c.referenced_column,
                # Stats
                "null_frac":             c.null_frac,
                "n_distinct":            c.n_distinct,
                "sample_values":         c.sample_values[:10],
                "top_freq_values":       c.top_freq_values,
                "distinct_values":       c.distinct_values,
                # Derived structural properties
                "is_measurable":         c.is_measurable,
                "is_groupable":          c.is_groupable,
                "has_data":              c.has_data,
                "filter_selectivity":    c.filter_selectivity,
                "value_vocabulary":      c.value_vocabulary,
                # LLM-enriched (empty at load time)
                "description":           c.description,
                "semantic_type":         c.semantic_type,
                "synonyms":              c.synonyms,
                "is_pii":                c.is_pii,
                # Pipeline internal
                "source_hash":           c.source_hash,
                "version":               c.version,
                "enrichment_status":     c.enrichment_status,
                "created_at":            now,
                "updated_at":            now,
            })

        n = self._batch_write("""
            UNWIND $rows AS r
            MERGE (c:Column {id: r.id})
            ON CREATE SET
                c.table_fqn             = r.table_fqn,
                c.name                  = r.name,
                c.data_type             = r.data_type,
                c.ordinal_position      = r.ordinal_position,
                c.is_nullable           = r.is_nullable,
                c.is_pk                 = r.is_pk,
                c.is_foreign_key        = r.is_foreign_key,
                c.is_surrogate_key      = r.is_surrogate_key,
                c.is_surrogate_fk       = r.is_surrogate_fk,
                c.referenced_table_fqn  = r.referenced_table_fqn,
                c.referenced_column     = r.referenced_column,
                c.null_frac             = r.null_frac,
                c.n_distinct            = r.n_distinct,
                c.sample_values         = r.sample_values,
                c.top_freq_values       = r.top_freq_values,
                c.distinct_values       = r.distinct_values,
                c.is_measurable         = r.is_measurable,
                c.is_groupable          = r.is_groupable,
                c.has_data              = r.has_data,
                c.filter_selectivity    = r.filter_selectivity,
                c.value_vocabulary      = r.value_vocabulary,
                c.description           = r.description,
                c.semantic_type         = r.semantic_type,
                c.synonyms              = r.synonyms,
                c.is_pii                = r.is_pii,
                c.source_hash           = r.source_hash,
                c.version               = r.version,
                c.enrichment_status     = r.enrichment_status,
                c.created_at            = r.created_at,
                c.updated_at            = r.updated_at
            ON MATCH SET
                c.data_type             = r.data_type,
                c.is_nullable           = r.is_nullable,
                c.is_pk                 = r.is_pk,
                c.is_foreign_key        = r.is_foreign_key,
                c.is_surrogate_key      = r.is_surrogate_key,
                c.is_surrogate_fk       = r.is_surrogate_fk,
                c.referenced_table_fqn  = r.referenced_table_fqn,
                c.referenced_column     = r.referenced_column,
                c.null_frac             = r.null_frac,
                c.n_distinct            = r.n_distinct,
                c.sample_values         = CASE WHEN size(r.sample_values) > 0 THEN r.sample_values ELSE c.sample_values END,
                c.top_freq_values       = CASE WHEN size(r.top_freq_values) > 0 THEN r.top_freq_values ELSE c.top_freq_values END,
                c.distinct_values       = CASE WHEN size(r.distinct_values) > 0 THEN r.distinct_values ELSE c.distinct_values END,
                c.is_measurable         = r.is_measurable,
                c.is_groupable          = r.is_groupable,
                c.has_data              = r.has_data,
                c.filter_selectivity    = CASE WHEN r.filter_selectivity <> '' THEN r.filter_selectivity ELSE c.filter_selectivity END,
                c.value_vocabulary      = CASE WHEN size(r.value_vocabulary) > 0 THEN r.value_vocabulary ELSE c.value_vocabulary END,
                c.version               = CASE WHEN r.source_hash <> c.source_hash THEN c.version + 1 ELSE c.version END,
                c.enrichment_status     = CASE WHEN r.source_hash <> c.source_hash THEN 'stale' ELSE c.enrichment_status END,
                c.source_hash           = r.source_hash,
                c.updated_at            = r.updated_at
        """, rows)
        log.info("Loaded/updated %d Column nodes.", n)

    def link_columns_to_tables(self, columns: list[ColumnMeta]):
        now = _NOW()
        rows = [{"col_id": c.id, "tbl_fqn": c.table_fqn, "created_at": now} for c in columns]
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (t:Table {fqn: r.tbl_fqn})
            MATCH (c:Column {id: r.col_id})
            MERGE (t)-[rel:HAS_COLUMN]->(c)
            ON CREATE SET rel.created_at = r.created_at
        """, rows)
        log.info("Linked %d columns to tables.", len(rows))

    # ── JOINS_TO edges ─────────────────────────────────────────────────────

    def load_fk_edges(self, edges: list[FKEdge]):
        now = _NOW()
        rows = []
        for e in edges:
            # Declared FK edges: load ALL unconditionally (including UUID-target joins)
            # UUID filter applies only to inferred edges in fk_infer.py
            rows.append({
                "from_fqn":          e.from_table,
                "from_col":          e.from_col,
                "to_fqn":            e.to_table,
                "to_col":            e.to_col,
                "from_table":        e.from_table,
                "to_table":          e.to_table,
                "confidence":        e.confidence,
                "join_cost":         e.join_cost,
                "leiden_weight":     e.leiden_weight,
                "is_declared":       e.is_declared,
                "is_inferred":       e.is_inferred,
                "to_col_is_pk":      e.to_col_is_pk,
                "is_self_join":      e.is_self_join,
                "from_col_null_frac":e.from_col_null_frac,
                "join_likely_sparse":e.join_likely_sparse,
                "source":            e.source,
                "frequency":         e.frequency,
                "source_hash":       e.compute_source_hash(),
                "created_at":        now,
                "updated_at":        now,
            })

        n = self._batch_write("""
            UNWIND $rows AS r
            MATCH (a:Table {fqn: r.from_fqn})
            MATCH (b:Table {fqn: r.to_fqn})
            MERGE (a)-[j:JOINS_TO {from_col: r.from_col, to_col: r.to_col}]->(b)
            ON CREATE SET
                j.from_table         = r.from_table,
                j.to_table           = r.to_table,
                j.confidence         = r.confidence,
                j.join_cost          = r.join_cost,
                j.leiden_weight      = r.leiden_weight,
                j.is_declared        = r.is_declared,
                j.is_inferred        = r.is_inferred,
                j.to_col_is_pk       = r.to_col_is_pk,
                j.is_self_join       = r.is_self_join,
                j.from_col_null_frac = r.from_col_null_frac,
                j.join_likely_sparse = r.join_likely_sparse,
                j.source             = r.source,
                j.frequency          = r.frequency,
                j.source_hash        = r.source_hash,
                j.created_at         = r.created_at,
                j.updated_at         = r.updated_at
            ON MATCH SET
                j.from_table         = r.from_table,
                j.to_table           = r.to_table,
                j.confidence         = CASE WHEN r.confidence > j.confidence THEN r.confidence ELSE j.confidence END,
                j.join_cost          = CASE WHEN r.confidence > j.confidence THEN r.join_cost ELSE j.join_cost END,
                j.leiden_weight      = CASE WHEN r.confidence > j.confidence THEN r.leiden_weight ELSE j.leiden_weight END,
                j.frequency          = CASE WHEN r.source = 'query_history' THEN r.frequency ELSE j.frequency END,
                j.source_hash        = r.source_hash,
                j.updated_at         = r.updated_at
        """, rows)
        log.info("Loaded/updated %d JOINS_TO edges.", n)

    # ── Enrichment updates ─────────────────────────────────────────────────

    def update_table_enrichment(self, fqn: str, props: dict):
        now = _NOW()
        self._run("""
            MATCH (t:Table {fqn: $fqn})
            SET t += $props,
                t.enrichment_status = 'complete',
                t.cohere_embedding  = null,
                t.updated_at        = $now
        """, fqn=fqn, props=props, now=now)

    def update_column_enrichment(self, col_id: str, props: dict):
        now = _NOW()
        self._run("""
            MATCH (c:Column {id: $id})
            SET c += $props,
                c.enrichment_status = 'complete',
                c.cohere_embedding  = null,
                c.updated_at        = $now
        """, id=col_id, props=props, now=now)

    def batch_update_column_enrichment(self, rows: list[dict], now: str):
        """rows: list of dicts with col_id + all enrichment props (no None values)."""
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (c:Column {id: r.col_id})
            SET c.description         = r.description,
                c.semantic_type       = r.semantic_type,
                c.synonyms            = r.synonyms,
                c.is_pii              = r.is_pii,
                c.pii_type            = r.pii_type,
                c.temporal_grain      = r.temporal_grain,
                c.default_aggregation = r.default_aggregation,
                c.value_aliases       = r.value_aliases,
                c.enrichment_status   = 'complete',
                c.cohere_embedding    = null,
                c.updated_at          = $now
        """, rows, now=now)

    def batch_update_table_enrichment(self, rows: list[dict], now: str):
        """rows: list of dicts with fqn, description, synonyms, grain, business_domain,
        table_type_override, description_model."""
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (t:Table {fqn: r.fqn})
            SET t.description        = r.description,
                t.synonyms           = r.synonyms,
                t.grain              = r.grain,
                t.business_domain    = CASE WHEN r.business_domain IS NOT NULL AND r.business_domain <> ''
                                           THEN r.business_domain ELSE t.business_domain END,
                t.table_type         = CASE WHEN r.table_type_override IS NOT NULL AND r.table_type_override <> ''
                                           THEN r.table_type_override ELSE t.table_type END,
                t.enrichment_status  = 'complete',
                t.cohere_embedding   = null,
                t.updated_at         = $now
        """, rows, now=now)

    def update_domain_description(self, domain_name: str, description: str):
        now = _NOW()
        self._run("""
            MATCH (d:Domain {name: $name})
            SET d.description        = $description,
                d.enrichment_status  = 'complete',
                d.cohere_embedding   = null,
                d.updated_at         = $now
        """, name=domain_name, description=description, now=now)

    def update_table_embedding(self, fqn: str, embedding: list[float], model: str):
        now = _NOW()
        self._run("""
            MATCH (t:Table {fqn: $fqn})
            SET t.cohere_embedding       = $emb,
                t.embedding_model        = $model,
                t.embedding_generated_at = $now,
                t.updated_at             = $now
        """, fqn=fqn, emb=embedding, model=model, now=now)

    def update_column_embedding(self, col_id: str, embedding: list[float], model: str):
        now = _NOW()
        self._run("""
            MATCH (c:Column {id: $id})
            SET c.cohere_embedding       = $emb,
                c.embedding_model        = $model,
                c.embedding_generated_at = $now,
                c.updated_at             = $now
        """, id=col_id, emb=embedding, model=model, now=now)

    # ── GDS score updates ─────────────────────────────────────────────────

    def update_gds_scores(self, scores: list[dict]):
        """Each dict: {fqn, pagerank_score, betweenness_score, community_id, wcc_component_id}"""
        now = _NOW()
        rows = [{**s, "now": now} for s in scores]
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (t:Table {fqn: r.fqn})
            SET t.wcc_component_id     = coalesce(r.wcc_component_id, t.wcc_component_id),
                t.community_id         = coalesce(r.community_id, t.community_id),
                t.pagerank_score       = coalesce(r.pagerank_score, t.pagerank_score),
                t.betweenness_score    = coalesce(r.betweenness_score, t.betweenness_score),
                t.pagerank_computed_at = CASE WHEN r.pagerank_score IS NOT NULL THEN r.now ELSE t.pagerank_computed_at END,
                t.leiden_computed_at   = CASE WHEN r.community_id IS NOT NULL THEN r.now ELSE t.leiden_computed_at END,
                t.updated_at           = r.now
        """, rows)

    def create_community_nodes(self, communities: list[dict]):
        """communities: [{id, dominant_domain, table_count}]"""
        now = _NOW()
        rows = [{**c, "created_at": now} for c in communities]
        self._batch_write("""
            UNWIND $rows AS r
            MERGE (c:Community {id: r.id})
            SET c.dominant_domain = r.dominant_domain,
                c.table_count     = r.table_count,
                c.created_at      = r.created_at
        """, rows)
        # Link tables to communities
        self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
            MATCH (c:Community {id: t.community_id})
            MERGE (c)-[:CONTAINS_TABLE]->(t)
        """)

    # ── Integrity checks ──────────────────────────────────────────────────

    def integrity_check(self) -> dict:
        table_count = self._run("MATCH (t:Table) RETURN count(t) AS n")[0]["n"]
        col_count   = self._run("MATCH (c:Column) RETURN count(c) AS n")[0]["n"]
        edge_count  = self._run("MATCH ()-[r:JOINS_TO]->() RETURN count(r) AS n")[0]["n"]
        broken_fks  = self._run("""
            MATCH ()-[r:JOINS_TO]->()
            WHERE r.to_fqn IS NOT NULL
              AND NOT EXISTS { MATCH (:Table {fqn: r.to_fqn}) }
            RETURN count(r) AS n
        """)[0]["n"]
        missing_emb_cols = self._run("""
            MATCH (c:Column) WHERE c.cohere_embedding IS NULL RETURN count(c) AS n
        """)[0]["n"]
        missing_emb_tbls = self._run("""
            MATCH (t:Table) WHERE t.cohere_embedding IS NULL RETURN count(t) AS n
        """)[0]["n"]
        stale = self._run("""
            MATCH (n) WHERE n.enrichment_status IN ['stale','failed']
            RETURN count(n) AS n
        """)[0]["n"]
        return {
            "table_count": table_count,
            "column_count": col_count,
            "edge_count": edge_count,
            "broken_fk_targets": broken_fks,
            "missing_col_embeddings": missing_emb_cols,
            "missing_tbl_embeddings": missing_emb_tbls,
            "stale_or_failed_nodes": stale,
        }

    # ── New Cypher passes ─────────────────────────────────────────────────

    def _pass_domain_voting(self):
        """Re-run PageRank-weighted domain voting after ENRICH sets business_domain on tables.
        Excludes tables with empty/null business_domain so they don't corrupt the vote."""
        self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
              AND t.business_domain IS NOT NULL AND t.business_domain <> ''
            WITH t.community_id AS cid,
                 t.business_domain AS dom,
                 sum(coalesce(t.pagerank_score, 0.001)) AS w
            ORDER BY w DESC
            WITH cid, collect({domain: dom, weight: w}) AS ranked
            WITH cid, ranked,
                 reduce(s = 0.0, r IN ranked | s + r.weight) AS total_w
            MATCH (c:Community {id: cid})
            SET c.dominant_domain            = ranked[0].domain,
                c.dominant_domain_confidence = CASE WHEN total_w = 0 THEN 0.0
                    ELSE round(ranked[0].weight / total_w * 100) / 100.0 END
        """)
        log.debug("Pass: community dominant_domain re-voted from post-enrich table domains.")

    def _pass_community_table_count(self):
        """Set table_count on Community nodes from CONTAINS_TABLE edges."""
        self._run("""
            MATCH (c:Community)-[:CONTAINS_TABLE]->(t:Table)
            WITH c, count(t) AS n
            SET c.table_count = n
        """)
        log.debug("Pass: community table_count set.")

    def _pass_shared_synonyms(self):
        """Set shared_synonyms on SEMANTICALLY_SIMILAR edges (post-enrichment)."""
        self._run("""
            MATCH (c1:Column)-[r:SEMANTICALLY_SIMILAR]->(c2:Column)
            WHERE c1.synonyms IS NOT NULL AND c2.synonyms IS NOT NULL
              AND size(c1.synonyms) > 0 AND size(c2.synonyms) > 0
            SET r.shared_synonyms = [s IN c1.synonyms WHERE s IN c2.synonyms]
        """)
        log.debug("Pass: shared_synonyms on SEMANTICALLY_SIMILAR set.")

    # ── New passes (9 additional) ─────────────────────────────────────────

    def _pass_surrogate_keys(self):
        """Flag uuid-named PK columns as surrogate keys with no join semantics."""
        self._run("""
            MATCH (c:Column)
            WHERE (c.name = 'uuid' OR c.name ENDS WITH '_uuid') AND c.is_pk = true
            SET c.is_surrogate_key = true
        """)
        log.debug("Pass: is_surrogate_key set on uuid PK columns.")

    def _pass_has_data(self):
        """Flag columns with usable data (not mostly null, has stats)."""
        self._run("""
            MATCH (c:Column)
            SET c.has_data = (c.null_frac < 0.95 AND c.n_distinct <> 0)
        """)
        log.debug("Pass: has_data set.")

    def _pass_dimension_hubs(self):
        """Mark high-in_degree dimension tables as cross-domain hubs.
        Computes hub_join_col = most common to_col in incoming JOINS_TO edges."""
        self._run("""
            MATCH (t:Table)
            WHERE t.in_degree >= 5
              AND t.table_type IN ['dimension', 'reference']
              AND NOT t.is_time_series
            SET t.is_dimension_hub = true
        """)
        # Compute hub_join_col per hub table
        self._run("""
            MATCH ()-[r:JOINS_TO]->(t:Table {is_dimension_hub: true})
            WITH t, r.to_col AS col, count(*) AS freq
            ORDER BY freq DESC
            WITH t, collect(col)[0] AS top_col
            SET t.hub_join_col = top_col
        """)
        log.debug("Pass: is_dimension_hub and hub_join_col set.")

    def _pass_seasonality_pattern(self):
        """Mark tables with multi-year time-series data and trend-related intents."""
        self._run("""
            MATCH (t:Table)
            WHERE t.is_time_series = true AND t.row_count > 365
            WITH t
            MATCH (t)-[:RELEVANT_TO]->(i:Intent)
            WHERE i.name IN ['trend_analysis', 'scenario_forecast', 'trend_and_forecast']
            SET t.has_seasonality_pattern = true,
                t.typical_lookback_days = CASE
                    WHEN t.row_count > 1000000 THEN 730
                    WHEN t.row_count > 100000  THEN 365
                    ELSE 180
                END
        """)
        log.debug("Pass: has_seasonality_pattern and typical_lookback_days set.")

    def _pass_fk_join_quality(self):
        """Copy FK source column's null_frac to JOINS_TO edge as data quality signal."""
        self._run("""
            MATCH (t1:Table)-[r:JOINS_TO]->(t2:Table)
            MATCH (t1)-[:HAS_COLUMN]->(c:Column {name: r.from_col})
            SET r.from_col_null_frac = coalesce(c.null_frac, 0.0),
                r.join_likely_sparse  = (coalesce(c.null_frac, 0.0) > 0.5)
        """)
        log.debug("Pass: from_col_null_frac / join_likely_sparse on JOINS_TO set.")

    def _pass_bridges_to_enhanced(self):
        """Create/update BRIDGES_TO edges between communities that share a hub table.
        When multiple hubs qualify, pick the one with the highest in_degree (most connected)
        so that company (in_degree=33) always beats peripheral hubs like pension_plan.
        """
        self._run("""
            MATCH (t1:Table)-[:JOINS_TO*1..2 {is_declared: true}]->(hub:Table {is_dimension_hub: true})
            MATCH (t2:Table)-[:JOINS_TO*1..2 {is_declared: true}]->(hub)
            WHERE t1.community_id <> t2.community_id
              AND t1.community_id IS NOT NULL AND t2.community_id IS NOT NULL
            WITH t1.community_id AS c1, t2.community_id AS c2,
                 hub.fqn AS hub_fqn, coalesce(hub.hub_join_col, 'code') AS hub_col,
                 hub.community_id AS hub_cid,
                 coalesce(hub.in_degree, 0) AS hub_indegree
            ORDER BY hub_indegree DESC
            WITH c1, c2,
                 head(collect(hub_fqn))  AS hub_fqn,
                 head(collect(hub_col))  AS hub_col,
                 head(collect(hub_cid))  AS hub_cid
            MATCH (comm1:Community {id: c1}), (comm2:Community {id: c2})
            MERGE (comm1)-[r:BRIDGES_TO]->(comm2)
            SET r.bridge_type              = 'hub_table',
                r.hub_table_fqn            = hub_fqn,
                r.hub_join_col             = hub_col,
                r.hub_community_id         = hub_cid,
                r.shared_dimension_columns = [hub_col],
                r.join_safe                = true
        """)
        log.debug("Pass: BRIDGES_TO enhanced with hub_table info.")

    def _pass_relevant_to_match_columns(self):
        """Populate match_columns on RELEVANT_TO edges with the table's top measurable/groupable columns.
        Only fills edges where match_columns is currently empty (ontology_class matched edges)."""
        self._run("""
            MATCH (t:Table)-[r:RELEVANT_TO]->(i:Intent)
            WHERE size(coalesce(r.match_columns, [])) = 0
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
            WHERE c.is_measurable = true OR c.is_groupable = true
              OR c.semantic_type IN ['amount', 'measure', 'percentage', 'date']
            WITH t, r, collect(DISTINCT c.name)[0..5] AS relevant_cols
            SET r.match_columns = relevant_cols
        """)
        log.debug("Pass: match_columns populated on RELEVANT_TO edges.")

    def _pass_validate_query_templates(self):
        """Validate anchor FQNs exist; compute template_confidence; set anchor_table_fqns_resolved."""
        # Step 1: resolve verified FQNs
        self._run("""
            MATCH (qt:QueryTemplate)
            WHERE qt.anchor_table_fqns IS NOT NULL AND size(qt.anchor_table_fqns) > 0
            WITH qt,
                 [fqn IN qt.anchor_table_fqns
                  WHERE EXISTS { MATCH (:Table {fqn: fqn}) }] AS resolved
            SET qt.anchor_fqns_verified       = (size(resolved) = size(qt.anchor_table_fqns)),
                qt.anchor_table_fqns_resolved  = resolved
        """)
        # Step 2: compute confidence score
        self._run("""
            MATCH (qt:QueryTemplate)
            SET qt.template_confidence =
              CASE WHEN coalesce(qt.anchor_fqns_verified, false) THEN 0.4 ELSE 0.0 END +
              CASE WHEN size(coalesce(qt.anchor_table_fqns_resolved, [])) > 0 THEN 0.3 ELSE 0.0 END +
              0.2 +
              CASE WHEN (qt.complexity = 'simple'   AND size(coalesce(qt.cte_steps, [])) <= 2)
                        OR (qt.complexity = 'complex'  AND size(coalesce(qt.cte_steps, [])) > 2)
                        OR (qt.complexity = 'advanced' AND size(coalesce(qt.cte_steps, [])) >= 5)
                   THEN 0.1 ELSE 0.0 END,
            qt.source      = coalesce(qt.source, 'questions_txt'),
            qt.is_validated = coalesce(qt.is_validated, false)
        """)
        log.debug("Pass: QueryTemplate confidence validated and resolved FQNs set.")

    # ── Post-enrich Cypher passes ─────────────────────────────────────────

    def run_post_enrich_passes(self):
        """
        Idempotent Cypher passes that derive properties from enrichment output.
        Call once after enrich + embed steps complete.
        """
        log.info("Running post-enrich Cypher passes …")
        # Column data quality passes
        self._pass_is_groupable_measurable()
        self._pass_filter_selectivity()
        self._pass_has_data()
        self._pass_surrogate_keys()
        self._pass_value_vocabulary()
        # Column same-name count (used by ambiguity pass)
        self._pass_same_name_col_count()
        # Table structural passes
        self._pass_time_dimension()
        self._pass_is_time_series_natural_dims()
        self._pass_typical_join_role()
        self._pass_dimension_hubs()
        self._pass_seasonality_pattern()
        # Edge quality passes
        self._pass_fk_join_quality()
        self._pass_nullable_fk()
        self._pass_is_canonical()
        self._pass_ambiguity_risk()
        # Community + cross-domain passes
        self._pass_domain_voting()
        self._pass_community_table_count()
        self._pass_bridges_to_enhanced()
        self._pass_shared_synonyms()
        # Template validation
        self._pass_validate_query_templates()
        log.info("Post-enrich Cypher passes complete.")

    # _pass_top_values_text removed — fulltext indexes now use array properties directly (Neo4j 5.x)

    def _pass_is_groupable_measurable(self):
        """B1 — is_groupable / is_measurable derived from semantic_type and data_type."""
        self._run("""
            MATCH (c:Column)
            SET c.is_groupable  = c.semantic_type IN
                    ['dimension','code','flag','date','identifier'],
                c.is_measurable = (
                    c.semantic_type IN ['measure','amount','ratio','percentage']
                    OR (
                        c.data_type IN [
                            'integer','bigint','smallint','int','int2','int4','int8',
                            'numeric','decimal','float','float4','float8',
                            'real','double precision'
                        ]
                        AND NOT c.semantic_type IN
                            ['identifier','flag','code','date']
                    )
                )
        """)
        log.debug("Pass: is_groupable / is_measurable set.")

    def _pass_filter_selectivity(self):
        """B3 — filter_selectivity from n_distinct."""
        self._run("""
            MATCH (c:Column) WHERE c.n_distinct IS NOT NULL
            SET c.filter_selectivity = CASE
                WHEN c.n_distinct = -1.0 OR c.n_distinct > 1000 THEN 'high'
                WHEN c.n_distinct > 50                           THEN 'medium'
                ELSE                                                  'low'
            END
        """)
        log.debug("Pass: filter_selectivity set.")

    def _pass_value_vocabulary(self):
        """B4 — value_vocabulary: prefer distinct_values; fall back to top_freq_values stripped.
        Applies to all low-cardinality columns (n_distinct <= 100), not just LLM-enriched ones."""
        # Use distinct_values when available (exact values, no counts)
        self._run("""
            MATCH (c:Column)
            WHERE c.distinct_values IS NOT NULL AND size(c.distinct_values) > 0
            SET c.value_vocabulary = c.distinct_values[0..30]
        """)
        # Fall back to top_freq_values (strip "val:count" format) for any remaining columns
        self._run("""
            MATCH (c:Column)
            WHERE (c.value_vocabulary IS NULL OR size(c.value_vocabulary) = 0)
              AND c.top_freq_values IS NOT NULL AND size(c.top_freq_values) > 0
            SET c.value_vocabulary = [v IN c.top_freq_values[0..30] | split(v, ':')[0]]
        """)
        log.debug("Pass: value_vocabulary set.")

    def _pass_same_name_col_count(self):
        """B6 — same_name_col_count: how many columns share this name across all tables."""
        self._run("""
            MATCH (c:Column)
            WITH c.name AS n, count(*) AS cnt
            MATCH (c2:Column {name: n})
            SET c2.same_name_col_count = cnt
        """)
        log.debug("Pass: same_name_col_count set.")

    def _pass_time_dimension(self):
        """C2 — time_dimension_col / time_dimension_grain: best date column per table."""
        self._run("""
            MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
            WHERE c.semantic_type = 'date'
            WITH t, c
            ORDER BY
                CASE WHEN t.sortkey1 = c.name THEN 0 ELSE 1 END,
                CASE WHEN c.name CONTAINS 'transaction' OR c.name CONTAINS 'value_date' THEN 1
                     WHEN c.name CONTAINS 'created'                                     THEN 2
                     ELSE                                                                     3 END,
                c.ordinal_position
            WITH t, collect(c)[0] AS best
            WHERE best IS NOT NULL
            SET t.time_dimension_col   = best.name,
                t.time_dimension_grain = coalesce(best.temporal_grain, 'day')
        """)
        log.debug("Pass: time_dimension_col / grain set.")

    def _pass_is_time_series_natural_dims(self):
        """C3 — is_time_series, natural_dimensions, natural_measures."""
        self._run("""
            MATCH (t:Table)
            SET t.is_time_series = (t.time_dimension_col IS NOT NULL AND t.table_type = 'fact')
        """)
        self._run("""
            MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
            WHERE c.is_groupable = true
            WITH t, collect(c.name)[0..10] AS dims
            SET t.natural_dimensions = dims
        """)
        self._run("""
            MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
            WHERE c.is_measurable = true
            WITH t, collect(c.name)[0..10] AS measures
            SET t.natural_measures = measures
        """)
        log.debug("Pass: is_time_series / natural_dimensions / natural_measures set.")

    def _pass_typical_join_role(self):
        """C4 — typical_join_role from table_type and betweenness."""
        self._run("""
            MATCH (t:Table)
            SET t.typical_join_role = CASE
                WHEN t.table_type = 'fact' AND coalesce(t.betweenness_score, 0) > 0.1 THEN 'anchor'
                WHEN t.table_type = 'fact'                                              THEN 'fact'
                WHEN t.table_type IN ['reference','dimension']                          THEN 'dimension'
                WHEN t.table_type = 'bridge'                                            THEN 'bridge'
                WHEN coalesce(t.row_count, 0) < 10000                                  THEN 'lookup'
                ELSE                                                                         'dimension'
            END
        """)
        log.debug("Pass: typical_join_role set.")

    def _pass_nullable_fk(self):
        """D3 — has_nullable_fk / recommended_join_type on JOINS_TO edges."""
        self._run("""
            MATCH (a:Table)-[j:JOINS_TO]->(b:Table)
            OPTIONAL MATCH (ca:Column) WHERE ca.id = a.fqn + '.' + j.from_col
            OPTIONAL MATCH (cb:Column) WHERE cb.id = b.fqn + '.' + j.to_col
            SET j.max_null_frac         = CASE
                    WHEN coalesce(ca.null_frac, 0) > coalesce(cb.null_frac, 0)
                    THEN coalesce(ca.null_frac, 0)
                    ELSE coalesce(cb.null_frac, 0) END,
                j.has_nullable_fk       = (coalesce(ca.null_frac, 0) > 0.01
                                           OR coalesce(cb.null_frac, 0) > 0.01),
                j.recommended_join_type = CASE
                    WHEN coalesce(ca.null_frac, 0) > 0.05 OR coalesce(cb.null_frac, 0) > 0.05
                    THEN 'LEFT JOIN'
                    ELSE 'INNER JOIN'
                END
        """)
        log.debug("Pass: nullable FK / recommended_join_type set.")

    def _pass_is_canonical(self):
        """E1 — is_canonical: one canonical JOINS_TO per table pair."""
        self._run("""
            MATCH (a:Table)-[j:JOINS_TO]->(b:Table)
            WITH a, b, j
            ORDER BY
                CASE WHEN j.is_ontology = true  THEN 0
                     WHEN j.is_declared = true   THEN 1
                     ELSE                             2 END,
                j.confidence DESC
            WITH a, b, collect(j) AS edges
            FOREACH (e IN edges[0..1] | SET e.is_canonical = true)
            FOREACH (e IN edges[1..] | SET e.is_canonical = false)
        """)
        log.debug("Pass: is_canonical set.")

    def _pass_ambiguity_risk(self):
        """E2 — ambiguity_risk from same_name_col_count on from_col."""
        self._run("""
            MATCH (a:Table)-[j:JOINS_TO]->(b:Table)
            OPTIONAL MATCH (c:Column) WHERE c.id = a.fqn + '.' + j.from_col
            SET j.ambiguity_risk = CASE
                WHEN coalesce(c.same_name_col_count, 1) <= 2  THEN 'none'
                WHEN coalesce(c.same_name_col_count, 1) <= 5  THEN 'low'
                WHEN coalesce(c.same_name_col_count, 1) <= 15 THEN 'medium'
                ELSE                                                'high'
            END
        """)
        log.debug("Pass: ambiguity_risk set.")

    # ── Intent / community / glossary / template loaders ─────────────────

    def load_intent_nodes(self, intents: list[dict]):
        """intents: list of {name, description}"""
        now = _NOW()
        rows = [{**i, "created_at": now} for i in intents]
        self._batch_write("""
            UNWIND $rows AS r
            MERGE (i:Intent {name: r.name})
            SET i.description      = r.description,
                i.cohere_embedding = null,
                i.created_at       = r.created_at
        """, rows)
        log.info("Loaded %d Intent nodes.", len(rows))

    def load_relevant_to_edges(self, edges: list[dict]):
        """edges: list of {table_fqn, intent_name, confidence, relevance_score, match_type, match_columns}"""
        now = _NOW()
        rows = [{**e, "created_at": now} for e in edges]
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (t:Table {fqn: r.table_fqn})
            MATCH (i:Intent {name: r.intent_name})
            MERGE (t)-[rel:RELEVANT_TO]->(i)
            SET rel.confidence      = r.confidence,
                rel.relevance_score = coalesce(r.relevance_score, r.confidence),
                rel.match_type      = coalesce(r.match_type, 'ontology_class'),
                rel.match_columns   = coalesce(r.match_columns, []),
                rel.created_at      = r.created_at
        """, rows)
        log.info("Loaded %d RELEVANT_TO edges.", len(rows))

    def set_intent_tags_on_tables(self):
        """Derive intent_tags array on each Table from RELEVANT_TO edges."""
        self._run("""
            MATCH (t:Table)-[:RELEVANT_TO]->(i:Intent)
            WITH t, collect(i.name) AS tags
            SET t.intent_tags = tags
        """)
        log.info("intent_tags set on Tables.")

    def load_business_terms(self, terms: list[dict]):
        """terms: list of {term, variants, term_type, term_category, description, related_table_fqns}"""
        now = _NOW()
        rows = [{**t, "created_at": now} for t in terms]
        self._batch_write("""
            UNWIND $rows AS r
            MERGE (b:BusinessTerm {term: r.term})
            SET b.variants           = r.variants,
                b.term_type          = r.term_type,
                b.term_category      = coalesce(r.term_category, "concept"),
                b.description        = r.description,
                b.related_table_fqns = coalesce(r.related_table_fqns, []),
                b.enrichment_status  = 'complete',
                b.created_at         = r.created_at
        """, rows)
        log.info("Loaded %d BusinessTerm nodes.", len(rows))

    def load_business_term_table_edges(self, terms: list[dict]):
        """Create REFERENCES_TABLE edges from BusinessTerm to Table.
        terms: list of {term, related_table_fqns: [fqn, ...], source_column_name}
        Replaces the synthetic CONTEXT_RELEVANT edges built at query time.
        """
        now = _NOW()
        rows = []
        for t in terms:
            for fqn in t.get("related_table_fqns") or []:
                rows.append({
                    "term": t["term"],
                    "fqn": fqn,
                    "source_column_name": t.get("source_column_name", ""),
                    "created_at": now,
                })
        if not rows:
            return
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (b:BusinessTerm {term: r.term})
            MATCH (t:Table {fqn: r.fqn})
            MERGE (b)-[rel:REFERENCES_TABLE]->(t)
            SET rel.source_column_name = r.source_column_name,
                rel.created_at         = r.created_at
        """, rows)
        log.info("Loaded %d REFERENCES_TABLE edges.", len(rows))

    def update_businessterm_embedding(self, term: str, embedding: list[float], model: str):
        now = _NOW()
        self._run("""
            MATCH (b:BusinessTerm {term: $term})
            SET b.cohere_embedding       = $emb,
                b.embedding_model        = $model,
                b.embedding_generated_at = $now
        """, term=term, emb=embedding, model=model, now=now)

    def load_query_templates(self, templates: list[dict]):
        """templates: list of QueryTemplate property dicts from enrich_query_templates()"""
        import json as _json
        now = _NOW()
        rows = [
            {**t,
             "intent_scores": _json.dumps(t.get("intent_scores") or {}),
             "created_at": now}
            for t in templates
        ]
        self._batch_write("""
            UNWIND $rows AS r
            MERGE (q:QueryTemplate {id: r.id})
            SET q.question_text           = r.question_text,
                q.description             = r.description,
                q.primary_intent          = r.primary_intent,
                q.intent_scores           = r.intent_scores,
                q.complexity              = r.complexity,
                q.anchor_table_fqns       = r.anchor_table_fqns,
                q.cte_steps               = r.cte_steps,
                q.required_aggregations   = r.required_aggregations,
                q.required_filters        = r.required_filters,
                q.time_windowed           = r.time_windowed,
                q.sql_pattern             = r.sql_pattern,
                q.is_cross_domain         = r.is_cross_domain,
                q.min_cte_count           = r.min_cte_count,
                q.max_cte_count           = r.max_cte_count,
                q.source_line             = r.source_line,
                q.enrichment_status       = 'complete',
                q.created_at              = r.created_at
        """, rows)
        log.info("Loaded %d QueryTemplate nodes.", len(rows))

    def link_query_templates_to_intents(self, template_intent_edges: list[dict]):
        """edges: list of {qt_id, intent_name, confidence}"""
        now = _NOW()
        rows = [{**e, "created_at": now} for e in template_intent_edges]
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (q:QueryTemplate {id: r.qt_id})
            MATCH (i:Intent {name: r.intent_name})
            MERGE (q)-[rel:CLASSIFIED_AS]->(i)
            SET rel.confidence = r.confidence,
                rel.created_at = r.created_at
        """, rows)
        log.info("Linked %d QueryTemplate → Intent edges.", len(rows))

    def link_query_templates_to_tables(self, template_table_edges: list[dict]):
        """edges: list of {qt_id, table_fqn}"""
        now = _NOW()
        rows = [{**e, "created_at": now} for e in template_table_edges]
        self._batch_write("""
            UNWIND $rows AS r
            MATCH (q:QueryTemplate {id: r.qt_id})
            MATCH (t:Table {fqn: r.table_fqn})
            MERGE (q)-[:REQUIRES_TABLE]->(t)
        """, rows)
        log.info("Linked %d QueryTemplate → Table edges.", len(rows))

    def update_querytemplate_embedding(self, qt_id: str, embedding: list[float], model: str):
        now = _NOW()
        self._run("""
            MATCH (q:QueryTemplate {id: $id})
            SET q.cohere_embedding       = $emb,
                q.embedding_model        = $model,
                q.embedding_generated_at = $now
        """, id=qt_id, emb=embedding, model=model, now=now)

    def load_community_descriptions(self, enriched: dict):
        """enriched: {str(community_id): {description}}"""
        now = _NOW()
        for cid_str, data in enriched.items():
            try:
                cid = int(cid_str)
            except ValueError:
                continue
            self._run("""
                MATCH (c:Community {id: $cid})
                SET c.description        = $desc,
                    c.enrichment_status  = 'complete',
                    c.cohere_embedding   = null,
                    c.updated_at         = $now
            """, cid=cid, desc=data.get("description", ""), now=now)
        log.info("Updated descriptions for %d Community nodes.", len(enriched))

    def run_is_subquery_anchor(self):
        """F2 — is_subquery_anchor for high-betweenness fact tables."""
        self._run("""
            MATCH (t:Table) WHERE t.betweenness_score IS NOT NULL
            WITH percentileCont(t.betweenness_score, 0.75) AS p75
            MATCH (t2:Table)
            SET t2.is_subquery_anchor = (
                t2.betweenness_score > p75
                AND t2.table_type = 'fact'
                AND coalesce(t2.is_rollup, false) = false
            )
        """)
        log.info("is_subquery_anchor set.")

    # ── Property defaults passes (idempotent, coalesce-safe) ──────────────

    def initialize_column_defaults(self):
        """Ensure every Column node has all expected property keys, even if enrichment skipped."""
        self._run("""
            MATCH (c:Column)
            SET c.description           = coalesce(c.description, ""),
                c.semantic_type         = coalesce(c.semantic_type, ""),
                c.synonyms              = coalesce(c.synonyms, []),
                c.is_pii                = coalesce(c.is_pii, false),
                c.pii_type              = coalesce(c.pii_type, ""),
                c.temporal_grain        = coalesce(c.temporal_grain, "none"),
                c.default_aggregation   = coalesce(c.default_aggregation, "NONE"),
                c.value_aliases         = coalesce(c.value_aliases, []),
                c.value_vocabulary      = coalesce(c.value_vocabulary, []),
                c.distinct_values       = coalesce(c.distinct_values, []),
                c.is_foreign_key        = coalesce(c.is_foreign_key, false),
                c.is_surrogate_key      = coalesce(c.is_surrogate_key, false),
                c.is_surrogate_fk       = coalesce(c.is_surrogate_fk, false),
                c.referenced_table_fqn  = coalesce(c.referenced_table_fqn, ""),
                c.referenced_column     = coalesce(c.referenced_column, ""),
                c.filter_selectivity    = coalesce(c.filter_selectivity, ""),
                c.has_data              = coalesce(c.has_data, true),
                c.is_groupable          = coalesce(c.is_groupable, false),
                c.is_measurable         = coalesce(c.is_measurable, false),
                c.enrichment_status     = coalesce(c.enrichment_status, "pending")
        """)
        log.debug("Column property defaults initialized.")

    def initialize_table_defaults(self):
        """Ensure every Table node has all expected property keys."""
        self._run("""
            MATCH (t:Table)
            SET t.description           = coalesce(t.description, ""),
                t.synonyms              = coalesce(t.synonyms, []),
                t.grain                 = coalesce(t.grain, ""),
                t.pk_columns            = coalesce(t.pk_columns, []),
                t.column_count          = coalesce(t.column_count, 0),
                t.business_domain       = coalesce(t.business_domain, ""),
                t.typical_join_role     = coalesce(t.typical_join_role, "unknown"),
                t.natural_dimensions    = coalesce(t.natural_dimensions, []),
                t.natural_measures      = coalesce(t.natural_measures, []),
                t.intent_tags           = coalesce(t.intent_tags, []),
                t.is_time_series        = coalesce(t.is_time_series, false),
                t.is_view               = coalesce(t.is_view, false),
                t.is_dimension_hub      = coalesce(t.is_dimension_hub, false),
                t.hub_join_col          = coalesce(t.hub_join_col, ""),
                t.has_seasonality_pattern = coalesce(t.has_seasonality_pattern, false),
                t.typical_lookback_days = coalesce(t.typical_lookback_days, 0),
                t.is_rollup             = coalesce(t.is_rollup, false),
                t.is_subquery_anchor    = coalesce(t.is_subquery_anchor, false),
                t.triangle_count        = coalesce(t.triangle_count, 0),
                t.scc_id                = coalesce(t.scc_id, ""),
                t.in_degree             = coalesce(t.in_degree, 0),
                t.out_degree            = coalesce(t.out_degree, 0),
                t.enrichment_status     = coalesce(t.enrichment_status, "pending")
        """)
        log.debug("Table property defaults initialized.")

    def initialize_intent_defaults(self):
        """Ensure every Intent node has all expected property keys."""
        self._run("""
            MATCH (i:Intent)
            SET i.description       = coalesce(i.description, ""),
                i.enrichment_status = coalesce(i.enrichment_status, "pending"),
                i.created_at        = coalesce(i.created_at, "")
        """)
        log.debug("Intent property defaults initialized.")

    def initialize_community_defaults(self):
        """Ensure every Community node has all expected property keys."""
        self._run("""
            MATCH (c:Community)
            SET c.dominant_domain            = coalesce(c.dominant_domain, "unknown"),
                c.dominant_domain_confidence = coalesce(c.dominant_domain_confidence, 0.0),
                c.table_count                = coalesce(c.table_count, 0),
                c.description                = coalesce(c.description, ""),
                c.enrichment_status          = coalesce(c.enrichment_status, "pending"),
                c.created_at                 = coalesce(c.created_at, "")
        """)
        log.debug("Community property defaults initialized.")

    def initialize_domain_defaults(self):
        """Ensure every Domain node has all expected property keys."""
        # Compute table_count from actual BELONGS_TO edges
        self._run("""
            MATCH (d:Domain)
            OPTIONAL MATCH (t:Table)-[:BELONGS_TO]->(d)
            WITH d, count(t) AS tc
            SET d.table_count       = tc,
                d.description       = coalesce(d.description, ""),
                d.enrichment_status = coalesce(d.enrichment_status, "pending"),
                d.created_at        = coalesce(d.created_at, ""),
                d.updated_at        = coalesce(d.updated_at, "")
        """)
        log.debug("Domain property defaults initialized.")

    def initialize_businessterm_defaults(self):
        """Ensure every BusinessTerm node has all expected property keys."""
        self._run("""
            MATCH (b:BusinessTerm)
            SET b.variants          = coalesce(b.variants, []),
                b.term_type         = coalesce(b.term_type, ""),
                b.term_category     = coalesce(b.term_category, "concept"),
                b.description       = coalesce(b.description, ""),
                b.enrichment_status = coalesce(b.enrichment_status, "pending"),
                b.created_at        = coalesce(b.created_at, "")
        """)
        log.debug("BusinessTerm property defaults initialized.")

    def initialize_querytemplate_defaults(self):
        """Ensure every QueryTemplate node has all expected property keys."""
        self._run("""
            MATCH (q:QueryTemplate)
            SET q.description           = coalesce(q.description, ""),
                q.primary_intent        = coalesce(q.primary_intent, ""),
                q.intent_scores         = coalesce(q.intent_scores, "{}"),
                q.complexity            = coalesce(q.complexity, "simple"),
                q.anchor_table_fqns     = coalesce(q.anchor_table_fqns, []),
                q.cte_steps             = coalesce(q.cte_steps, []),
                q.required_aggregations = coalesce(q.required_aggregations, []),
                q.required_filters      = coalesce(q.required_filters, []),
                q.time_windowed         = coalesce(q.time_windowed, false),
                q.sql_pattern           = coalesce(q.sql_pattern, "multi_join"),
                q.is_cross_domain       = coalesce(q.is_cross_domain, false),
                q.min_cte_count         = coalesce(q.min_cte_count, 1),
                q.max_cte_count         = coalesce(q.max_cte_count, 5),
                q.enrichment_status     = coalesce(q.enrichment_status, "pending")
        """)
        log.debug("QueryTemplate property defaults initialized.")

    def initialize_joinpath_defaults(self):
        """Ensure every JoinPath node has all expected property keys."""
        self._run("""
            MATCH (jp:JoinPath)
            SET jp.is_cross_community = coalesce(jp.is_cross_community, false),
                jp.quality_score      = coalesce(jp.quality_score, 0.0),
                jp.hop_count          = coalesce(jp.hop_count, 0),
                jp.k_rank             = coalesce(jp.k_rank, 1),
                jp.join_clauses       = coalesce(jp.join_clauses, [])
        """)
        log.debug("JoinPath property defaults initialized.")
