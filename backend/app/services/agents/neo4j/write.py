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
