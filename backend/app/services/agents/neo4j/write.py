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


_QP_CREATE_CYPHER = """
MERGE (qp:QueryPattern {id: $id})
ON CREATE SET
  qp.question_text    = $question_text,
  qp.sql_text         = $sql_text,
  qp.sql_cte_outline  = $sql_cte_outline,
  qp.join_outline     = $join_outline,
  qp.filter_summary   = $filter_summary,
  qp.measure_summary  = $measure_summary,
  qp.dimension_summary = $dimension_summary,
  qp.directive_summary = $directive_summary,
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
  qp.enabled          = false,
  qp.liked_count      = 0,
  qp.disliked_count   = 0,
  qp.first_seen       = datetime(),
  qp.occurrence_count = 1
ON MATCH SET
  qp.occurrence_count  = coalesce(qp.occurrence_count, 0) + 1,
  qp.last_seen         = datetime(),
  qp.sql_text          = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $sql_text          ELSE qp.sql_text          END,
  qp.sql_cte_outline   = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $sql_cte_outline   ELSE qp.sql_cte_outline   END,
  qp.join_outline      = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $join_outline      ELSE qp.join_outline      END,
  qp.filter_summary    = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $filter_summary    ELSE qp.filter_summary    END,
  qp.measure_summary   = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
      THEN $measure_summary   ELSE qp.measure_summary   END,
  qp.dimension_summary = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
      THEN $dimension_summary ELSE qp.dimension_summary END,
  qp.directive_summary = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
      THEN $directive_summary ELSE qp.directive_summary END,
  qp.tables_used       = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $tables_used       ELSE qp.tables_used       END,
  qp.confidence_score  = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $confidence_score ELSE qp.confidence_score END,
  qp.repair_count      = CASE WHEN $repair_count     < coalesce(qp.repair_count,     9999) THEN $repair_count     ELSE qp.repair_count     END,
  qp.recompile_count   = CASE WHEN $recompile_count  < coalesce(qp.recompile_count,  9999) THEN $recompile_count  ELSE qp.recompile_count  END
"""

_QP_UPDATE_CYPHER = """
MATCH (qp:QueryPattern {id: $id})
SET
  qp.occurrence_count  = coalesce(qp.occurrence_count, 0) + 1,
  qp.last_seen         = datetime(),
  qp.sql_text          = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $sql_text          ELSE qp.sql_text          END,
  qp.sql_cte_outline   = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $sql_cte_outline   ELSE qp.sql_cte_outline   END,
  qp.join_outline      = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $join_outline      ELSE qp.join_outline      END,
  qp.filter_summary    = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $filter_summary    ELSE qp.filter_summary    END,
  qp.measure_summary   = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
      THEN $measure_summary   ELSE qp.measure_summary   END,
  qp.dimension_summary = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
      THEN $dimension_summary ELSE qp.dimension_summary END,
  qp.directive_summary = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
      THEN $directive_summary ELSE qp.directive_summary END,
  qp.tables_used       = CASE
      WHEN ($repair_count + $recompile_count) < coalesce(qp.repair_count + qp.recompile_count, 9999)
        OR $confidence_score > coalesce(qp.confidence_score, 0)
      THEN $tables_used       ELSE qp.tables_used       END,
  qp.confidence_score  = CASE WHEN $confidence_score > coalesce(qp.confidence_score, 0) THEN $confidence_score ELSE qp.confidence_score END,
  qp.repair_count      = CASE WHEN $repair_count     < coalesce(qp.repair_count,     9999) THEN $repair_count     ELSE qp.repair_count     END,
  qp.recompile_count   = CASE WHEN $recompile_count  < coalesce(qp.recompile_count,  9999) THEN $recompile_count  ELSE qp.recompile_count  END
"""

_AP_CYPHER = """
MERGE (ap:AntiPattern {merge_key: $merge_key})
ON CREATE SET
  ap.id               = $id,
  ap.question_text    = $question_text,
  ap.sql_text         = $sql_text,
  ap.error_type       = $error_type,
  ap.error_detail     = $error_detail,
  ap.failing_element  = $failing_element,
  ap.tables_involved  = $tables_involved,
  ap.intent           = $intent,
  ap.complexity       = $complexity,
  ap.cohere_embedding = $cohere_embedding,
  ap.first_seen       = datetime(),
  ap.occurrence_count = 1,
  ap.success_count    = 0,
  ap.enabled          = false
ON MATCH SET
  ap.occurrence_count  = ap.occurrence_count + 1,
  ap.last_seen         = datetime(),
  ap.sql_text          = $sql_text,
  ap.error_detail      = $error_detail,
  ap.failing_element   = CASE WHEN $failing_element <> '' THEN $failing_element ELSE ap.failing_element END
"""


@neo4j_breaker
def write_query_pattern(pattern_data: dict, is_update: bool = False) -> None:
    cypher = _QP_UPDATE_CYPHER if is_update else _QP_CREATE_CYPHER
    _neo4j_write(cypher, **pattern_data)
    logger.debug("neo4j | fn=write_query_pattern | id={} | is_update={}", pattern_data.get("id"), is_update)


@neo4j_breaker
def write_anti_pattern(pattern_data: dict) -> None:
    _neo4j_write(_AP_CYPHER, **pattern_data)
    logger.debug("neo4j | fn=write_anti_pattern | merge_key={}", (pattern_data.get("merge_key") or "")[:8])


@neo4j_breaker
def increment_anti_pattern_success(anti_pattern_ids: list) -> None:
    """Increment success_count on matched anti-patterns when a clean execution ran against the same intent.

    Anti-patterns with success_count >= 3 are suppressed in search_anti_patterns — they've been
    shown to be overcautious for this query intent.
    """
    if not anti_pattern_ids:
        return
    _neo4j_write(
        """
        MATCH (ap:AntiPattern) WHERE ap.id IN $ids
        SET ap.success_count = coalesce(ap.success_count, 0) + 1
        """,
        ids=anti_pattern_ids,
    )
    logger.debug("neo4j | fn=increment_anti_pattern_success | ids={}", len(anti_pattern_ids))


# ── Feedback loops ────────────────────────────────────────────────────────────

@neo4j_breaker
def update_pattern_cross_signals(pattern_id: str, like_delta: int, dislike_delta: int) -> None:
    """Update cross-thread like/dislike counters on a QueryPattern.

    Called after each pipeline run when the matched pattern has similar feedback from
    other threads. These counters adjust tier selection in context/fetcher.py.
    """
    _neo4j_write(
        """
        MATCH (qp:QueryPattern {id: $id})
        SET qp.cross_thread_likes    = coalesce(qp.cross_thread_likes, 0) + $like_delta,
            qp.cross_thread_dislikes = coalesce(qp.cross_thread_dislikes, 0) + $dislike_delta,
            qp.last_cross_signal_at  = datetime()
        """,
        id=pattern_id,
        like_delta=like_delta,
        dislike_delta=dislike_delta,
    )
    logger.debug(
        "neo4j | fn=update_pattern_cross_signals | id={} | +likes={} | +dislikes={}",
        pattern_id[:8], like_delta, dislike_delta,
    )


@neo4j_breaker
def update_pattern_feedback(pattern_id: str, liked: bool) -> None:
    """Increment liked/disliked count on a QueryPattern and update promotion_status."""
    if liked:
        _neo4j_write(
            """
            MATCH (p:QueryPattern {id: $id})
            SET p.liked_count = coalesce(p.liked_count, 0) + 1,
                p.promotion_status = CASE
                    WHEN coalesce(p.liked_count, 0) + 1 >= 2
                         AND coalesce(p.occurrence_count, 0) >= 3
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
            t.sql_pattern                = p.sql_text,
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
