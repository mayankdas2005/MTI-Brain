"""Neo4j driver initialization and low-level execution helpers."""

from __future__ import annotations

import time

from app.core.circuit_breaker import neo4j_breaker
from app.core.config import settings
from app.core.logger import logger
from app.core.retry import retry_sync

_driver = None


def init_neo4j() -> None:
    global _driver
    try:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=settings.NEO4J_MAX_POOL_SIZE,
            connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
            connection_acquisition_timeout=settings.NEO4J_ACQUISITION_TIMEOUT,
            max_connection_lifetime=300,
            keep_alive=True,
            notifications_disabled_categories=frozenset({"UNRECOGNIZED"}),
        )
        _driver.verify_connectivity()
        with _driver.session(database=settings.NEO4J_DB) as _s:
            _s.run("RETURN 1").consume()
        logger.info("Neo4j driver initialized | uri={} | pool={}", settings.NEO4J_URI, settings.NEO4J_MAX_POOL_SIZE)
    except Exception as e:
        logger.error("Neo4j driver initialization failed: {}", e)
        raise


def close_neo4j() -> None:
    global _driver
    if _driver:
        _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


def get_driver():
    if not _driver:
        raise RuntimeError("Neo4j not initialized — call init_neo4j() first.")
    return _driver


def _neo4j_run(cypher: str, params: dict) -> list:
    return retry_sync(
        lambda: _do_neo4j_run(cypher, params),
        max_attempts=3,
        backoff_base=0.5,
        service="neo4j",
    )


def _do_neo4j_run(cypher: str, params: dict) -> list:
    with get_driver().session(database=settings.NEO4J_DB) as session:
        return list(session.run(cypher, params))


def _neo4j_run_single(cypher: str, params: dict):
    return retry_sync(
        lambda: _do_neo4j_run_single(cypher, params),
        max_attempts=3,
        backoff_base=0.5,
        service="neo4j",
    )


def _do_neo4j_run_single(cypher: str, params: dict):
    with get_driver().session(database=settings.NEO4J_DB) as session:
        return session.run(cypher, params).single()


def _neo4j_write(cypher: str, **kwargs) -> None:
    retry_sync(
        lambda: _do_neo4j_write(cypher, **kwargs),
        max_attempts=3,
        backoff_base=0.5,
        service="neo4j",
    )


def _do_neo4j_write(cypher: str, **kwargs) -> None:
    with get_driver().session(database=settings.NEO4J_DB) as session:
        session.run(cypher, **kwargs)


@neo4j_breaker
def get_candidate_col_summary(fqns: list[str]) -> dict[str, dict]:
    """Return measure_cols and date_cols per table.

    Used by anchor_resolver col-gate: measure_cols — does candidate add unique measures?
    date_cols — does the primary anchor already cover dates (blocks redundant snapshot injection)?

    Returns:
        {
            "lpp.cash_flow": {
                "measure_cols": ["signed_amount", "flow_amount"],
                "date_cols":    ["value_date", "transaction_date"],
            },
            "lpp.cash_balance": {
                "measure_cols": ["amount"],
                "date_cols":    ["balance_date"],
            },
            ...
        }
    """
    if not fqns:
        return {}
    cypher = """
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    WHERE t.fqn IN $fqns
      AND (c.semantic_type IN ['measure', 'amount', 'percentage', 'ratio']
           OR c.semantic_type = 'date'
           OR c.data_type IN ['date', 'timestamp with time zone'])
    RETURN t.fqn AS fqn,
           [x IN collect(CASE WHEN c.semantic_type IN ['measure', 'amount', 'percentage', 'ratio']
                              THEN c.name END) WHERE x IS NOT NULL] AS measure_cols,
           [x IN collect(CASE WHEN c.semantic_type = 'date'
                                   OR c.data_type IN ['date', 'timestamp with time zone']
                              THEN c.name END) WHERE x IS NOT NULL] AS date_cols
    """
    t0 = time.monotonic()
    rows = _neo4j_run(cypher, {"fqns": fqns})
    elapsed_ms = (time.monotonic() - t0) * 1000
    result: dict[str, dict] = {}
    for row in rows:
        fqn = row.get("fqn")
        if not fqn:
            continue
        result[fqn] = {
            "measure_cols": list(row.get("measure_cols") or []),
            "date_cols":    list(row.get("date_cols") or []),
        }
    logger.debug(
        "neo4j | get_candidate_col_summary | fqns={} | ms={:.0f} | results={}",
        len(fqns), elapsed_ms, len(result),
    )
    return result
