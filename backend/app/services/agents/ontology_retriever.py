"""Semantic ontology retrieval using pgvector.

Replaces the keyword-based ``resolve_term`` lookup with embedding-based search:
  1. Embed the question + intent via Bedrock Cohere
  2. Vector search → top-K classes
  3. 1-hop graph expansion: subclass children + properties whose domain matches
  4. Direct-similarity property fallback

Call ``init_ontology_retriever()`` at startup alongside ``init_data_pool()``.
Use ``retrieve_ontology_context()`` in place of keyword matching.
"""

from __future__ import annotations

import json
import time
from typing import Any

import asyncpg
import boto3

from app.core.config import settings
from app.core.logger import logger

_pool: asyncpg.Pool | None = None
_bedrock: Any = None
_embed_dim: int | None = None


async def init_ontology_retriever() -> None:
    global _pool, _bedrock, _embed_dim
    try:
        dsn = (
            f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
            f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        )
        ssl = settings.DATABASE_SSL_MODE
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            command_timeout=10,
            ssl=(ssl if ssl != "disable" else None),
            statement_cache_size=0,
        )
        _bedrock = boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY.strip("'"),
        )
        row_count = await _pool.fetchval("SELECT COUNT(*) FROM ontology_nodes")
        logger.info(f"Ontology retriever ready — {row_count} nodes indexed in pgvector")
    except Exception as e:
        logger.warning(f"Ontology retriever init failed (falling back to keyword lookup): {e}")
        _pool = None
        _bedrock = None


async def close_ontology_retriever() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def is_retriever_ready() -> bool:
    return _pool is not None and _bedrock is not None


def _embed_query(text: str) -> list[float]:
    arn = settings.AWS_BEDROCK_COHERE_EMBED_V4_ARN
    if not arn:
        raise RuntimeError("AWS_BEDROCK_COHERE_EMBED_V4_ARN not set")
    body = json.dumps({"texts": [text], "input_type": "search_query"})
    resp = _bedrock.invoke_model(
        modelId=arn,
        body=body,
        contentType="application/json",
        accept="*/*",
    )
    result = json.loads(resp["body"].read())
    embs = result["embeddings"]
    return (embs["float"] if isinstance(embs, dict) else embs)[0]


def _local_name(uri: str | None) -> str:
    if not uri:
        return ""
    for sep in ("#", "/"):
        if sep in uri:
            return uri.rsplit(sep, 1)[-1]
    return uri


def _row_to_term(row: asyncpg.Record, term_type: str | None = None) -> dict:
    node_type = row["node_type"]
    domain_uris = row.get("domain_uris") or []
    t = {
        "uri": row["uri"],
        "local": row["local_name"],
        "label": row.get("label") or row["local_name"],
        "comment": row.get("comment") or "",
        "type": term_type or ("class" if node_type == "class" else
                              ("object_property" if row.get("prop_kind") == "object" else "datatype_property")),
        "property_type": _local_name(row.get("range_uri")),
        "domain": _local_name(domain_uris[0]) if domain_uris else "",
        "named_graph": row.get("named_graph"),
    }
    return t


async def retrieve_ontology_context(
    question: str,
    intent: str,
    hint_class_uris: list[str] | None = None,
    k_classes: int = 15,
    k_props: int = 30,
) -> tuple[list[dict], list[str]]:
    """Return (ontology_terms, named_graphs).

    ontology_terms: list of term dicts compatible with _format_ontology_terms()
    named_graphs:   unique named graph strings for the resolved classes

    Falls back to ([], []) if pgvector is not initialised — caller should
    use keyword-based fallback in that case.
    """
    if not is_retriever_ready():
        return [], []

    t0 = time.perf_counter()
    q_text = f"{question} | {intent}"

    try:
        q_embed = _embed_query(q_text)
        q_vec = f"[{','.join(str(x) for x in q_embed)}]"
    except Exception as e:
        logger.warning(f"[ontology_retriever] embedding failed: {e}")
        return [], []

    async with _pool.acquire() as conn:
        classes = await conn.fetch(
            f"""
            SELECT uri, local_name, node_type, prop_kind, label, comment, subclass_of, domain_uris, range_uri, named_graph
            FROM   ontology_nodes
            WHERE  node_type = 'class'
            ORDER  BY embedding <=> '{q_vec}'::vector
            LIMIT  $1
            """,
            k_classes,
        )

        seed_uris = [r["uri"] for r in classes]

        child_classes = await conn.fetch(
            f"""
            SELECT uri, local_name, node_type, prop_kind, label, comment, subclass_of, domain_uris, range_uri, named_graph
            FROM   ontology_nodes
            WHERE  node_type = 'class'
              AND  subclass_of && $1
              AND  uri <> ALL($1)
            ORDER  BY embedding <=> '{q_vec}'::vector
            LIMIT  8
            """,
            seed_uris,
        ) if seed_uris else []

        all_class_uris = list({r["uri"] for r in list(classes) + list(child_classes)})

        domain_props = await conn.fetch(
            f"""
            SELECT uri, local_name, node_type, prop_kind, label, comment, domain_uris, range_uri,
                   1 - (embedding <=> '{q_vec}'::vector) AS similarity
            FROM   ontology_nodes
            WHERE  node_type = 'property'
              AND  domain_uris && $1
            ORDER  BY embedding <=> '{q_vec}'::vector
            LIMIT  $2
            """,
            all_class_uris, k_props,
        ) if all_class_uris else []

        domain_prop_uris = [r["uri"] for r in domain_props]

        extra_props = await conn.fetch(
            f"""
            SELECT uri, local_name, node_type, prop_kind, label, comment, domain_uris, range_uri
            FROM   ontology_nodes
            WHERE  node_type = 'property'
              AND  ($1::text[] IS NULL OR uri <> ALL($1))
            ORDER  BY embedding <=> '{q_vec}'::vector
            LIMIT  10
            """,
            domain_prop_uris or None,
        )

        if hint_class_uris:
            hint_rows = await conn.fetch(
                """
                SELECT uri, local_name, node_type, prop_kind, label, comment, subclass_of, domain_uris, range_uri, named_graph
                FROM   ontology_nodes
                WHERE  uri = ANY($1)
                """,
                hint_class_uris,
            )
        else:
            hint_rows = []

    terms: list[dict] = []
    seen: set[str] = set()

    def _add(row, t=None):
        uri = row["uri"]
        if uri in seen:
            return
        seen.add(uri)
        terms.append(_row_to_term(row, t))

    for row in hint_rows:
        _add(row, "class")
    for row in classes:
        _add(row, "class")
    for row in child_classes:
        _add(row, "class")
    for row in domain_props:
        _add(row)
    for row in extra_props:
        _add(row)

    # Normalize legacy URIs written by old index runs (before DEFAULT_GRAPH was fixed).
    _GRAPH_URI_MAP = {"graph:treasury": "graph:treasury:all"}
    named_graphs = list(dict.fromkeys(
        _GRAPH_URI_MAP.get(r["named_graph"], r["named_graph"])
        for r in list(classes) + list(child_classes) + list(hint_rows)
        if r.get("named_graph")
    ))

    ms = round((time.perf_counter() - t0) * 1000)
    logger.info(f"[ontology_retriever] {len(terms)} terms ({len(classes)} classes + "
                f"{len(domain_props)} domain props + {len(extra_props)} extra) in {ms}ms")
    return terms, named_graphs
