"""
Cohere Embed v4 embeddings via AWS Bedrock.

Column embeddings: "{name} {description} {synonyms_text} {top_values_text}"  → 1536-dim
Table embeddings:  "{name} {domain} {description} synonyms: {synonyms_text} columns: {top_col_names}" → 1536-dim

Batches 96 items per API call (Cohere batch limit).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)
_BATCH = 96


def _bedrock_client(cfg):
    return boto3.client(
        "bedrock-runtime",
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
    )


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
def _embed_batch(client, model_arn: str, texts: list[str], input_type: str) -> list[list[float]]:
    body = {
        "texts": texts,
        "input_type": input_type,
        "embedding_types": ["float"],
    }
    resp = client.invoke_model(
        modelId=model_arn,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    data = json.loads(resp["body"].read())
    return data["embeddings"]["float"]


def embed_texts(
    texts: list[str],
    client,
    model_arn: str,
    input_type: str = "search_document",
) -> list[list[float]]:
    """Embed a list of texts in batches of 96. Returns list of embedding vectors."""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        batch = texts[i : i + _BATCH]
        try:
            embs = _embed_batch(client, model_arn, batch, input_type)
            all_embeddings.extend(embs)
            log.info("Embedded batch %d/%d (%d items)", i // _BATCH + 1,
                     (len(texts) - 1) // _BATCH + 1, len(batch))
        except Exception as e:
            log.error("Embedding batch %d failed: %s — using zero vectors", i // _BATCH, e)
            all_embeddings.extend([[0.0] * 1536] * len(batch))
    return all_embeddings


def build_column_text(
    name: str,
    description: str,
    synonyms: list[str],
    synonyms_text: str = "",
    top_values_text: str = "",
    value_vocabulary: list[str] | None = None,
) -> str:
    parts = [name]
    if description:
        parts.append(description)
    syns = synonyms_text.strip() or " ".join(synonyms)
    if syns:
        parts.append(syns)
    # Include value vocabulary for categorical columns (improves value-level vector search)
    if value_vocabulary:
        parts.append(" ".join(value_vocabulary[:20]))
    return " ".join(parts).strip()


def build_table_text(
    name: str,
    description: str,
    business_domain: str,
    top_col_names: list[str],
    synonyms_text: str = "",
) -> str:
    parts = [name]
    if business_domain:
        parts.append(business_domain)
    if description:
        parts.append(description)
    if synonyms_text:
        parts.append("synonyms: " + synonyms_text.strip())
    if top_col_names:
        parts.append("columns: " + " ".join(top_col_names[:8]))
    return " ".join(parts).strip()


def embed_columns(
    column_records: list[dict],
    client,
    model_arn: str,
) -> list[dict]:
    """
    column_records: list of {id, name, description, synonyms, value_vocabulary}
    Returns list of {id, embedding}
    """
    texts = [
        build_column_text(
            r["name"],
            r.get("description", ""),
            r.get("synonyms") or [],
            value_vocabulary=r.get("value_vocabulary") or [],
        )
        for r in column_records
    ]
    embeddings = embed_texts(texts, client, model_arn, input_type="search_document")
    return [
        {"id": r["id"], "embedding": emb}
        for r, emb in zip(column_records, embeddings)
    ]


def embed_tables(
    table_records: list[dict],
    client,
    model_arn: str,
) -> list[dict]:
    """
    table_records: list of {fqn, name, description, business_domain, top_col_names, synonyms_text}
    Returns list of {fqn, embedding}
    """
    texts = [
        build_table_text(
            r["name"],
            r.get("description", ""),
            r.get("business_domain", ""),
            r.get("top_col_names") or [],
            r.get("synonyms_text") or "",
        )
        for r in table_records
    ]
    embeddings = embed_texts(texts, client, model_arn, input_type="search_document")
    return [
        {"fqn": r["fqn"], "embedding": emb}
        for r, emb in zip(table_records, embeddings)
    ]


def embed_query(text: str, client, model_arn: str) -> list[float]:
    """Embed a single query string for vector search at inference time."""
    results = embed_texts([text], client, model_arn, input_type="search_query")
    return results[0] if results else [0.0] * 1536


def embed_intents(intent_records: list[dict], client, model_arn: str) -> list[dict]:
    """intent_records: [{name, description}]. Returns [{name, embedding}]."""
    texts = [f"{r['name']} {r.get('description', '')}".strip() for r in intent_records]
    embeddings = embed_texts(texts, client, model_arn, input_type="search_document")
    return [{"name": r["name"], "embedding": emb} for r, emb in zip(intent_records, embeddings)]


def embed_communities(community_records: list[dict], client, model_arn: str) -> list[dict]:
    """community_records: [{id, dominant_domain, description, query_patterns}]. Returns [{id, embedding}]."""
    texts = [
        " ".join(filter(None, [
            r.get("dominant_domain", ""),
            r.get("description", ""),
            " ".join(r.get("query_patterns") or [])
        ])).strip()
        for r in community_records
    ]
    embeddings = embed_texts(texts, client, model_arn, input_type="search_document")
    return [{"id": r["id"], "embedding": emb} for r, emb in zip(community_records, embeddings)]


def embed_domains(domain_records: list[dict], client, model_arn: str) -> list[dict]:
    """domain_records: [{name, description}]. Returns [{name, embedding}]."""
    texts = [f"{r['name']} {r.get('description', '')}".strip() for r in domain_records]
    embeddings = embed_texts(texts, client, model_arn, input_type="search_document")
    return [{"name": r["name"], "embedding": emb} for r, emb in zip(domain_records, embeddings)]
