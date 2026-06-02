"""
LLM enrichment via AWS Bedrock (Claude Sonnet).

Generates fresh descriptions for:
  - Table nodes  (description, business_domain, table_type override)
  - Column nodes (description, semantic_type, synonyms, is_pii)
  - Domain nodes (description summarising member tables)

Uses a local JSON checkpoint file so crashes don't lose progress.
Only processes nodes with enrichment_status IN [pending, stale, failed].
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import boto3
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_CHECKPOINT_FILE = Path(__file__).resolve().parents[2] / "graph_enrichment_cache.json"
_TABLE_BATCH = 5  # tables per Bedrock call
_NOW = lambda: datetime.now(timezone.utc).isoformat()


# ── Pydantic models for structured LLM output ──────────────────────────────────

class ColumnEnrichment(BaseModel):
    name: str
    description: str
    semantic_type: Literal[
        "identifier", "measure", "dimension", "date", "flag",
        "amount", "free_text", "code", "percentage", "ratio",
    ]
    synonyms: list[str] = Field(default_factory=list)
    is_pii: bool = False
    pii_type: Optional[Literal["name", "email", "phone", "address", "dob", "ssn"]] = None
    temporal_grain: Literal["day", "week", "month", "quarter", "year", "timestamp", "none"] = "none"
    default_aggregation: Literal["SUM", "AVG", "COUNT", "MIN", "MAX", "NONE"] = "NONE"
    value_aliases: Optional[list[str]] = None  # ["DB_VALUE -> business label", ...], grounded in value_vocabulary


class ColumnsEnrichmentResponse(BaseModel):
    columns: list[ColumnEnrichment] = Field(default_factory=list)


class TableEnrichment(BaseModel):
    fqn: str
    description: str
    business_domain: Literal[
        "banking", "cash_and_liquidity", "forecasting", "payments", "card_acquiring",
        "working_capital", "erp_reconciliation", "corporate", "debt_and_capital",
        "fx_and_hedging", "investments", "knowledge_graph", "fraud", "benchmarking",
        "reference", "staging",
    ]
    table_type_override: Optional[Literal["fact", "dimension", "bridge", "reference", "staging"]] = None
    table_type_reason: Optional[str] = None
    grain: str
    grain_confirmed: bool = True
    synonyms: list[str] = Field(default_factory=list)


class TablesEnrichmentResponse(BaseModel):
    tables: list[TableEnrichment] = Field(default_factory=list)



class CommunityEnrichment(BaseModel):
    id: Any
    description: str
    query_patterns: list[str] = Field(default_factory=list)


class CommunitiesOutput(BaseModel):
    communities: list[CommunityEnrichment] = Field(default_factory=list)


class IntentEnrichment(BaseModel):
    intent: str
    description: str


class IntentsOutput(BaseModel):
    intents: list[IntentEnrichment] = Field(default_factory=list)


class BusinessTermItem(BaseModel):
    term: str
    variants: list[str] = Field(default_factory=list)
    term_type: Literal["abbreviation", "entity_alias", "unit", "metric", "product",
                       "concept", "status", "dimension"]
    term_category: Literal["metric", "dimension", "entity", "concept", "status",
                           "policy_reference", "aggregation_scope", "filter_value"] = "concept"
    description: str
    related_table_names: list[str] = Field(default_factory=list)


class GlossaryOutput(BaseModel):
    terms: list[BusinessTermItem] = Field(default_factory=list)


class QueryTemplateItem(BaseModel):
    source_line: int
    description: str
    intent_scores: dict[str, float] = Field(default_factory=lambda: {"general_analytics": 0.5})
    complexity: Literal["simple", "complex", "advanced"] = "complex"
    sql_pattern: Literal[
        "single_table", "multi_join", "time_series",
        "cross_domain", "time_series_seasonality"
    ] = "multi_join"
    is_cross_domain: bool = False
    min_cte_count: int = 1
    max_cte_count: int = 5
    anchor_table_names: list[str] = Field(default_factory=list)
    cte_steps: list[str] = Field(default_factory=list)
    required_aggregations: list[str] = Field(default_factory=list)
    required_filters: list[str] = Field(default_factory=list)
    time_windowed: bool = False


class QueryTemplatesOutput(BaseModel):
    templates: list[QueryTemplateItem] = Field(default_factory=list)


# ── Bedrock clients ────────────────────────────────────────────────────────────

def _bedrock_client(cfg):
    from botocore.config import Config as _BotoCfg
    return boto3.client(
        "bedrock-runtime",
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        config=_BotoCfg(read_timeout=120, connect_timeout=15),
    )


def _langchain_bedrock(cfg):
    """LangChain ChatBedrock for structured output (tool_use). Guarantees complete valid JSON."""
    from langchain_aws import ChatBedrock
    return ChatBedrock(
        model_id=cfg.aws_bedrock_sonnet_arn,
        provider="anthropic",
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        model_kwargs={"max_tokens": 4096},
        max_retries=3,
    )


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(min=2, max=30),
    retry=retry_if_exception_type(Exception),
)
def _invoke(client, model_arn: str, messages: list[dict], max_tokens: int = 2048) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    resp = client.invoke_model(
        modelId=model_arn,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    content = json.loads(resp["body"].read())
    return content["content"][0]["text"]


# ── Checkpoint helpers ─────────────────────────────────────────────────────────

def _load_checkpoint() -> dict:
    if _CHECKPOINT_FILE.exists():
        with open(_CHECKPOINT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_checkpoint(cache: dict):
    with open(_CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# ── Table enrichment ───────────────────────────────────────────────────────────

_TABLE_SYSTEM = (
    "You are a senior data engineer documenting a treasury and payments data warehouse "
    "for a large enterprise. You have deep knowledge of financial data: bank accounts, "
    "cash positions, FX, payments, card acquiring, debt instruments, and ERP integration. "
    "Document tables based purely on the structural evidence provided — do not invent facts. "
    "IMPORTANT: Do not name specific database columns, foreign key columns, primary keys, "
    "join predicates, or SQL syntax in your response. Describe only the business meaning and purpose."
)

_VALID_DOMAINS = {
    "banking", "cash_and_liquidity", "forecasting", "payments", "card_acquiring",
    "working_capital", "erp_reconciliation", "corporate", "debt_and_capital",
    "fx_and_hedging", "investments", "knowledge_graph", "fraud", "benchmarking",
    "reference", "staging",
}
_VALID_TABLE_TYPES = {"fact", "dimension", "bridge", "reference", "staging"}

_TABLE_PROMPT_TEMPLATE = """For each table below, return a JSON array with one object per table.

Each object must have these exact keys:
- "fqn": the table fqn as given
- "description": 2-3 sentences — what business entity or event this table represents, its granularity (one row = one what?), and key identifiers
- "business_domain": one of [banking, cash_and_liquidity, forecasting, payments, card_acquiring, working_capital, erp_reconciliation, corporate, debt_and_capital, fx_and_hedging, investments, knowledge_graph, fraud, benchmarking, reference, staging]
- "table_type_override": null OR one of [fact, dimension, bridge, reference, staging] — only set if you're confident the inferred type is wrong; otherwise null
- "grain": one sentence starting with "One row per " — the business grain of this table
- "synonyms": list of 3-5 business names/aliases that non-technical users would call this table

Column descriptions and semantic types (where available) are pre-enriched — use them to ground your table description in specific business meaning.
Use sample_values, data types, row_count as additional evidence.

Return ONLY a valid JSON array, no other text:
[{{"fqn": "...", "description": "...", "business_domain": "...", "table_type_override": null, "grain": "...", "synonyms": []}}, ...]

Tables:
{tables_json}"""


def enrich_tables(tables_data: list[dict], chat_client, cache: dict) -> dict:
    """
    Enrich a list of table data dicts via direct JSON invocation.
    Returns {fqn: {description, business_domain, table_type_override, ...}}
    """
    from json_repair import loads as _json_repair_loads

    to_process = [t for t in tables_data if t["fqn"] not in cache]
    results = dict(cache)

    for i in range(0, len(to_process), _TABLE_BATCH):
        batch = to_process[i: i + _TABLE_BATCH]
        prompt = _TABLE_PROMPT_TEMPLATE.format(
            tables_json=json.dumps(batch, indent=2, default=str)
        )
        try:
            response = chat_client.invoke(prompt)
            raw_text = response.content if hasattr(response, "content") else str(response)
            parsed = _json_repair_loads(raw_text)
            # Accept both {"tables": [...]} and bare [...]
            items = parsed if isinstance(parsed, list) else (parsed.get("tables") or [])
            for tbl in items:
                fqn = tbl.get("fqn", "")
                if not fqn:
                    continue
                domain = tbl.get("business_domain", "")
                if domain not in _VALID_DOMAINS:
                    domain = "reference"
                ttype = tbl.get("table_type_override")
                if ttype not in _VALID_TABLE_TYPES:
                    ttype = None
                results[fqn] = {
                    "fqn": fqn,
                    "description": tbl.get("description", ""),
                    "business_domain": domain,
                    "table_type_override": ttype,
                    "grain": tbl.get("grain", ""),
                    "synonyms": tbl.get("synonyms") or [],
                }
            log.info("Table enrichment: batch %d/%d done (%d/%d tables).",
                     i // _TABLE_BATCH + 1, -(-len(to_process) // _TABLE_BATCH),
                     min(i + _TABLE_BATCH, len(to_process)), len(to_process))
        except Exception as e:
            log.error("Table batch %d failed: %s", i // _TABLE_BATCH + 1, e, exc_info=True)
            for t in batch:
                if t["fqn"] not in results:
                    results[t["fqn"]] = {
                        "fqn": t["fqn"], "description": "", "business_domain": "",
                        "table_type_override": None, "_enrichment_failed": True,
                    }

    return results


# ── Column enrichment ──────────────────────────────────────────────────────────

_VALID_SEMANTIC_TYPES = {
    "identifier", "measure", "dimension", "date", "flag",
    "amount", "free_text", "code", "percentage", "ratio",
}
_VALID_TEMPORAL_GRAINS = {"day", "week", "month", "quarter", "year", "timestamp", "none"}
_VALID_AGGREGATIONS = {"SUM", "AVG", "COUNT", "MIN", "MAX", "NONE"}

_COL_PROMPT_TEMPLATE = """You are documenting database columns for a text-to-SQL semantic layer.

IMPORTANT: Do not name foreign key targets, join predicates, or SQL column references. Describe only the business meaning of each column in isolation.

Table: {fqn}
Table description: {table_description}

Return ONLY a valid JSON array, no other text. One object per column:
[{{
  "name": "<column name as given>",
  "description": "<one sentence using sample_vals as evidence>",
  "semantic_type": "<identifier|measure|dimension|date|flag|amount|free_text|code|percentage|ratio>",
  "synonyms": ["<business term>", ...],
  "is_pii": false,
  "pii_type": null,
  "temporal_grain": "<day|week|month|quarter|year|timestamp|none>",
  "default_aggregation": "<SUM|AVG|COUNT|MIN|MAX|NONE>",
  "value_aliases": ["DB_VALUE -> business label", ...] or null
}}]

Rules for value_aliases:
  - Only generate when semantic_type IN [code, flag, dimension] AND sample_vals/value_vocabulary is non-empty
  - Map ONLY values that appear in sample_vals or value_vocabulary — never invent
  - If values are already self-explanatory English words, return null

Columns:
{columns_json}"""


def enrich_columns(
    fqn: str,
    table_description: str,
    columns_data: list[dict],
    chat_client,
    col_cache: dict,
) -> dict:
    """
    Enrich columns for one table via direct JSON invocation.
    Returns {col_name: {description, semantic_type, synonyms, is_pii, pii_type, ...}}
    """
    from json_repair import loads as _json_repair_loads

    to_process = [c for c in columns_data if f"{fqn}.{c['name']}" not in col_cache]

    if not to_process:
        cached = {k.split(".")[-1]: v for k, v in col_cache.items() if k.startswith(f"{fqn}.")}
        return cached

    prompt = _COL_PROMPT_TEMPLATE.format(
        fqn=fqn,
        table_description=table_description,
        columns_json=json.dumps(to_process, indent=2, default=str),
    )
    try:
        response = chat_client.invoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        parsed = _json_repair_loads(raw_text)
        items = parsed if isinstance(parsed, list) else (parsed.get("columns") or [])
        out = {}
        for col in items:
            name = col.get("name", "")
            if not name:
                continue
            sem = col.get("semantic_type", "dimension")
            if sem not in _VALID_SEMANTIC_TYPES:
                sem = "dimension"
            grain = col.get("temporal_grain", "none")
            if grain not in _VALID_TEMPORAL_GRAINS:
                grain = "none"
            agg = col.get("default_aggregation", "NONE")
            if agg not in _VALID_AGGREGATIONS:
                agg = "NONE"
            col_dict = {
                "name": name,
                "description": col.get("description", ""),
                "semantic_type": sem,
                "synonyms": col.get("synonyms") or [],
                "is_pii": bool(col.get("is_pii", False)),
                "pii_type": col.get("pii_type") or None,
                "temporal_grain": grain,
                "default_aggregation": agg,
                "value_aliases": col.get("value_aliases") or [],
            }
            out[name] = col_dict
            col_cache[f"{fqn}.{name}"] = col_dict
        return out
    except Exception as e:
        log.error("Column enrichment failed for %s: %s", fqn, e, exc_info=True)
        return {}


# ── Domain description ─────────────────────────────────────────────────────────

_DOMAIN_PROMPT_TEMPLATE = """You are documenting a business data domain for an enterprise treasury system.

Domain name: {domain}

Member tables (table name → description):
{tables_text}

Write a 2-3 sentence description of this business domain covering:
1. What business function or area it represents
2. What data it holds (key entities/events)
3. What business questions it can answer

Return ONLY the description text, no JSON, no headers."""


def enrich_domain(
    domain_name: str,
    member_table_descriptions: list[tuple[str, str]],
    client,
    model_arn: str,
) -> str:
    tables_text = "\n".join(
        f"- {name}: {desc}" for name, desc in member_table_descriptions[:20]
    )
    prompt = _DOMAIN_PROMPT_TEMPLATE.format(
        domain=domain_name, tables_text=tables_text
    )
    try:
        return _invoke(client, model_arn, [{"role": "user", "content": prompt}], max_tokens=256)
    except Exception as e:
        log.error("Domain enrichment failed for %s: %s", domain_name, e)
        return ""


def build_table_llm_input(
    fqn: str,
    ontology_class: str,
    table_type: str,
    type_confidence: float,
    row_count: int,
    size_mb: float,
    diststyle: str,
    distkey_col: str,
    sortkey1: str,
    columns: list[dict],
    enriched_columns: dict | None = None,
) -> dict:
    """Build the dict passed to the table enrichment LLM.

    enriched_columns: optional {col_name: {description, semantic_type}} from Phase 1 column enrichment.
    When provided, each column summary is augmented with pre-enriched description and semantic_type.
    """
    col_summaries = []
    for c in columns:
        top_freq = c.get("top_freq_values") or []
        sample = c.get("sample_values") or c.get("most_common_vals") or []
        if top_freq:
            display_vals = [v.split(":")[0] for v in top_freq[:5]]
        else:
            display_vals = [str(v) for v in sample[:6]]
        summary: dict = {
            "name": c["name"],
            "data_type": c["data_type"],
            "is_pk": c.get("is_pk", False),
            "is_notnull": c.get("is_notnull", False),
            "null_frac": round(float(c.get("null_frac") or 0), 3),
            "n_distinct": round(float(c.get("n_distinct") or 0), 3),
            "sample_vals": display_vals,
        }
        if enriched_columns and c["name"] in enriched_columns:
            ec = enriched_columns[c["name"]]
            if ec.get("description"):
                summary["description"] = ec["description"]
            if ec.get("semantic_type"):
                summary["semantic_type"] = ec["semantic_type"]
        col_summaries.append(summary)

    return {
        "fqn": fqn,
        "ontology_class": ontology_class,
        "table_type_inferred": table_type,
        "type_confidence": round(type_confidence, 2),
        "row_count": row_count,
        "size_mb": round(size_mb, 1),
        "diststyle": diststyle,
        "distkey_col": distkey_col,
        "sortkey1": sortkey1,
        "columns": col_summaries,
    }


# ── Community enrichment ───────────────────────────────────────────────────────

_COMMUNITY_PROMPT_TEMPLATE = """You are documenting business communities in a treasury and payments data warehouse.
Each community is a cluster of tables that are frequently joined together.

Each community entry contains:
- "tables": list of {{name, description, domain}} — member tables with their enriched descriptions
- "frequent_joins": list of table names that are commonly joined within this community
- "dominant_domain": the primary business domain for this community

For each community below, provide a description of 2-3 sentences covering what business area this cluster represents, what data it contains, and what analytical questions it can answer. Ground your description in the actual table descriptions provided.

Return ONLY a valid JSON object, no other text:
{{"communities": [
  {{"id": <int>, "description": "<string>"}}
]}}

Communities:
{communities_json}"""


def enrich_community(
    communities: list[dict],
    chat_client,
    cache: dict,
    checkpoint_fn=None,
) -> dict:
    """
    Enrich Community nodes with descriptions via direct JSON invocation.
    communities: list of {id, dominant_domain, tables, frequent_joins}
    checkpoint_fn: optional callable(results) called after each batch for incremental saves.
    Returns {community_id: {description}}
    """
    from json_repair import loads as _json_repair_loads

    to_process = [c for c in communities if str(c["id"]) not in cache]
    results = dict(cache)
    batch_size = 8

    for i in range(0, len(to_process), batch_size):
        batch = to_process[i: i + batch_size]
        prompt = _COMMUNITY_PROMPT_TEMPLATE.format(
            communities_json=json.dumps(batch, indent=2, default=str)
        )
        try:
            response = chat_client.invoke(prompt)
            raw_text = response.content if hasattr(response, "content") else str(response)
            data = _json_repair_loads(raw_text)
            items = data.get("communities") or [] if isinstance(data, dict) else []
            for item in items:
                cid = str(item.get("id", ""))
                if cid:
                    results[cid] = {"id": item["id"], "description": item.get("description", "")}
            log.info("Community enrichment: batch %d/%d done (%d items).",
                     i // batch_size + 1, -(-len(to_process) // batch_size), len(items))
        except Exception as e:
            log.error("Community enrichment batch %d failed: %s", i // batch_size, e, exc_info=True)
        if checkpoint_fn:
            checkpoint_fn(results)

    log.info("Community enrichment: %d communities processed.", len(results))
    return results


# ── Intent description enrichment ─────────────────────────────────────────────

_INTENT_PROMPT_TEMPLATE = """You are building a routing layer for a treasury analytics text-to-SQL system.
Non-technical users ask business questions. The system classifies each question into intents to find relevant tables.

Each intent entry contains:
- "classes": ontology class names that belong to this intent
- "tables": list of {{name, description, domain, measures}} — enriched descriptions of the key tables this intent covers

For each intent below, write a 1-2 sentence description covering what business questions it covers and what makes it distinct.

Return ONLY a valid JSON object, no other text:
{{"intents": [
  {{"intent": "<intent_name_exactly_as_given>", "description": "<string>"}}
]}}

Intents:
{intents_json}"""


def enrich_intents(
    intents: list[dict],
    chat_client,
) -> dict:
    """
    Generate descriptions for Intent nodes via direct JSON invocation.
    intents: list of {intent, classes: [...], class_count: int}
    Returns {intent_name: {description}}
    """
    from json_repair import loads as _json_repair_loads

    prompt = _INTENT_PROMPT_TEMPLATE.format(
        intents_json=json.dumps(intents, indent=2, default=str)
    )
    try:
        response = chat_client.invoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        data = _json_repair_loads(raw_text)
        items = data.get("intents") or [] if isinstance(data, dict) else []
        return {item["intent"]: item for item in items if item.get("intent")}
    except Exception as e:
        log.error("Intent enrichment failed: %s", e, exc_info=True)
        return {}


# ── Business Glossary generation ───────────────────────────────────────────────

_GLOSSARY_PROMPT_TEMPLATE = """You are building a business glossary for a treasury management text-to-SQL system.
Non-technical users ask questions using business terms, abbreviations, and synonyms that may not match the database schema.

Context — column synonyms, table descriptions, and sample domain values from the database:
{context_text}

Identify business terms from the context above. Focus on:
1. Financial abbreviations: DPO, DSO, DIO, MTD, YTD, QTD, ACH, RTP, FedNow, STP, SLA, MTM, OCI, WC, EBITDA, WACC, LTV, ARR
2. Entity aliases: terms users say vs what the database calls them (e.g. customer vs member, vendor vs supplier/payee, bank vs lender/counterparty)
3. Product/value synonyms: what users call card networks, payment methods vs DB codes (e.g. Visa vs VI, Wire vs wires)
4. Unit terms: how users express amounts, rates, percentages (million, billion, basis points, bps, %)
5. Treasury-specific metrics: hedge ratio, blended rate, Days Sales Outstanding, etc.

Return ONLY a valid JSON object, no other text:
{{
  "terms": [
    {{
      "term": "canonical term name",
      "variants": ["synonym1", "synonym2"],
      "term_type": "abbreviation|entity_alias|unit|metric|product|concept|status|dimension",
      "term_category": "metric|dimension|entity|concept|status|policy_reference|aggregation_scope|filter_value",
      "description": "short description in treasury context",
      "related_table_names": ["table_name"]
    }}
  ]
}}

Extract as many relevant terms as you can. If the context contains no recognisable business terms, return {{"terms": []}}."""


def generate_business_glossary(
    context_text: str,
    chat_client,
) -> list[dict]:
    """
    Generate BusinessTerm nodes from database context via direct JSON invocation.
    Returns list of {term, variants, term_type, term_category, description, related_table_names}
    """
    prompt = _GLOSSARY_PROMPT_TEMPLATE.format(context_text=context_text)
    try:
        response = chat_client.invoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        log.debug("GLOSSARY raw text (first 300): %r", raw_text[:300])

        from json_repair import loads as _json_repair_loads
        data = _json_repair_loads(raw_text)
        raw_terms = data.get("terms") or [] if isinstance(data, dict) else []

        terms = []
        valid_term_types = {"abbreviation", "entity_alias", "unit", "metric", "product",
                            "concept", "status", "dimension"}
        valid_categories = {"metric", "dimension", "entity", "concept", "status",
                            "policy_reference", "aggregation_scope", "filter_value"}
        for t in raw_terms:
            if not t.get("term") or not t.get("description"):
                continue
            t["term_type"] = t.get("term_type", "concept") if t.get("term_type") in valid_term_types else "concept"
            t["term_category"] = t.get("term_category", "concept") if t.get("term_category") in valid_categories else "concept"
            t.setdefault("variants", [])
            t.setdefault("related_table_names", [])
            terms.append(t)

        log.info("Business glossary: %d terms generated. First: %s",
                 len(terms), terms[0].get("term") if terms else "—")
        if not terms:
            log.warning("GLOSSARY returned 0 terms. Context sample:\n%s",
                        "\n".join(context_text.splitlines()[:2]))
        return terms
    except Exception as e:
        log.error("Business glossary generation failed: %s", e, exc_info=True)
        return []


# ── QueryTemplate enrichment ───────────────────────────────────────────────────

_QUERY_TEMPLATE_PROMPT = """You are analyzing treasury analytics questions for a text-to-SQL system.
Users are non-technical business people. All queries use CTEs. Do NOT write SQL.

Available intents: {intents_list}

Available tables (name: description [domain]):
{tables_context}

For each question provided, analyze it and fill in:
- source_line: the integer source_line from the question entry
- description: one sentence describing what data and metrics the answer contains (no table or column names)
- intent_scores: a dict mapping intent names to confidence scores (0.0-1.0); at least one intent required
- complexity: one of simple, complex, or advanced
- sql_pattern: one of [single_table, multi_join, time_series, cross_domain, time_series_seasonality]
  * single_table: data from one table with filters
  * multi_join: 2-4 tables joined via foreign keys, same business domain
  * time_series: temporal trend or comparison over time periods
  * cross_domain: question spans multiple business domains (liquidity + debt + FX, etc.)
  * time_series_seasonality: forecast with prior-year comparison baseline
- is_cross_domain: true if the question requires data from 2+ distinct business domains (e.g. cash + debt + FX combined)
- min_cte_count: minimum number of CTEs expected (integer)
- max_cte_count: maximum number of CTEs expected (integer)
- anchor_table_names: list of 2-6 table names from the available tables list above that this question primarily requires. Use exact names only. No invented names.
- cte_steps: list of business-level step descriptions — always at least min_cte_count steps, each as "step_name: what business operation this step performs" — NO column names or table names
- required_aggregations: list of plain English descriptions of each aggregation (e.g. "total cash balance by currency") — no SQL
- required_filters: list of plain English descriptions of each filter condition (e.g. "within a date range") — no SQL
- time_windowed: true if the question involves a time window (MTD, YTD, last N days, etc.)

Complexity rules:
- simple: 1-2 CTEs, single aggregate or lookup
- complex: 3-5 CTEs, multi-aggregate with conditions
- advanced: 5+ CTEs, cross-domain, stress tests, trends, executive dashboards

Questions (with source_line and question_text):
{questions_json}

Return ONLY a valid JSON object, no other text:
{{"templates": [
  {{
    "source_line": <int>,
    "description": "<string>",
    "intent_scores": {{"intent_name": <float>, ...}},
    "complexity": "simple|complex|advanced",
    "sql_pattern": "single_table|multi_join|time_series|cross_domain|time_series_seasonality",
    "is_cross_domain": <bool>,
    "min_cte_count": <int>,
    "max_cte_count": <int>,
    "anchor_table_names": ["table_name", ...],
    "cte_steps": ["step: description", ...],
    "required_aggregations": ["..."],
    "required_filters": ["..."],
    "time_windowed": <bool>
  }}
]}}"""


def enrich_query_templates(
    questions: list[dict],
    intents: list[str],
    tables: list[dict],
    chat_client,
    cache: dict,
    batch_size: int = 10,
) -> dict:
    """
    Enrich QueryTemplate nodes from Questions.txt via direct JSON invocation.
    questions: list of {source_line: int, question_text: str}
    tables: list of {name, description, domain} — passed to LLM for anchor table selection
    Returns {source_line: enriched_dict}
    """
    from json_repair import loads as _json_repair_loads

    to_process = [q for q in questions if str(q["source_line"]) not in cache]
    results = dict(cache)

    intents_list = ", ".join(intents)
    tables_context = "\n".join(
        f"{t['name']}: {(t.get('description') or '').split('.')[0][:80]} [{t.get('domain') or ''}]"
        for t in tables
    )

    valid_patterns = {"single_table", "multi_join", "time_series", "cross_domain", "time_series_seasonality"}
    valid_complexity = {"simple", "complex", "advanced"}

    for i in range(0, len(to_process), batch_size):
        batch = to_process[i: i + batch_size]
        prompt = _QUERY_TEMPLATE_PROMPT.format(
            intents_list=intents_list,
            tables_context=tables_context,
            questions_json=json.dumps(batch, indent=2),
        )
        try:
            response = chat_client.invoke(prompt)
            raw_text = response.content if hasattr(response, "content") else str(response)
            data = _json_repair_loads(raw_text)
            templates = data.get("templates") or [] if isinstance(data, dict) else []

            if not templates:
                log.warning("TEMPLATES batch %d returned 0 templates.", i // batch_size)

            for d in templates:
                sl = str(d.get("source_line", ""))
                if not sl:
                    continue
                if d.get("sql_pattern") not in valid_patterns:
                    d["sql_pattern"] = "multi_join"
                if d.get("complexity") not in valid_complexity:
                    d["complexity"] = "complex"
                if len(d.get("cte_steps") or []) < 2:
                    d["cte_steps"] = [
                        "filter: narrow down records to the relevant scope",
                        "aggregate: compute the required business metrics",
                    ]
                d.setdefault("intent_scores", {"general_analytics": 0.5})
                d.setdefault("anchor_table_names", [])
                d.setdefault("required_aggregations", [])
                d.setdefault("required_filters", [])
                d.setdefault("time_windowed", False)
                d.setdefault("is_cross_domain", False)
                d.setdefault("min_cte_count", 1)
                d.setdefault("max_cte_count", 5)
                log.debug("TEMPLATES item: source_line=%s anchor_table_names=%s", sl, d.get("anchor_table_names"))
                results[sl] = d

            done = min(i + batch_size, len(to_process))
            log.info("Templates LLM: %d/%d questions enriched, %d in this batch.",
                     done, len(to_process), len(templates))
        except Exception as e:
            log.error("QueryTemplate batch %d failed: %s", i // batch_size, e, exc_info=True)
            for q in batch:
                sl = str(q["source_line"])
                if sl not in results:
                    results[sl] = {
                        "source_line": q["source_line"],
                        "description": q["question_text"],
                        "intent_scores": {"general_analytics": 0.5},
                        "complexity": "complex",
                        "sql_pattern": "multi_join",
                        "anchor_table_names": [],
                        "cte_steps": [
                            "filter: narrow down records to the relevant scope",
                            "aggregate: compute the required business metrics",
                        ],
                        "required_aggregations": [],
                        "required_filters": [],
                        "time_windowed": False,
                        "is_cross_domain": False,
                        "min_cte_count": 1,
                        "max_cte_count": 5,
                        "_enrichment_failed": True,
                    }

    log.info("QueryTemplate enrichment: %d / %d processed.", len(results), len(questions))
    return results
