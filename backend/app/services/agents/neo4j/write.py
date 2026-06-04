"""Write functions — persist runtime artifacts back to Neo4j."""

from __future__ import annotations

from app.core.circuit_breaker import neo4j_breaker
from app.core.logger import logger
from .client import _neo4j_write

try:
    from semantic_model_generator.graph.load.neo4j_writer import (
        write_join_path as _wjp,
        write_query_pattern as _wqp,
        write_anti_pattern as _wap,
    )
    _WRITER_AVAILABLE = True
except ImportError:
    _WRITER_AVAILABLE = False


@neo4j_breaker
def write_join_path(path_data: dict) -> None:
    if _WRITER_AVAILABLE:
        _wjp(run_fn=_neo4j_write, path_data=path_data)
    else:
        _neo4j_write("MERGE (jp:JoinPath {id: $id}) SET jp += $props",
                     id=path_data["id"], props=path_data)
    logger.debug("neo4j | fn=write_join_path | id={}", path_data.get("id"))


@neo4j_breaker
def write_query_pattern(pattern_data: dict) -> None:
    if _WRITER_AVAILABLE:
        _wqp(run_fn=_neo4j_write, pattern_data=pattern_data)
    else:
        _neo4j_write("MERGE (qp:QueryPattern {id: $id}) SET qp += $props",
                     id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_query_pattern | id={}", pattern_data.get("id"))


@neo4j_breaker
def write_anti_pattern(pattern_data: dict) -> None:
    if _WRITER_AVAILABLE:
        _wap(run_fn=_neo4j_write, pattern_data=pattern_data)
    else:
        _neo4j_write("MERGE (ap:AntiPattern {id: $id}) SET ap += $props",
                     id=pattern_data["id"], props=pattern_data)
    logger.debug("neo4j | fn=write_anti_pattern | id={}", pattern_data.get("id"))


# ── Feedback loops ────────────────────────────────────────────────────────────

@neo4j_breaker
def update_pattern_feedback(pattern_id: str, liked: bool) -> None:
    """Increment liked/disliked count on a QueryPattern and update promotion_status."""
    if liked:
        _neo4j_write(
            """
            MATCH (p:QueryPattern {id: $id})
            SET p.liked_count = coalesce(p.liked_count, 0) + 1,
                p.promotion_status = CASE
                    WHEN coalesce(p.liked_count, 0) + 1 >= 1
                         AND coalesce(p.confidence_score, 0) >= 80
                         AND coalesce(p.repair_count, 1) = 0
                         AND NOT p.promotion_status IN ['promoted', 'demoted']
                    THEN 'candidate'
                    ELSE coalesce(p.promotion_status, 'active')
                END
            """,
            id=pattern_id,
        )
    else:
        _neo4j_write(
            """
            MATCH (p:QueryPattern {id: $id})
            SET p.disliked_count = coalesce(p.disliked_count, 0) + 1,
                p.promotion_status = CASE
                    WHEN coalesce(p.disliked_count, 0) + 1 >= 2
                    THEN 'demoted'
                    ELSE coalesce(p.promotion_status, 'active')
                END
            """,
            id=pattern_id,
        )
    logger.debug("neo4j | update_pattern_feedback | id={} | liked={}", pattern_id, liked)


@neo4j_breaker
def promote_pattern_to_template(pattern_id: str) -> None:
    """Promote a QueryPattern candidate to a QueryTemplate node.

    Only runs when promotion_status='candidate' — safe to call unconditionally
    after thumbs-up; the WHERE guard makes it a no-op otherwise.
    """
    _neo4j_write(
        """
        MATCH (p:QueryPattern {id: $id})
        WHERE p.promotion_status = 'candidate'
        MERGE (t:QueryTemplate {id: p.id + '_tmpl'})
        ON CREATE SET
            t.question_text              = p.question_text,
            t.sql_pattern                = p.sql_cte_outline,
            t.join_outline               = p.join_outline,
            t.filter_summary             = p.filter_summary,
            t.anchor_table_fqns_resolved = p.tables_used,
            t.primary_intent             = p.intent,
            t.complexity                 = p.complexity,
            t.confidence                 = p.confidence_score,
            t.validation_status          = 'auto_promoted',
            t.cohere_embedding           = p.cohere_embedding,
            t.promoted_at                = datetime()
        SET p.promotion_status = 'promoted'
        """,
        id=pattern_id,
    )
    logger.info("neo4j | promote_pattern_to_template | id={}", pattern_id)


@neo4j_breaker
def write_schema_gap(concept: str, tables_mentioned: list[str], thread_id: str) -> None:
    """Upsert a SchemaGap node — accumulates occurrence counts across runs."""
    _neo4j_write(
        """
        MERGE (sg:SchemaGap {concept: $concept})
        ON CREATE SET
            sg.first_seen       = datetime(),
            sg.occurrence_count = 1,
            sg.example_tables   = $tables,
            sg.last_thread_id   = $thread_id
        ON MATCH SET
            sg.occurrence_count = sg.occurrence_count + 1,
            sg.last_seen        = datetime(),
            sg.last_thread_id   = $thread_id
        """,
        concept=concept,
        tables=tables_mentioned,
        thread_id=thread_id,
    )
    logger.debug("neo4j | write_schema_gap | concept={}", concept)
