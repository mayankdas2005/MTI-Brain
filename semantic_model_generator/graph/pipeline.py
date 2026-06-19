"""
Neo4j Semantic Layer Pipeline — main orchestration.

Usage:
  python -m semantic_model_generator.graph.pipeline [--steps STEPS] [--dry-run] [--reset-checkpoint]

Steps (comma-separated or 'all'):
  extract   Q1-Q16 from Redshift + parse semantic_model.yml
  infer     table type + FK inference
  load      apply schema + MERGE nodes/edges into Neo4j
  wcc       WCC → bridge isolated tables → re-run WCC
  gds       PageRank + Betweenness + Leiden + FastRP + Node Similarity
  enrich    LLM table/column/domain descriptions (Bedrock, with checkpoint)
  embed     Cohere column + table embeddings; KNN SEMANTICALLY_SIMILAR
  paths     Dijkstra + Yen's + cross-community JoinPath precomputation
  rollup    ROLLUP_OF edge detection + is_subquery_anchor
  intents   Intent nodes + RELEVANT_TO edges + intent_tags on Tables
  glossary  BusinessTerm nodes + embeddings
  templates QueryTemplate nodes from Questions.txt + embeddings
  all       run all steps in order (default)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import bedrock as bedrock_cfg
from .config import neo4j as neo4j_cfg
from .config import rs as rs_cfg
from .utils import is_uuid_col
from .enrich.embeddings import (
    embed_columns,
    embed_communities,
    embed_domains,
    embed_intents,
    embed_tables,
)
from .enrich.intents import (
    build_intent_node_dicts,
    compute_relevant_to_edges,
    load_intent_classes,
)
from .enrich.llm_enricher import (
    _bedrock_client,
    _langchain_bedrock,
    _load_checkpoint,
    _save_checkpoint,
    build_table_llm_input,
    enrich_columns,
    enrich_community,
    enrich_domain,
    enrich_intents,
    enrich_query_templates,
    enrich_tables,
    generate_business_glossary,
)
from .enrich.rollup import detect_rollup_candidates_from_graph
from .extract.redshift import RedshiftExtractor
from .extract.yml_parser import parse as parse_yml
from .gds.algorithms import GDSPipeline
from .gds.join_paths import JoinPathBuilder
from .infer.fk_infer import infer_fks
from .infer.table_type import infer_table_type
from .load.neo4j_loader import Neo4jLoader
from .models import ColumnMeta, FKEdge, TableMeta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
logging.getLogger("semantic_model_generator.graph.enrich.llm_enricher").setLevel(logging.DEBUG)
log = logging.getLogger("pipeline")

_ALL_STEPS = [
    "extract", "infer", "load", "wcc", "gds", "enrich",
    "paths", "rollup", "intents", "glossary", "templates", "embed",
]
_NOW = lambda: datetime.now(timezone.utc).isoformat()
_CHECKPOINT_FILE = Path(__file__).resolve().parent.parent / "graph_enrichment_cache.json"


def _idx(rows: list[dict], key: str) -> dict:
    """Index a list of dicts by a string key."""
    return {str(r.get(key, "")): r for r in rows}


def run(steps: list[str], dry_run: bool = False, reset_checkpoint: bool = False,
        reset_templates: bool = False, reset_glossary: bool = False, reset_communities: bool = False,
        table_filter: list[str] | None = None):
    run_id = str(uuid.uuid4())
    started_at = _NOW()
    run_meta: dict = {
        "run_id": run_id,
        "started_at": started_at,
        "steps": steps,
        "dry_run": dry_run,
    }
    _t0 = time.time()
    stats: dict = {k: 0 for k in [
        "tables_processed", "columns_processed", "edges_created",
        "llm_calls", "embed_calls", "errors",
    ]}

    if reset_checkpoint and _CHECKPOINT_FILE.exists():
        _CHECKPOINT_FILE.unlink()
        log.info("Checkpoint reset.")
    elif (reset_templates or reset_glossary or reset_communities) and _CHECKPOINT_FILE.exists():
        cp = _load_checkpoint()
        if reset_templates:
            cp.pop("query_templates", None)
            log.info("Templates cache cleared from checkpoint.")
        if reset_glossary:
            cp.pop("glossary_terms", None)
            cp.pop("glossary_context_offset", None)
            log.info("Glossary cache cleared from checkpoint.")
        if reset_communities:
            cp.pop("communities", None)
            log.info("Communities cache cleared from checkpoint.")
        _save_checkpoint(cp)

    loader = None if dry_run else Neo4jLoader(
        neo4j_cfg.uri, neo4j_cfg.user, neo4j_cfg.password, neo4j_cfg.db
    )
    gds = None if dry_run else GDSPipeline(
        neo4j_cfg.uri, neo4j_cfg.user, neo4j_cfg.password, neo4j_cfg.db
    )

    # ── EXTRACT ────────────────────────────────────────────────────────────

    tables_meta: list[TableMeta] = []
    columns_meta: list[ColumnMeta] = []
    sme_edges: list[FKEdge] = []
    raw: dict = {}

    if "extract" in steps:
        t = time.time()
        log.info("── EXTRACT ──────────────────────────────────────────────")

        log.info("Parsing lpp_semantic_model.yml …")
        tables_meta, columns_meta, sme_edges = parse_yml()
        log.info("YML: %d tables, %d columns (uuid-cols excluded), %d declared FK edges",
                 len(tables_meta), len(columns_meta), len(sme_edges))

        # Scope to specific tables if --tables was given
        if table_filter:
            _filter_fqns = set(table_filter)
            tables_meta  = [tm for tm in tables_meta  if tm.fqn in _filter_fqns]
            columns_meta = [cm for cm in columns_meta if cm.table_fqn in _filter_fqns]
            sme_edges    = [e  for e  in sme_edges    if e.from_table in _filter_fqns or e.to_table in _filter_fqns]
            log.info("TABLE FILTER: scoped to %d tables — %s", len(tables_meta), table_filter)

        yml_table_names: set[str] = {tm.name for tm in tables_meta}
        yml_col_names: set[tuple[str, str]] = {
            (cm.table_fqn.split(".")[-1], cm.name) for cm in columns_meta
        }

        log.info("Fetching Redshift metadata (Q1–Q16) scoped to %d YML tables …", len(yml_table_names))
        extractor = RedshiftExtractor(
            host=rs_cfg.host,
            database=rs_cfg.db,
            user=rs_cfg.user,
            password=rs_cfg.password,
            port=rs_cfg.port,
            schema=rs_cfg.schema,
        )
        raw = extractor.run_all(table_names=yml_table_names, col_names=yml_col_names)

        log.info("EXTRACT done in %.1fs", time.time() - t)

    # ── INFER ──────────────────────────────────────────────────────────────

    all_fk_edges: list[FKEdge] = list(sme_edges)
    col_map: dict[str, list[ColumnMeta]] = defaultdict(list)

    if "infer" in steps:
        t = time.time()
        log.info("── INFER ────────────────────────────────────────────────")

        # Enrich TableMeta + ColumnMeta from Redshift data
        q1_idx  = _idx(raw.get("tables", []), "table_name")
        q9_idx  = _idx(raw.get("col_type_dist", []), "table_name")
        q10_idx = _idx(raw.get("cardinality", []), "table_name")
        q11_idx = _idx(raw.get("encoding", []), "table_name")
        q2_by_table: dict[str, list[dict]] = defaultdict(list)
        for row in raw.get("columns", []):
            q2_by_table[row["table_name"]].append(row)
        q3_by_table: dict[str, dict[str, dict]] = defaultdict(dict)
        for row in raw.get("pg_stats", []):
            q3_by_table[row["table_name"]][row["column_name"]] = row

        # Declared PKs from Q4 (uuid-named columns excluded from PK tracking)
        declared_pks: set[tuple] = set()
        declared_fk_edges: list[FKEdge] = []
        for row in raw.get("constraints", []):
            col_name = row["column_name"]
            if row["constraint_type"] == "PRIMARY KEY":
                if not is_uuid_col(col_name):
                    declared_pks.add((row["table_name"], col_name))
            elif row["constraint_type"] == "FOREIGN KEY" and row.get("ref_table"):
                ref_col = row["ref_column"] or "code"
                # Skip uuid-based FK edges
                if is_uuid_col(col_name) or is_uuid_col(ref_col):
                    continue
                declared_fk_edges.append(FKEdge(
                    from_table=f"{rs_cfg.schema}.{row['table_name']}",
                    from_col=col_name,
                    to_table=f"{rs_cfg.schema}.{row['ref_table']}",
                    to_col=ref_col,
                    confidence=1.0,
                    source="declared_fk",
                    is_declared=True,
                ))

        # Update TableMeta from Redshift stats; infer table types
        updated_tables: list[TableMeta] = []
        for tm in tables_meta:
            name = tm.name
            q1 = q1_idx.get(name, {})
            q9 = q9_idx.get(name, {})
            q10 = q10_idx.get(name, {})
            q11 = q11_idx.get(name, {})
            q2_cols = q2_by_table.get(name, [])

            # Update stats from Redshift Q1 (only fields that still exist in TableMeta)
            tm.row_count   = int(q1.get("row_count") or tm.row_count)
            tm.diststyle   = q1.get("diststyle") or tm.diststyle
            tm.distkey_col = q1.get("distkey_col") or tm.distkey_col
            tm.sortkey1    = q1.get("sortkey1") or tm.sortkey1

            if tm.table_type != "derived":
                ttype, _, _ = infer_table_type(
                    table=q1, col_dist=q9, card=q10, enc=q11, columns=q2_cols
                )
                tm.table_type = ttype

            updated_tables.append(tm)

        # Merge Redshift stats into YML-defined ColumnMeta only
        col_by_id = {c.id: c for c in columns_meta}

        for name, q2_cols in q2_by_table.items():
            fqn = f"{rs_cfg.schema}.{name}"
            for row in q2_cols:
                col_name = row["column_name"]
                col_id   = f"{fqn}.{col_name}"
                q3_stat  = q3_by_table.get(name, {}).get(col_name, {})
                is_pk    = (name, col_name) in declared_pks

                cm = col_by_id.get(col_id)
                if cm:
                    cm.data_type        = row.get("data_type", cm.data_type)
                    cm.ordinal_position = int(row.get("ordinal_position") or cm.ordinal_position)
                    cm.is_nullable      = row.get("is_nullable", "YES") == "YES"
                    # is_notnull removed from ColumnMeta; use is_nullable instead
                    cm.is_pk            = is_pk or cm.is_pk
                    cm.null_frac        = float(q3_stat.get("null_frac") or 0)
                    cm.n_distinct       = float(q3_stat.get("n_distinct") or 0)
                    cm.source_hash      = cm.compute_source_hash()

        # Wire top_freq_values, distinct_values, sample_values from Redshift extracts
        q_topvals  = raw.get("top_freq_values", {})
        q_distinct = raw.get("distinct_values", {})
        q8         = raw.get("samples", {})
        for cm in columns_meta:
            tbl = cm.table_fqn.split(".")[-1]
            if fv := q_topvals.get(tbl, {}).get(cm.name):
                cm.top_freq_values = fv
            if dv := q_distinct.get(tbl, {}).get(cm.name):
                cm.distinct_values = dv
            if sv := q8.get(tbl, {}).get(cm.name):
                cm.sample_values = sv
            # sample_values fallback: Q8 has partial coverage (~5 cols/table cap)
            # distinct_values is authoritative for string cols — derive from it when Q8 missed
            if not cm.sample_values:
                if cm.distinct_values:
                    cm.sample_values = cm.distinct_values[:10]
                elif cm.top_freq_values:
                    cm.sample_values = [v.split(":")[0] for v in cm.top_freq_values[:10]]

        # Derive value_vocabulary per column
        for cm in columns_meta:
            if cm.distinct_values:
                cm.value_vocabulary = cm.distinct_values[:30]
            elif cm.top_freq_values:
                cm.value_vocabulary = [v.split(":")[0] for v in cm.top_freq_values[:30]]

            # filter_selectivity derived from n_distinct relative to row count
            nd = abs(cm.n_distinct) if cm.n_distinct < 0 else cm.n_distinct
            tbl_name = cm.table_fqn.split(".")[-1]
            row_count = int(q1_idx.get(tbl_name, {}).get("row_count") or 1)  # cast Decimal→int
            if nd > 0:
                ratio = nd / max(row_count, 1) if cm.n_distinct > 0 else abs(cm.n_distinct)
                if ratio > 0.5:
                    cm.filter_selectivity = "high"
                elif ratio >= 0.01:
                    cm.filter_selectivity = "medium"
                else:
                    cm.filter_selectivity = "low"

        # Derive DERIVED table-level structural properties (no LLM, no name patterns)
        _TS_DATA_TYPES = {"date", "timestamp", "timestamp with time zone",
                          "timestamp without time zone", "timestamptz"}
        _NUMERIC_GROUPING = {
            "numeric", "decimal", "integer", "bigint", "smallint",
            "double precision", "real", "float", "int",
        }
        _GROUPABLE_DT = {
            "character varying", "varchar", "char", "character",
            "boolean", "date", "timestamp", "timestamp with time zone",
            "timestamp without time zone", "timestamptz",
        }
        _JOIN_ROLE_MAP = {
            "fact": "center", "dimension": "lookup",
            "bridge": "bridge", "derived": "derived",
        }

        for tm in updated_tables:
            cols_for_table = [c for c in columns_meta if c.table_fqn == tm.fqn]

            # column_count
            tm.column_count = len(cols_for_table)

            # typical_join_role from table_type
            tm.typical_join_role = _JOIN_ROLE_MAP.get(tm.table_type, "unknown")

            # grain: join deduplicated non-uuid pk_columns (already set from YAML)
            if tm.pk_columns:
                tm.grain = ", ".join(tm.pk_columns)

            # is_time_series: PK has a date/timestamp column AND row_count > 500 AND has FK col
            pk_set = set(tm.pk_columns)
            has_date_pk = any(
                c.data_type.lower().split("(")[0].strip() in _TS_DATA_TYPES
                for c in cols_for_table if c.name in pk_set
            )
            has_fk_col = any(c.is_foreign_key for c in cols_for_table)
            tm.is_time_series = has_date_pk and tm.row_count > 500 and has_fk_col

            # natural_dimensions: groupable, not PK, not FK
            tm.natural_dimensions = [
                c.name for c in cols_for_table
                if c.is_groupable and not c.is_pk and not c.is_foreign_key
            ]

            # natural_measures: measurable (already set on ColumnMeta in yml_parser)
            tm.natural_measures = [
                c.name for c in cols_for_table if c.is_measurable
            ]

        # Compute source hashes on tables
        for tm in updated_tables:
            col_names = [c.name for c in columns_meta if c.table_fqn == tm.fqn]
            tm.source_hash = tm.compute_source_hash(col_names)

        tables_meta = updated_tables

        # Build col_map
        for cm in columns_meta:
            col_map[cm.table_fqn].append(cm)

        # Inferred FK edges
        all_fk_edges = list(sme_edges) + declared_fk_edges
        inferred = infer_fks(
            tables=tables_meta,
            col_map=col_map,
            existing_edges=all_fk_edges,
            query_history_rows=raw.get("stl_joins") or raw.get("stl_recent") or [],
        )
        all_fk_edges.extend(inferred)

        stats["tables_processed"] = len(tables_meta)
        stats["columns_processed"] = len(columns_meta)
        stats["edges_created"] = len(all_fk_edges)

        log.info("INFER: %d tables, %d columns, %d total FK edges (sme=%d declared=%d inferred=%d)",
                 len(tables_meta), len(columns_meta), len(all_fk_edges),
                 len(sme_edges), len(declared_fk_edges), len(inferred))
        log.info("INFER done in %.1fs", time.time() - t)

    # ── LOAD ───────────────────────────────────────────────────────────────

    if "load" in steps and not dry_run:
        t = time.time()
        log.info("── LOAD ─────────────────────────────────────────────────")
        loader.apply_schema()

        loader.load_tables(tables_meta)
        loader.load_columns(columns_meta)
        loader.link_columns_to_tables(columns_meta)
        loader.load_fk_edges(all_fk_edges)
        loader.initialize_table_defaults()
        loader.initialize_column_defaults()

        log.info("LOAD done in %.1fs", time.time() - t)

    # ── WCC ────────────────────────────────────────────────────────────────

    if "wcc" in steps and not dry_run:
        t = time.time()
        log.info("── WCC ──────────────────────────────────────────────────")

        gds.project_join_graph()
        gds.run_wcc()
        report = gds.get_wcc_report()

        log.info("WCC: isolated=%d small_clusters=%d pendants=%d",
                 len(report["isolated"]),
                 len(report["small_clusters"]),
                 len(report["pendants"]))

        # Bridge isolated tables using shared column names from Q15
        shared_col_index: dict[str, list[str]] = defaultdict(list)
        for row in raw.get("shared_cols", []):
            col_name = row["column_name"]
            for tbl in (row.get("tables") or "").split(","):
                tbl = tbl.strip()
                if tbl:
                    shared_col_index[col_name].append(tbl)

        bridge_edges: list[dict] = []
        main_cid = report["main_component_id"]
        isolated_fqns = (
            [r["fqn"] for r in report["isolated"]]
            + gds.get_small_cluster_fqns(main_cid)
        )

        # M1 — import the same blocklist used by FK inference
        from .infer.fk_infer import _GENERIC_PK_NAMES as _GENERIC_BRIDGE_COLS

        for iso_fqn in isolated_fqns:
            iso_name = iso_fqn.split(".")[-1]
            # Prefer FK-suffix columns over generic names for bridging
            iso_cols = sorted(
                [c.name for c in col_map.get(iso_fqn, [])],
                key=lambda n: (n.lower() in _GENERIC_BRIDGE_COLS, n),
            )

            for col_name in iso_cols:
                if col_name.lower() in _GENERIC_BRIDGE_COLS:
                    continue  # skip generic names; only use if no FK-suffix col found
                partners = shared_col_index.get(col_name, [])
                for partner_name in partners:
                    if partner_name == iso_name:
                        continue
                    partner_fqn = f"{rs_cfg.schema}.{partner_name}"
                    bridge_edges.append({
                        "from_fqn": iso_fqn,
                        "from_col": col_name,
                        "to_fqn": partner_fqn,
                        "to_col": col_name,
                        "confidence": 0.80,
                        "source": "wcc_shared_column",
                    })
                    break  # one bridge per isolated table
                if bridge_edges and bridge_edges[-1]["from_fqn"] == iso_fqn:
                    break  # found a bridge for this table

        if bridge_edges:
            gds.load_wcc_bridge_edges(bridge_edges)
            # Re-project and re-run WCC to verify
            gds._drop_graph("join_graph")
            gds.project_join_graph()
            gds.run_wcc()
            report2 = gds.get_wcc_report()
            log.info("Post-bridge WCC: isolated=%d", len(report2["isolated"]))

        gds.flag_isolated_tables()
        log.info("WCC done in %.1fs", time.time() - t)

    # ── GDS ────────────────────────────────────────────────────────────────

    if "gds" in steps and not dry_run:
        t = time.time()
        log.info("── GDS ──────────────────────────────────────────────────")

        # M2 — re-project join_graph in case GDS step runs without a preceding WCC step
        gds._drop_graph("join_graph")
        gds.project_join_graph()

        gds.run_pagerank()
        gds.run_betweenness()
        gds.run_scc()
        gds.run_triangle_count()
        gds.run_degree_centrality()

        # Leiden with gamma tuning
        gds.project_leiden_graph()
        leiden_meta = gds.run_leiden(gamma=1.2)
        gds._drop_graph("leiden_graph")

        leiden_valid = gds.validate_leiden()
        if not leiden_valid["ok"]:
            log.warning("Leiden validation failed — retrying with gamma=1.5")
            gds.project_leiden_graph()
            leiden_meta = gds.run_leiden(gamma=1.5)
            gds._drop_graph("leiden_graph")
            leiden_valid = gds.validate_leiden()
            if not leiden_valid["ok"]:
                log.warning("Leiden gamma=1.5 still not ideal: %s", leiden_valid)

        gds.build_community_nodes(leiden_meta)
        loader.initialize_community_defaults()

        # FastRP + Node Similarity
        gds.run_fastrp_for_node_similarity()
        gds.run_node_similarity(cutoff=0.5, top_k=10)

        gds._drop_graph("join_graph")

        log.info("GDS done in %.1fs", time.time() - t)

    # ── ENRICH ─────────────────────────────────────────────────────────────

    if "enrich" in steps and not dry_run:
        t = time.time()
        log.info("── ENRICH ───────────────────────────────────────────────")

        # Force re-enrichment for filtered tables: mark stale in Neo4j + purge from checkpoint
        if table_filter and loader:
            loader._run("""
                UNWIND $fqns AS fqn
                MATCH (t:Table {fqn: fqn})
                SET t.enrichment_status = 'stale'
                WITH t
                MATCH (t)-[:HAS_COLUMN]->(c:Column)
                SET c.enrichment_status = 'stale'
            """, fqns=list(table_filter))
            if _CHECKPOINT_FILE.exists():
                _cp = _load_checkpoint()
                for _fqn in table_filter:
                    _cp.get("tables", {}).pop(_fqn, None)
                    for _key in list(_cp.get("columns", {}).keys()):
                        if _key.startswith(_fqn + "."):
                            _cp["columns"].pop(_key, None)
                _save_checkpoint(_cp)
            log.info("TABLE FILTER enrich: marked %d tables/columns stale, cleared from checkpoint.",
                     len(table_filter))

        # C4 — rebuild tables_meta and col_map from Neo4j if INFER was skipped
        if not tables_meta and loader:
            tbl_rows_neo = loader._run("""
                MATCH (t:Table)
                RETURN t.fqn AS fqn, t.name AS name, t.table_type AS table_type,
                       t.ontology_class AS ontology_class,
                       toInteger(t.row_count) AS row_count,
                       t.sortkey1 AS sortkey1, t.business_domain AS business_domain
            """)
            for r in tbl_rows_neo:
                tm = TableMeta(
                    fqn=r["fqn"], name=r["name"],
                    schema=r["fqn"].split(".")[0],
                    table_type=r.get("table_type") or "",
                    ontology_class=r.get("ontology_class") or "",
                )
                tm.row_count       = r.get("row_count") or 0
                tm.sortkey1        = r.get("sortkey1") or ""
                tm.business_domain = r.get("business_domain") or ""
                tables_meta.append(tm)
            log.info("C4: rebuilt tables_meta from Neo4j (%d tables).", len(tables_meta))

        if not col_map and loader:
            col_rows_neo = loader._run("""
                MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
                RETURN c.table_fqn AS table_fqn, c.id AS id, c.name AS name,
                       c.data_type AS data_type, c.ordinal_position AS ordinal_position,
                       c.is_nullable AS is_nullable, c.is_pk AS is_pk,
                       c.null_frac AS null_frac,
                       c.n_distinct AS n_distinct, c.sample_values AS sample_values,
                       c.top_freq_values AS top_freq_values,
                       c.value_vocabulary AS value_vocabulary,
                       c.distinct_values AS distinct_values
            """)
            for r in col_rows_neo:
                cm = ColumnMeta(
                    table_fqn=r["table_fqn"], name=r["name"],
                    data_type=r.get("data_type", "varchar"),
                    ordinal_position=r.get("ordinal_position") or 0,
                    is_nullable=r.get("is_nullable", True),
                    is_pk=r.get("is_pk", False),
                    null_frac=float(r.get("null_frac") or 0),
                    n_distinct=float(r.get("n_distinct") or 0),
                    sample_values=r.get("sample_values") or [],
                    top_freq_values=r.get("top_freq_values") or [],
                    value_vocabulary=r.get("value_vocabulary") or [],
                    distinct_values=r.get("distinct_values") or [],
                )
                col_map[cm.table_fqn].append(cm)
            log.info("C4: rebuilt col_map from Neo4j (%d columns).", sum(len(v) for v in col_map.values()))

        bedrock_client = _bedrock_client(bedrock_cfg)
        chat_client    = _langchain_bedrock(bedrock_cfg)
        model_arn = bedrock_cfg.aws_bedrock_sonnet_arn

        checkpoint = _load_checkpoint()
        table_cache = checkpoint.get("tables", {})
        col_cache   = checkpoint.get("columns", {})

        # Identify tables needing enrichment (fetch enrichment_status from Neo4j)
        status_rows = loader._run("""
            MATCH (t:Table) WHERE t.enrichment_status IN ['pending','stale','failed']
            RETURN t.fqn AS fqn, t.table_type AS table_type,
                   t.row_count AS row_count, t.ontology_class AS ontology_class,
                   t.sortkey1 AS sortkey1
        """)
        fqns_to_enrich = {r["fqn"] for r in status_rows}

        tables_to_enrich = [
            tm for tm in tables_meta
            if tm.fqn in fqns_to_enrich or tm.fqn not in table_cache
        ]
        total = len(tables_to_enrich)

        # ── Phase 1: Column enrichment (all tables, table_description="" — no table desc yet) ──
        COL_FLUSH = 5
        log.info("ENRICH Phase 1 — column enrichment (%d tables) …", total)
        pending_col_rows: list[dict] = []
        for idx, tm in enumerate(tables_to_enrich, 1):
            cols_data = []
            for c in col_map.get(tm.fqn, []):
                cols_data.append({
                    "name": c.name, "data_type": c.data_type, "is_pk": c.is_pk,
                    "null_frac": c.null_frac, "n_distinct": c.n_distinct,
                    "sample_vals": c.sample_values[:10],
                    "value_vocabulary": c.value_vocabulary[:30],
                })

            col_results = enrich_columns(
                fqn=tm.fqn,
                table_description="",
                columns_data=cols_data,
                chat_client=chat_client,
                col_cache=col_cache,
            )
            stats["llm_calls"] += 1

            for col_name, col_enr in col_results.items():
                col_id = f"{tm.fqn}.{col_name}"
                pending_col_rows.append({
                    "col_id":             col_id,
                    "description":        col_enr.get("description", ""),
                    "semantic_type":      col_enr.get("semantic_type", ""),
                    "synonyms":           col_enr.get("synonyms") or [],
                    "is_pii":             bool(col_enr.get("is_pii", False)),
                    "pii_type":           col_enr.get("pii_type") or "",
                    "temporal_grain":     col_enr.get("temporal_grain") or "none",
                    "default_aggregation": col_enr.get("default_aggregation") or "NONE",
                    "value_aliases":      col_enr.get("value_aliases") or [],
                    "description_model":  model_arn,
                })

            # Save checkpoint after every table so interrupts lose at most 1 table
            checkpoint["columns"] = col_cache
            _save_checkpoint(checkpoint)

            if idx % COL_FLUSH == 0 or idx == total:
                loader.batch_update_column_enrichment(pending_col_rows, _NOW())
                log.info("ENRICH Phase 1: %d/%d tables done, %d cols flushed.",
                         idx, total, len(pending_col_rows))
                pending_col_rows = []

        loader.initialize_column_defaults()

        # ── Phase 2: Table enrichment with enriched column context from Neo4j ──
        TABLE_BATCH = 5
        log.info("ENRICH Phase 2 — table enrichment with enriched column context …")
        tables_llm_input = []
        tm_by_fqn: dict = {}
        for tm in tables_to_enrich:
            enriched_col_rows = loader._run("""
                MATCH (t:Table {fqn: $fqn})-[:HAS_COLUMN]->(c:Column)
                RETURN c.name AS name,
                       c.description AS description,
                       c.semantic_type AS semantic_type
                ORDER BY coalesce(c.ordinal_position, 9999)
            """, fqn=tm.fqn)
            enriched_columns = {
                r["name"]: {
                    "description":   r.get("description") or "",
                    "semantic_type": r.get("semantic_type") or "",
                }
                for r in enriched_col_rows
            }

            cols_for_llm = col_map.get(tm.fqn, [])
            tables_llm_input.append(build_table_llm_input(
                fqn=tm.fqn,
                ontology_class=tm.ontology_class,
                table_type=tm.table_type,
                type_confidence=0.0,
                row_count=tm.row_count,
                size_mb=0.0,
                diststyle=tm.diststyle,
                distkey_col=tm.distkey_col,
                sortkey1=tm.sortkey1,
                columns=[{
                    "name": c.name,
                    "data_type": c.data_type,
                    "is_pk": c.is_pk,
                    "is_notnull": getattr(c, "is_notnull", False),
                    "null_frac": c.null_frac,
                    "n_distinct": c.n_distinct,
                    "sample_values": c.sample_values,
                    "most_common_vals": c.most_common_vals,
                } for c in cols_for_llm],
                enriched_columns=enriched_columns,
            ))
            tm_by_fqn[tm.fqn] = tm

        tables_not_cached = [t for t in tables_llm_input if t["fqn"] not in table_cache]
        total_t = len(tables_not_cached)
        log.info("ENRICH Phase 2: %d to enrich, %d from cache.",
                 total_t, len(tables_llm_input) - total_t)

        for batch_start in range(0, max(total_t, 1), TABLE_BATCH):
            batch = tables_not_cached[batch_start:batch_start + TABLE_BATCH]
            if not batch:
                break
            batch_results = enrich_tables(batch, chat_client, {})
            table_cache.update(batch_results)
            stats["llm_calls"] += len(batch)

            pending_tbl_rows = []
            for tbl_input in batch:
                fqn = tbl_input["fqn"]
                enr = table_cache.get(fqn, {})
                if enr.get("_enrichment_failed"):
                    continue
                tm_obj = tm_by_fqn.get(fqn)
                desc   = enr.get("description", "")
                domain = enr.get("business_domain") or (tm_obj.business_domain if tm_obj else "") or ""
                pending_tbl_rows.append({
                    "fqn":                fqn,
                    "description":        desc,
                    "description_model":  model_arn,
                    "synonyms":           enr.get("synonyms") or [],
                    "grain":              enr.get("grain") or "",
                    "business_domain":    domain,
                    "table_type_override": enr.get("table_type_override") or "",
                })

            if pending_tbl_rows:
                loader.batch_update_table_enrichment(pending_tbl_rows, _NOW())
            checkpoint["tables"] = table_cache
            _save_checkpoint(checkpoint)
            done_count = min(batch_start + TABLE_BATCH, total_t)
            log.info("ENRICH Phase 2: %d/%d tables written.", done_count, total_t)

        # Build domain_table_descriptions from full cache for Phase 3
        domain_table_descriptions: dict[str, list[tuple]] = defaultdict(list)
        for tm in tables_to_enrich:
            enr = table_cache.get(tm.fqn, {})
            if enr.get("_enrichment_failed"):
                continue
            desc   = enr.get("description", "")
            domain = enr.get("business_domain") or tm.business_domain
            if domain and desc:
                domain_table_descriptions[domain].append((tm.name, desc))

        # Create Domain nodes from enriched business_domain values + link tables
        enriched_domains = list(domain_table_descriptions.keys())
        if enriched_domains:
            loader.load_domains(enriched_domains)
            loader._run("""
                MATCH (t:Table) WHERE t.business_domain IS NOT NULL AND t.business_domain <> ''
                MATCH (d:Domain {name: t.business_domain})
                MERGE (t)-[:BELONGS_TO]->(d)
            """)
            log.info("Created %d Domain nodes from enrichment.", len(enriched_domains))

        loader.initialize_table_defaults()

        # ── Phase 3: Domain voting + community enrichment + domain enrichment ──
        log.info("ENRICH Phase 3 — domain voting + community + domain enrichment …")

        # Re-run domain voting now that business_domain is set on all tables
        loader._run("""
            MATCH (t:Table)
            WHERE t.community_id IS NOT NULL
              AND t.business_domain IS NOT NULL AND t.business_domain <> ''
            WITH t.community_id AS cid, t.business_domain AS dom,
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
        log.info("  Domain voting updated on Community nodes.")

        # Community enrichment with actual table descriptions + join patterns
        community_rows_enrich = loader._run("""
            MATCH (c:Community)-[:CONTAINS_TABLE]->(t:Table)
            OPTIONAL MATCH (t)-[:JOINS_TO]->(t2:Table) WHERE t2.community_id = c.id
            WITH c,
                 collect(DISTINCT {
                     name: t.name,
                     description: coalesce(t.description, ''),
                     domain: coalesce(t.business_domain, '')
                 })[0..15] AS tables,
                 collect(DISTINCT t2.name)[0..8] AS frequent_joins
            RETURN c.id AS id, c.dominant_domain AS dominant_domain,
                   tables, frequent_joins
        """)
        comm_cache = checkpoint.get("communities", {})
        community_llm_input = [
            {
                "id": r["id"],
                "dominant_domain": r.get("dominant_domain"),
                "tables": r.get("tables", []),
                "frequent_joins": r.get("frequent_joins", []),
            }
            for r in community_rows_enrich
        ]
        def _save_comm(results):
            checkpoint["communities"] = results
            _save_checkpoint(checkpoint)

        comm_enriched = enrich_community(community_llm_input, chat_client, comm_cache,
                                         checkpoint_fn=_save_comm)
        loader.load_community_descriptions(comm_enriched)
        loader.initialize_community_defaults()
        log.info("  Community enrichment done (%d communities).", len(comm_enriched))

        # Domain descriptions
        for domain_name, table_descs in domain_table_descriptions.items():
            if not table_descs:
                continue
            domain_desc = enrich_domain(domain_name, table_descs, bedrock_client, model_arn)
            if domain_desc:
                loader.update_domain_description(domain_name, domain_desc)

        loader.initialize_column_defaults()
        loader.initialize_domain_defaults()

        log.info("ENRICH done in %.1fs", time.time() - t)

    # ── PATHS ──────────────────────────────────────────────────────────────

    if "paths" in steps and not dry_run:
        t = time.time()
        log.info("── PATHS ────────────────────────────────────────────────")

        # Run typical_join_role pass before cross-community path scoping
        loader.run_post_enrich_passes()

        gds.project_join_graph()
        gds.build_bridges_to_edges()

        path_builder = JoinPathBuilder(
            neo4j_cfg.uri, neo4j_cfg.user, neo4j_cfg.password, neo4j_cfg.db
        )
        path_builder.run_dijkstra_all_pairs(max_hops=6)
        path_builder.run_yens_all_pairs(k=3, max_hops=6)
        path_builder.run_cross_community_paths(max_hops=6)
        path_builder.run_quality_scores()
        path_builder.close()
        gds._drop_graph("join_graph")
        loader.initialize_joinpath_defaults()

        log.info("PATHS done in %.1fs", time.time() - t)

    # ── ROLLUP ─────────────────────────────────────────────────────────────

    if "rollup" in steps and not dry_run:
        t = time.time()
        log.info("── ROLLUP ───────────────────────────────────────────────")

        candidates = detect_rollup_candidates_from_graph(loader)
        validated = candidates  # schema-validated; confidence = column overlap ratio

        now = _NOW()
        for v in validated:
            loader._run("""
                MATCH (rollup:Table {fqn: $rfqn})
                MATCH (base:Table {fqn: $bfqn})
                MERGE (rollup)-[r:ROLLUP_OF]->(base)
                SET r.window_type  = $window_type,
                    r.window_days  = $window_days,
                    r.confidence   = $confidence,
                    r.computed_at  = $now
                SET rollup.is_rollup          = true,
                    rollup.rollup_base_fqn    = $bfqn,
                    rollup.rollup_window_days = $window_days
            """,
            rfqn=v["rollup_fqn"], bfqn=v["base_fqn"],
            window_type=v.get("window_type"), window_days=v.get("window_days"),
            confidence=v.get("confidence", 0.9), now=now)

        loader.run_is_subquery_anchor()
        log.info("ROLLUP: %d ROLLUP_OF edges written.", len(validated))
        log.info("ROLLUP done in %.1fs", time.time() - t)

    # ── INTENTS ────────────────────────────────────────────────────────────

    if "intents" in steps and not dry_run:
        t = time.time()
        log.info("── INTENTS ──────────────────────────────────────────────")

        chat_client = _langchain_bedrock(bedrock_cfg)

        intent_json_path = Path(__file__).resolve().parents[1] / "output" / "intent_classes.json"
        intent_classes = load_intent_classes(intent_json_path)

        def _to_pascal(snake: str) -> str:
            return "".join(w.capitalize() for w in snake.split("_"))

        # Build ontology_class → [fqn] index from table names (PascalCase)
        # ontology_class on the node may be empty — derive from table name as fallback
        class_rows = loader._run("""
            MATCH (t:Table)
            RETURN t.fqn AS fqn, t.name AS name,
                   coalesce(t.ontology_class, '') AS ontology_class
        """)
        table_fqn_by_class: dict[str, list[str]] = defaultdict(list)
        for r in class_rows:
            _oc = r["ontology_class"]
            cls = _oc.split(":")[-1] if _oc else _to_pascal(r["name"])
            table_fqn_by_class[cls].append(r["fqn"])

        # Persist derived ontology_class back to nodes that are missing it
        missing_rows = [
            {"fqn": r["fqn"], "cls": f"{r['fqn'].split('.')[0]}:{_to_pascal(r['name'])}"}
            for r in class_rows if not r["ontology_class"]
        ]
        if missing_rows:
            loader._batch_write("""
                UNWIND $rows AS r
                MATCH (t:Table {fqn: r.fqn})
                SET t.ontology_class = r.cls
            """, missing_rows)
            log.info("INTENTS: set ontology_class on %d tables that were missing it.", len(missing_rows))

        # Query enriched table context for each ontology class
        tbl_ctx_rows = loader._run("""
            MATCH (t:Table)
            RETURN t.name AS name,
                   CASE WHEN coalesce(t.ontology_class,'') <> ''
                        THEN split(t.ontology_class,':')[-1]
                        ELSE t.name END AS cls,
                   coalesce(t.description, '') AS description,
                   coalesce(t.business_domain, '') AS domain,
                   coalesce(t.natural_measures, []) AS measures
        """)
        tbl_ctx_by_cls: dict[str, list[dict]] = defaultdict(list)
        for r in tbl_ctx_rows:
            tbl_ctx_by_cls[r["cls"]].append({
                "name": r["name"], "description": r["description"],
                "domain": r["domain"], "measures": r.get("measures") or [],
            })

        # Build intent_input with table-level context (no column details — intent is table-level)
        intent_input = []
        for name, classes in intent_classes.items():
            tables_for_intent: list[dict] = []
            seen_names: set[str] = set()
            for cls in classes:
                for tbl in tbl_ctx_by_cls.get(cls, []):
                    if tbl["name"] not in seen_names:
                        seen_names.add(tbl["name"])
                        tables_for_intent.append(tbl)
            intent_input.append({
                "intent": name,
                "classes": classes,
                "class_count": len(classes),
                "tables": tables_for_intent[:8],
            })

        intent_descriptions = enrich_intents(intent_input, chat_client)

        # Build and load Intent nodes
        intent_nodes = build_intent_node_dicts(intent_classes, intent_descriptions)
        loader.load_intent_nodes(intent_nodes)

        # Build RELEVANT_TO edges and load them
        relevant_edges = compute_relevant_to_edges(intent_classes, table_fqn_by_class)
        loader.load_relevant_to_edges(relevant_edges)

        # Derive intent_tags on Tables
        loader.set_intent_tags_on_tables()

        # Populate match_columns on RELEVANT_TO edges
        loader._pass_relevant_to_match_columns()

        loader._run("""
            MATCH (i:Intent) WHERE i.description IS NOT NULL AND i.description <> ''
            SET i.enrichment_status = 'complete'
        """)
        loader.initialize_intent_defaults()
        log.info("INTENTS: %d nodes, %d edges.", len(intent_nodes), len(relevant_edges))
        log.info("INTENTS done in %.1fs", time.time() - t)

    # ── GLOSSARY ───────────────────────────────────────────────────────────

    if "glossary" in steps and not dry_run:
        t = time.time()
        log.info("── GLOSSARY ─────────────────────────────────────────────")

        bedrock_client = _bedrock_client(bedrock_cfg)
        chat_client = _langchain_bedrock(bedrock_cfg)

        # Build context: enriched column synonyms, semantic types, value aliases + table descriptions
        context_rows = loader._run("""
            MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
            WHERE size(coalesce(c.synonyms, [])) > 0
               OR size(coalesce(c.value_aliases, [])) > 0
            RETURN t.name AS table_name, t.fqn AS table_fqn, t.description AS table_desc,
                   c.name AS col_name, c.synonyms AS synonyms,
                   c.semantic_type AS semantic_type,
                   c.value_aliases AS value_aliases,
                   c.value_vocabulary AS value_vocabulary
            ORDER BY size(coalesce(c.value_aliases, [])) DESC,
                     size(coalesce(c.value_vocabulary, [])) DESC,
                     size(coalesce(c.synonyms, [])) DESC
            LIMIT 2000
        """)
        fqn_by_name_gloss: dict[str, str] = {r["table_name"]: r["table_fqn"] for r in context_rows}

        # Build (table_name -> {token_lower -> col_name}) for source_column_name lookup
        _col_token_idx: dict[str, dict[str, str]] = {}
        for r in context_rows:
            tbl, col = r["table_name"], r["col_name"]
            tbl_idx = _col_token_idx.setdefault(tbl, {})
            for alias_pair in (r.get("value_aliases") or []):
                key = alias_pair.split("->")[0].strip().lower()
                if key:
                    tbl_idx[key] = col
            for val in (r.get("value_vocabulary") or []):
                tbl_idx[val.strip().lower()] = col
            for syn in (r.get("synonyms") or []):
                tbl_idx[syn.strip().lower()] = col

        def _find_source_col(term_dict: dict, table_name: str) -> str:
            tbl_idx = _col_token_idx.get(table_name, {})
            candidates = [term_dict.get("term", "")] + list(term_dict.get("variants") or [])
            for c in candidates:
                hit = tbl_idx.get(c.strip().lower())
                if hit:
                    return hit
            return ""
        context_lines = []
        for r in context_rows:
            syns = ", ".join(r.get("synonyms") or [])
            aliases = ", ".join(r.get("value_aliases") or [])
            vocab = ", ".join(r.get("value_vocabulary") or [])
            sem_type = r.get("semantic_type") or ""
            line = f"{r['table_name']}.{r['col_name']}"
            if sem_type:
                line += f" [{sem_type}]"
            line += f": synonyms=[{syns}]"
            if aliases:
                line += f" aliases=[{aliases}]"
            if vocab:
                line += f" values=[{vocab}]"
            context_lines.append(line)

        GLOSS_BATCH = 15
        glossary_checkpoint = _load_checkpoint()
        cached_terms: list[dict] = glossary_checkpoint.get("glossary_terms") or []
        glossary_offset: int = glossary_checkpoint.get("glossary_context_offset", 0)

        if glossary_offset >= len(context_lines) and cached_terms:
            log.info("GLOSSARY: %d terms from checkpoint — all %d context rows done, skipping LLM.",
                     len(cached_terms), len(context_lines))
            for bt in cached_terms:
                names = bt.get("related_table_names") or []
                if not bt.get("related_table_fqns"):
                    bt["related_table_fqns"] = [fqn_by_name_gloss[n] for n in names if n in fqn_by_name_gloss]
                # Use cached LLM source_column_name; fall back to token-index lookup
                if not bt.get("source_column_name"):
                    bt["source_column_name"] = next(
                        (_find_source_col(bt, n) for n in names if _find_source_col(bt, n)), ""
                    )
            loader.load_business_terms(cached_terms)
            loader.load_business_term_table_edges(cached_terms)
            terms = cached_terms
        else:
            existing_term_names: set[str] = {bt["term"] for bt in cached_terms}
            all_terms: list[dict] = list(cached_terms)

            log.info("GLOSSARY: %d context lines total, resuming from offset %d, %d terms cached.",
                     len(context_lines), glossary_offset, len(all_terms))

            for i in range(glossary_offset, len(context_lines), GLOSS_BATCH):
                chunk = context_lines[i:i + GLOSS_BATCH]
                done_rows = min(i + GLOSS_BATCH, len(context_lines))
                log.info("GLOSSARY: calling LLM for context rows %d–%d / %d ...",
                         i + 1, done_rows, len(context_lines))
                batch_terms = generate_business_glossary("\n".join(chunk), chat_client)
                for bt in batch_terms:
                    names = bt.get("related_table_names") or []
                    bt["related_table_fqns"] = [fqn_by_name_gloss[n] for n in names if n in fqn_by_name_gloss]
                    # Use LLM-provided source_column_name; fall back to token-index lookup
                    if not bt.get("source_column_name"):
                        bt["source_column_name"] = next(
                            (_find_source_col(bt, n) for n in names if _find_source_col(bt, n)), ""
                        )
                new_terms = [bt for bt in batch_terms if bt["term"] not in existing_term_names]
                all_terms.extend(new_terms)
                existing_term_names.update(bt["term"] for bt in new_terms)
                if new_terms:
                    loader.load_business_terms(new_terms)
                    loader.load_business_term_table_edges(new_terms)
                glossary_checkpoint["glossary_terms"] = all_terms
                glossary_checkpoint["glossary_context_offset"] = done_rows
                _save_checkpoint(glossary_checkpoint)
                log.info("GLOSSARY: %d/%d rows done — %d new terms, %d total written to Neo4j.",
                         done_rows, len(context_lines), len(new_terms), len(all_terms))

            terms = all_terms

        loader.initialize_businessterm_defaults()
        log.info("GLOSSARY: %d BusinessTerm nodes written.", len(terms))
        log.info("GLOSSARY done in %.1fs", time.time() - t)

    # ── TEMPLATES ──────────────────────────────────────────────────────────

    if "templates" in steps and not dry_run:
        t = time.time()
        log.info("── TEMPLATES ────────────────────────────────────────────")

        bedrock_client = _bedrock_client(bedrock_cfg)
        chat_client = _langchain_bedrock(bedrock_cfg)

        questions_path = Path(__file__).resolve().parents[1] / "output" / "Questions.txt"
        questions_raw = questions_path.read_text(encoding="utf-8").splitlines()
        questions = [
            {"source_line": i, "question_text": line.strip()}
            for i, line in enumerate(questions_raw)
            if line.strip()
        ]

        intent_json_path = Path(__file__).resolve().parents[1] / "output" / "intent_classes.json"
        intent_classes = load_intent_classes(intent_json_path)
        intents_list = list(intent_classes.keys())

        # Fetch all tables for LLM anchor selection and name→fqn resolution
        table_rows_qt = loader._run("""
            MATCH (t:Table)
            RETURN t.name AS name, t.fqn AS fqn,
                   t.description AS description, t.business_domain AS domain
        """)
        tables_for_llm = [
            {"name": r["name"], "description": r.get("description") or "", "domain": r.get("domain") or ""}
            for r in table_rows_qt
        ]
        fqn_by_name_qt: dict[str, str] = {r["name"]: r["fqn"] for r in table_rows_qt}

        checkpoint = _load_checkpoint()

        # Migrate corrupted checkpoint: old bug saved template results at root level
        # instead of under "query_templates" key — recover them if present.
        if "query_templates" not in checkpoint:
            recovered = {
                k: v for k, v in checkpoint.items()
                if k.isdigit() and isinstance(v, dict) and "source_line" in v
            }
            if recovered:
                log.info("TEMPLATES: migrating %d cached results from corrupted checkpoint.", len(recovered))
                checkpoint["query_templates"] = recovered
                _save_checkpoint(checkpoint)

        qt_cache = checkpoint.get("query_templates", {})

        to_enrich_qt = [q for q in questions if str(q["source_line"]) not in qt_cache]
        n_cached_qt = len(questions) - len(to_enrich_qt)
        log.info("TEMPLATES: %d questions total, %d cached, %d to enrich.",
                 len(questions), n_cached_qt, len(to_enrich_qt))

        def _build_qt_dicts(qs: list[dict]) -> tuple:
            tmpls, i_edges, t_edges = [], [], []
            for q in qs:
                sl = q["source_line"]
                enr = qt_cache.get(str(sl))
                if not enr:
                    continue
                qt_id = f"qt_{sl:03d}"
                intent_scores: dict = enr.get("intent_scores") or {"general_analytics": 0.5}
                # Normalise so the top intent always scores 1.0 — preserves ranking,
                # prevents artificially low confidence when LLM spreads probability thin
                _max = max(intent_scores.values()) if intent_scores else 1.0
                if _max > 0:
                    intent_scores = {k: round(v / _max, 4) for k, v in intent_scores.items()}
                primary_intent = max(intent_scores, key=intent_scores.get)
                anchor_names = enr.get("anchor_table_names") or []
                anchor_fqns = [fqn_by_name_qt[n] for n in anchor_names if n in fqn_by_name_qt]
                tmpls.append({
                    "id":                      qt_id,
                    "question_text":           q["question_text"],
                    "description":             enr.get("description", q["question_text"]),
                    "primary_intent":          primary_intent,
                    "intent_scores":           [{"name": k, "score": float(v)} for k, v in intent_scores.items()],
                    "complexity":              enr.get("complexity", "complex"),
                    "anchor_table_fqns":       anchor_fqns,
                    "cte_steps":               enr.get("cte_steps") or [],
                    "required_aggregations":   enr.get("required_aggregations") or [],
                    "required_filters":        enr.get("required_filters") or [],
                    "time_windowed":           bool(enr.get("time_windowed", False)),
                    "sql_pattern":             enr.get("sql_pattern") or "multi_join",
                    "is_cross_domain":         bool(enr.get("is_cross_domain", False)),
                    "min_cte_count":           int(enr.get("min_cte_count") or 1),
                    "max_cte_count":           int(enr.get("max_cte_count") or 5),
                    "source_line":             sl,
                })
                for intent_name, conf in intent_scores.items():
                    i_edges.append({"qt_id": qt_id, "intent_name": intent_name, "confidence": float(conf)})
                for fqn in anchor_fqns:
                    t_edges.append({"qt_id": qt_id, "table_fqn": fqn})
            return tmpls, i_edges, t_edges

        all_templates_to_load: list[dict] = []

        # Write already-cached templates to Neo4j first
        if n_cached_qt:
            cached_qs = [q for q in questions if str(q["source_line"]) in qt_cache]
            c_tmpls, c_i_edges, c_t_edges = _build_qt_dicts(cached_qs)
            if c_tmpls:
                loader.load_query_templates(c_tmpls)
                loader.link_query_templates_to_intents(c_i_edges)
                loader.link_query_templates_to_tables(c_t_edges)
                all_templates_to_load.extend(c_tmpls)
                log.info("TEMPLATES: %d cached templates written to Neo4j.", len(c_tmpls))

        # Process uncached in batches of 10 — write to Neo4j after each batch
        TMPL_BATCH = 10
        for batch_start in range(0, len(to_enrich_qt), TMPL_BATCH):
            batch = to_enrich_qt[batch_start:batch_start + TMPL_BATCH]
            batch_result = enrich_query_templates(
                questions=batch,
                intents=intents_list,
                tables=tables_for_llm,
                chat_client=chat_client,
                cache={},
                batch_size=TMPL_BATCH,
            )
            qt_cache.update(batch_result)

            b_tmpls, b_i_edges, b_t_edges = _build_qt_dicts(batch)
            if b_tmpls:
                loader.load_query_templates(b_tmpls)
                loader.link_query_templates_to_intents(b_i_edges)
                loader.link_query_templates_to_tables(b_t_edges)
                all_templates_to_load.extend(b_tmpls)

            checkpoint["query_templates"] = qt_cache
            _save_checkpoint(checkpoint)
            done = min(batch_start + TMPL_BATCH, len(to_enrich_qt))
            log.info("TEMPLATES: %d/%d enriched and written to Neo4j.",
                     n_cached_qt + done, len(questions))

        templates_to_load = all_templates_to_load

        loader.initialize_querytemplate_defaults()
        log.info("TEMPLATES: %d QueryTemplate nodes written.", len(templates_to_load))
        log.info("TEMPLATES done in %.1fs", time.time() - t)

    # ── EMBED ──────────────────────────────────────────────────────────────

    if "embed" in steps and not dry_run:
        t = time.time()
        log.info("── EMBED ────────────────────────────────────────────────")

        # Null out embeddings for filtered tables/columns so embed step re-runs them
        if table_filter and loader:
            loader._run("""
                UNWIND $fqns AS fqn
                MATCH (t:Table {fqn: fqn})
                SET t.cohere_embedding = null
                WITH t
                MATCH (t)-[:HAS_COLUMN]->(c:Column)
                SET c.cohere_embedding = null
            """, fqns=list(table_filter))
            log.info("TABLE FILTER embed: cleared embeddings for %d tables to force re-embed.",
                     len(table_filter))

        EMBED_BATCH = 50
        bedrock_client = _bedrock_client(bedrock_cfg)
        model_arn = bedrock_cfg.aws_bedrock_cohere_embed_v4_arn

        # Normalise any legacy 'enriched' status to 'complete' across all node types
        loader._run("""
            MATCH (n) WHERE n.enrichment_status = 'enriched'
            SET n.enrichment_status = 'complete'
        """)

        # Column embeddings — name + description + synonyms (array-joined at embed time)
        col_rows = loader._run("""
            MATCH (c:Column) WHERE c.cohere_embedding IS NULL
               OR c.enrichment_status = 'stale'
            RETURN c.id AS id, c.name AS name,
                   c.description AS description,
                   c.synonyms AS synonyms,
                   c.value_vocabulary AS value_vocabulary
        """)
        if col_rows:
            col_embs = embed_columns(col_rows, bedrock_client, model_arn)
            now = _NOW()
            emb_rows = [{"col_id": e["id"], "emb": e["embedding"], "model": model_arn}
                        for e in col_embs]
            for i in range(0, len(emb_rows), EMBED_BATCH):
                loader._batch_write("""
                    UNWIND $rows AS r
                    MATCH (c:Column {id: r.col_id})
                    SET c.cohere_embedding       = r.emb,
                        c.embedding_model        = r.model,
                        c.embedding_generated_at = $now,
                        c.updated_at             = $now
                """, emb_rows[i:i + EMBED_BATCH], now=now)
                log.info("EMBED cols: %d/%d written.",
                         min(i + EMBED_BATCH, len(emb_rows)), len(emb_rows))
            stats["embed_calls"] += 1

        # Table embeddings — name + domain + description + synonyms (array-joined) + top cols
        tbl_rows = loader._run("""
            MATCH (t:Table) WHERE t.cohere_embedding IS NULL
               OR t.enrichment_status = 'stale'
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
            WITH t, c ORDER BY coalesce(c.ordinal_position, 9999)
            WITH t, collect(c.name)[0..8] AS top_cols
            RETURN t.fqn AS fqn, t.name AS name,
                   t.description AS description,
                   t.business_domain AS business_domain,
                   reduce(s = '', syn IN coalesce(t.synonyms, []) | s + ' ' + syn) AS synonyms_text,
                   top_cols AS top_col_names
        """)
        if tbl_rows:
            tbl_embs = embed_tables(tbl_rows, bedrock_client, model_arn)
            now = _NOW()
            emb_rows = [{"fqn": e["fqn"], "emb": e["embedding"], "model": model_arn}
                        for e in tbl_embs]
            for i in range(0, len(emb_rows), EMBED_BATCH):
                loader._batch_write("""
                    UNWIND $rows AS r
                    MATCH (t:Table {fqn: r.fqn})
                    SET t.cohere_embedding       = r.emb,
                        t.embedding_model        = r.model,
                        t.embedding_generated_at = $now,
                        t.updated_at             = $now
                """, emb_rows[i:i + EMBED_BATCH], now=now)
                log.info("EMBED tables: %d/%d written.",
                         min(i + EMBED_BATCH, len(emb_rows)), len(emb_rows))
            stats["embed_calls"] += 1

        # Intent embeddings
        intent_rows = loader._run("""
            MATCH (i:Intent) WHERE i.description IS NOT NULL AND i.description <> ''
              AND (i.cohere_embedding IS NULL OR i.enrichment_status = 'stale')
            RETURN i.name AS name, i.description AS description
        """)
        if intent_rows:
            intent_embs = embed_intents(intent_rows, bedrock_client, model_arn)
            now = _NOW()
            emb_rows = [{"name": e["name"], "emb": e["embedding"], "model": model_arn}
                        for e in intent_embs]
            loader._batch_write("""
                UNWIND $rows AS r
                MATCH (i:Intent {name: r.name})
                SET i.cohere_embedding       = r.emb,
                    i.embedding_model        = r.model,
                    i.embedding_generated_at = $now
            """, emb_rows, now=now)
            log.info("EMBED intents: %d written.", len(emb_rows))

        # Community embeddings
        comm_rows = loader._run("""
            MATCH (c:Community) WHERE c.description IS NOT NULL AND c.description <> ''
              AND (c.cohere_embedding IS NULL OR c.enrichment_status = 'stale')
            RETURN c.id AS id, c.dominant_domain AS dominant_domain,
                   c.description AS description
        """)
        if comm_rows:
            comm_embs = embed_communities(comm_rows, bedrock_client, model_arn)
            now = _NOW()
            emb_rows = [{"comm_id": e["id"], "emb": e["embedding"], "model": model_arn}
                        for e in comm_embs]
            loader._batch_write("""
                UNWIND $rows AS r
                MATCH (c:Community {id: r.comm_id})
                SET c.cohere_embedding       = r.emb,
                    c.embedding_model        = r.model,
                    c.embedding_generated_at = $now
            """, emb_rows, now=now)
            log.info("EMBED communities: %d written.", len(emb_rows))

        # Domain embeddings
        domain_rows = loader._run("""
            MATCH (d:Domain) WHERE d.description IS NOT NULL AND d.description <> ''
              AND (d.cohere_embedding IS NULL OR d.enrichment_status = 'stale')
            RETURN d.name AS name, d.description AS description
        """)
        if domain_rows:
            domain_embs = embed_domains(domain_rows, bedrock_client, model_arn)
            now = _NOW()
            emb_rows = [{"name": e["name"], "emb": e["embedding"], "model": model_arn}
                        for e in domain_embs]
            loader._batch_write("""
                UNWIND $rows AS r
                MATCH (d:Domain {name: r.name})
                SET d.cohere_embedding       = r.emb,
                    d.embedding_model        = r.model,
                    d.embedding_generated_at = $now
            """, emb_rows, now=now)
            log.info("EMBED domains: %d written.", len(emb_rows))

        # BusinessTerm embeddings (produced by GLOSSARY step)
        bt_rows = loader._run("""
            MATCH (b:BusinessTerm)
            WHERE b.cohere_embedding IS NULL OR NOT (size(b.cohere_embedding) > 100)
            RETURN b.term AS term, b.description AS description
        """)
        if bt_rows:
            now = _NOW()
            from .enrich.embeddings import embed_texts as _embed_texts
            texts = [r["term"] + " " + (r.get("description") or "") for r in bt_rows]
            embeddings = _embed_texts(texts, bedrock_client, model_arn, input_type="search_document")
            emb_rows = [{"term": r["term"], "emb": e, "model": model_arn}
                        for r, e in zip(bt_rows, embeddings)]
            for i in range(0, len(emb_rows), EMBED_BATCH):
                loader._batch_write("""
                    UNWIND $rows AS r
                    MATCH (b:BusinessTerm {term: r.term})
                    SET b.cohere_embedding       = r.emb,
                        b.embedding_model        = r.model,
                        b.embedding_generated_at = $now
                """, emb_rows[i:i + EMBED_BATCH], now=now)
                log.info("EMBED business terms: %d/%d written.",
                         min(i + EMBED_BATCH, len(emb_rows)), len(emb_rows))

        # QueryTemplate embeddings (produced by TEMPLATES step)
        qt_rows = loader._run("""
            MATCH (q:QueryTemplate)
            WHERE q.cohere_embedding IS NULL OR NOT (size(q.cohere_embedding) > 100)
            RETURN q.id AS id, q.question_text AS question_text
        """)
        if qt_rows:
            now = _NOW()
            from .enrich.embeddings import embed_texts as _embed_texts
            texts = [r["question_text"] for r in qt_rows]
            embeddings = _embed_texts(texts, bedrock_client, model_arn, input_type="search_query")
            emb_rows = [{"qt_id": r["id"], "emb": e, "model": model_arn}
                        for r, e in zip(qt_rows, embeddings)]
            for i in range(0, len(emb_rows), EMBED_BATCH):
                loader._batch_write("""
                    UNWIND $rows AS r
                    MATCH (q:QueryTemplate {id: r.qt_id})
                    SET q.cohere_embedding       = r.emb,
                        q.embedding_model        = r.model,
                        q.embedding_generated_at = $now
                """, emb_rows[i:i + EMBED_BATCH], now=now)
                log.info("EMBED query templates: %d/%d written.",
                         min(i + EMBED_BATCH, len(emb_rows)), len(emb_rows))

        # KNN on column embeddings → SEMANTICALLY_SIMILAR edges
        gds.run_knn(cutoff=0.88)

        log.info("EMBED done in %.1fs", time.time() - t)

    # ── Integrity check + PipelineRun ──────────────────────────────────────

    if not dry_run and loader:
        check = loader.integrity_check()
        log.info("Integrity: %s", check)
        run_meta.update({
            **stats,
            "completed_at": _NOW(),
            "duration_seconds": round(time.time() - _t0, 1),
            **{f"integrity_{k}": v for k, v in check.items()},
        })

    if loader:
        loader.close()
    if gds:
        gds.close()

    log.info("Pipeline complete in %.1fs.", time.time() - _t0)
    return run_meta


# ── Helpers ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Neo4j Semantic Layer Pipeline")
    parser.add_argument(
        "--steps",
        default="all",
        help="Comma-separated steps or 'all'. Options: " + ", ".join(_ALL_STEPS),
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and infer without writing to Neo4j")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Delete enrichment checkpoint and start fresh")
    parser.add_argument("--reset-templates", action="store_true", help="Clear only the templates cache so templates re-enrich with updated prompt")
    parser.add_argument("--reset-glossary", action="store_true", help="Clear only the glossary cache so glossary re-enriches with updated prompt")
    parser.add_argument("--reset-communities", action="store_true", help="Clear only the community descriptions cache so communities re-enrich")
    parser.add_argument(
        "--tables",
        default=None,
        help="Comma-separated FQNs to update (e.g. lpp.ap_invoice,lpp.third_party). "
             "Scopes extract/infer/load/enrich/embed to only these tables.",
    )
    args = parser.parse_args()

    if args.steps.strip().lower() == "all":
        steps = _ALL_STEPS
    else:
        steps = [s.strip() for s in args.steps.split(",")]
        invalid = [s for s in steps if s not in _ALL_STEPS]
        if invalid:
            print(f"Unknown steps: {invalid}. Valid: {_ALL_STEPS}", file=sys.stderr)
            sys.exit(1)

    table_filter = [t.strip() for t in args.tables.split(",")] if args.tables else None

    run(steps=steps, dry_run=args.dry_run, reset_checkpoint=args.reset_checkpoint,
        reset_templates=args.reset_templates, reset_glossary=args.reset_glossary,
        reset_communities=args.reset_communities, table_filter=table_filter)


if __name__ == "__main__":
    main()
