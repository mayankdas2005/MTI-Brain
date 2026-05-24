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
    value_aliases: Optional[list[str]] = None
    value_scale: Optional[str] = None


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

_TABLE_SEMANTIC_TYPES = (
    "identifier", "measure", "dimension", "date", "flag",
    "amount", "free_text", "code", "percentage", "ratio",
)


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
    term_type: Literal["abbreviation", "entity_alias", "unit", "metric", "product"]
    description: str


class GlossaryOutput(BaseModel):
    terms: list[BusinessTermItem] = Field(default_factory=list)


class QueryTemplateItem(BaseModel):
    source_line: int
    description: str
    intent_scores: dict[str, float] = Field(default_factory=lambda: {"general_analytics": 0.5})
    complexity: Literal["simple", "complex", "advanced"] = "complex"
    anchor_ontology_classes: list[str] = Field(default_factory=list)
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
    "Document tables based purely on the structural evidence provided — do not invent facts."
)

_TABLE_PROMPT_TEMPLATE = """For each table below, return a JSON array with one object per table.

Each object must have these exact keys:
- "fqn": the table fqn as given
- "description": 2-3 sentences — what business entity or event this table represents, its granularity (one row = one what?), and key identifiers
- "business_domain": one of [banking, cash_and_liquidity, forecasting, payments, card_acquiring, working_capital, erp_reconciliation, corporate, debt_and_capital, fx_and_hedging, investments, knowledge_graph, fraud, benchmarking, reference, staging]
- "table_type_override": null OR one of [fact, dimension, bridge, reference, staging] — only set if you're confident the inferred type is wrong; otherwise null
- "table_type_reason": null OR brief reason for override
- "grain": one sentence starting with "One row per " — the business grain of this table
- "grain_confirmed": true if the grain is certain from the evidence; false if inferred
- "synonyms": list of 3-5 business names/aliases that non-technical users would call this table (e.g. for bank_fee: ["bank charges","service fees","bank service charges","fee schedule"])

Column descriptions and semantic types (where available) are pre-enriched — use them to ground your table description in specific business meaning.
Use sample_values, data types, row_count, diststyle/sortkey as additional evidence.

Tables:
{tables_json}"""


def enrich_tables(tables_data: list[dict], chat_client, cache: dict) -> dict:
    """
    Enrich a list of table data dicts using LangChain structured output.
    Returns {fqn: {description, business_domain, table_type_override, ...}}
    """
    to_process = [t for t in tables_data if t["fqn"] not in cache]
    results = dict(cache)
    structured_llm = chat_client.with_structured_output(TablesEnrichmentResponse)

    for i in range(0, len(to_process), _TABLE_BATCH):
        batch = to_process[i: i + _TABLE_BATCH]
        prompt = _TABLE_PROMPT_TEMPLATE.format(
            tables_json=json.dumps(batch, indent=2, default=str)
        )
        try:
            response: TablesEnrichmentResponse = structured_llm.invoke(prompt)
            for tbl in response.tables:
                results[tbl.fqn] = tbl.model_dump()
        except Exception as e:
            log.error("Table batch %d failed: %s", i // _TABLE_BATCH + 1, e)
            for t in batch:
                if t["fqn"] not in results:
                    results[t["fqn"]] = {
                        "fqn": t["fqn"], "description": "", "business_domain": "",
                        "table_type_override": None, "_enrichment_failed": True,
                    }

    return results


# ── Column enrichment ──────────────────────────────────────────────────────────

_COL_PROMPT_TEMPLATE = """You are documenting database columns for a text-to-SQL semantic layer.

Table: {fqn}
Table description: {table_description}

For each column, return a JSON array with one object per column having these exact keys:
- "name": column name as given
- "description": one sentence — what does this column store? Use sample_vals as direct evidence. If sample_vals = ['US','GB','DE'] write "ISO 2-letter country code", not "a column that stores country".
- "semantic_type": one of [identifier, measure, dimension, date, flag, amount, free_text, code, percentage, ratio]
- "synonyms": list of 3-5 business terms a non-technical user would call this column
- "is_pii": true or false
- "pii_type": one of [name, email, phone, address, dob, ssn, null]
- "temporal_grain": one of [day, week, month, quarter, year, timestamp, none] — "none" for non-date columns
- "default_aggregation": one of [SUM, AVG, COUNT, MIN, MAX, NONE] — most natural aggregation for this column; NONE for non-numeric or identifier columns
- "value_aliases": for categorical/code columns with n_distinct <= 20 — list of "user_term -> db_value" strings (e.g. ["Visa -> VI","Mastercard -> MC","Amex -> AX"]); null for all other columns
- "value_scale": for numeric amount/ratio columns — describe the unit and scale (e.g. "USD, stored in full dollars", "basis points (1 bp = 0.01%)", "percentage 0-100 scale"); null for all other columns

Rules for value_aliases: Only populate when semantic_type IN [code, flag, dimension] AND n_distinct <= 20. Use the sample_vals to infer the mapping to common business names. If codes are already self-explanatory, set null.
Rules for value_scale: Only populate when semantic_type IN [amount, measure, ratio, percentage]. Infer from column name (e.g. "_bps" → basis points, "_pct" → percentage, "_usd" → USD). If unclear, set null.

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
    Enrich columns for one table using LangChain structured output.
    Returns {col_name: {description, semantic_type, synonyms, is_pii, pii_type, ...}}
    """
    to_process = [c for c in columns_data if f"{fqn}.{c['name']}" not in col_cache]

    if not to_process:
        cached = {k.split(".")[-1]: v for k, v in col_cache.items() if k.startswith(f"{fqn}.")}
        return cached

    prompt = _COL_PROMPT_TEMPLATE.format(
        fqn=fqn,
        table_description=table_description,
        columns_json=json.dumps(to_process, indent=2, default=str),
    )
    structured_llm = chat_client.with_structured_output(ColumnsEnrichmentResponse)
    try:
        response: ColumnsEnrichmentResponse = structured_llm.invoke(prompt)
        out = {}
        for col in response.columns:
            col_dict = col.model_dump()
            out[col.name] = col_dict
            col_cache[f"{fqn}.{col.name}"] = col_dict
        return out
    except Exception as e:
        log.error("Column enrichment failed for %s: %s", fqn, e)
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

For each community below, provide:
- id: the community id exactly as given
- description: 2-3 sentences — what business area this cluster represents, what data it contains, and what analytical questions it can answer. Ground your description in the actual table descriptions provided.
- query_patterns: list of 3 example question templates that this community's tables can answer (plain English, no SQL)

Communities:
{communities_json}"""


def enrich_community(
    communities: list[dict],
    chat_client,
    cache: dict,
) -> dict:
    """
    Enrich Community nodes with description and query_patterns.
    communities: list of {id, dominant_domain, table_names: [...], table_descriptions: [...]}
    Returns {community_id: {description, query_patterns}}
    """
    to_process = [c for c in communities if str(c["id"]) not in cache]
    results = dict(cache)
    batch_size = 8
    structured = chat_client.with_structured_output(CommunitiesOutput)

    for i in range(0, len(to_process), batch_size):
        batch = to_process[i: i + batch_size]
        prompt = _COMMUNITY_PROMPT_TEMPLATE.format(
            communities_json=json.dumps(batch, indent=2, default=str)
        )
        try:
            output: CommunitiesOutput = structured.invoke(prompt)
            for item in output.communities:
                d = item.model_dump()
                results[str(d["id"])] = d
            log.info("Community enrichment: batch %d/%d done.", i // batch_size + 1,
                     -(-len(to_process) // batch_size))
        except Exception as e:
            log.error("Community enrichment batch %d failed: %s", i // batch_size, e)

    log.info("Community enrichment: %d communities processed.", len(results))
    return results


# ── Intent description enrichment ─────────────────────────────────────────────

_INTENT_PROMPT_TEMPLATE = """You are building a routing layer for a treasury analytics text-to-SQL system.
Non-technical users ask business questions. The system classifies each question into intents to find relevant tables.

Each intent entry contains:
- "classes": ontology class names that belong to this intent
- "tables": list of {{name, description, domain, measures}} — enriched descriptions of the key tables this intent covers

For each intent below, write a 1-2 sentence description covering:
1. What business questions this intent covers (use the table descriptions as evidence)
2. What types of data are queried (entities, metrics, time windows)
3. What makes it distinct from other intents

For each intent provide: the intent name (exactly as given) and its description.

Intents:
{intents_json}"""


def enrich_intents(
    intents: list[dict],
    chat_client,
) -> dict:
    """
    Generate descriptions for Intent nodes.
    intents: list of {intent, classes: [...], class_count: int}
    Returns {intent_name: {description}}
    """
    prompt = _INTENT_PROMPT_TEMPLATE.format(
        intents_json=json.dumps(intents, indent=2, default=str)
    )
    try:
        structured = chat_client.with_structured_output(IntentsOutput)
        output: IntentsOutput = structured.invoke(prompt)
        return {item.intent: item.model_dump() for item in output.intents}
    except Exception as e:
        log.error("Intent enrichment failed: %s", e)
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

For each term, provide: the canonical term, common variants/synonyms, the term type (abbreviation, entity_alias, unit, metric, or product), and a short description of what it means in this treasury context.
Identify as many relevant terms as you can from the context provided. If the context contains no recognisable business terms, return an empty list."""


def generate_business_glossary(
    context_text: str,
    chat_client,
) -> list[dict]:
    """
    Generate BusinessTerm nodes from database context using structured output.
    context_text: concise summary of column synonyms, table descriptions, and sample values
    Returns list of {term, variants, term_type, description}
    """
    prompt = _GLOSSARY_PROMPT_TEMPLATE.format(context_text=context_text)
    try:
        structured = chat_client.with_structured_output(GlossaryOutput)
        result: GlossaryOutput = structured.invoke(prompt)
        terms = [t.model_dump() for t in result.terms]
        log.info("Business glossary: %d terms generated.", len(terms))
        return terms
    except Exception as e:
        log.error("Business glossary generation failed: %s", e)
        return []


# ── QueryTemplate enrichment ───────────────────────────────────────────────────

_QUERY_TEMPLATE_PROMPT = """You are analyzing treasury analytics questions for a text-to-SQL system.
Users are non-technical business people. All queries use CTEs. Do NOT write SQL.

Available intents: {intents_list}

Available ontology classes (camelCase — pick only from this list): {classes_list}

Ontology class descriptions (use these to understand what each class represents):
{class_context}

For each question provided, analyze it and fill in:
- source_line: the integer source_line from the question entry
- description: one sentence describing what data and metrics the answer contains
- intent_scores: a dict mapping intent names to confidence scores (0.0-1.0); at least one intent required
- complexity: one of simple, complex, or advanced
- anchor_ontology_classes: list of ontology class names from the provided list that are relevant; empty list if none match
- cte_steps: list of CTE step descriptions in snake_case — always at least 2 steps, each as "cte_name: what this CTE does"
- required_aggregations: list of plain English descriptions of each aggregation (no SQL)
- required_filters: list of plain English descriptions of each filter condition
- time_windowed: true if the question involves a time window (MTD, YTD, last N days, etc.)

Complexity rules:
- simple: 1-2 CTEs, <= 2 tables, single aggregate or lookup
- complex: 3-5 CTEs, 3-5 tables, multi-aggregate with conditions
- advanced: 5+ CTEs, cross-domain, stress tests, trends, executive dashboards

Questions (with source_line and question_text):
{questions_json}"""


def enrich_query_templates(
    questions: list[dict],
    intents: list[str],
    ontology_classes: list[str],
    chat_client,
    cache: dict,
    batch_size: int = 10,
    class_context: str = "",
) -> dict:
    """
    Enrich QueryTemplate nodes from Questions.txt using structured output.
    questions: list of {source_line: int, question_text: str}
    Returns {source_line: enriched_dict}
    """
    to_process = [q for q in questions if str(q["source_line"]) not in cache]
    results = dict(cache)

    intents_list = ", ".join(intents)
    classes_list = ", ".join(ontology_classes[:150])
    structured = chat_client.with_structured_output(QueryTemplatesOutput)

    for i in range(0, len(to_process), batch_size):
        batch = to_process[i: i + batch_size]
        prompt = _QUERY_TEMPLATE_PROMPT.format(
            intents_list=intents_list,
            classes_list=classes_list,
            class_context=class_context or "(no class descriptions available)",
            questions_json=json.dumps(batch, indent=2),
        )
        try:
            output: QueryTemplatesOutput = structured.invoke(prompt)
            for item in output.templates:
                d = item.model_dump()
                if len(d.get("cte_steps") or []) < 2:
                    d["cte_steps"] = [
                        "base: select relevant rows from anchor tables with applied filters",
                        "final: aggregate or format results for the user",
                    ]
                results[str(d["source_line"])] = d
            done = min(i + batch_size, len(to_process))
            log.info("Templates LLM: %d/%d questions enriched.", done, len(to_process))
        except Exception as e:
            log.error("QueryTemplate batch %d failed: %s", i // batch_size, e)
            for q in batch:
                sl = str(q["source_line"])
                if sl not in results:
                    results[sl] = {
                        "source_line": q["source_line"],
                        "description": q["question_text"],
                        "intent_scores": {"general_analytics": 0.5},
                        "complexity": "complex",
                        "anchor_ontology_classes": [],
                        "cte_steps": [
                            "base: select relevant rows from anchor tables with applied filters",
                            "final: aggregate or format results for the user",
                        ],
                        "required_aggregations": [],
                        "required_filters": [],
                        "time_windowed": False,
                        "_enrichment_failed": True,
                    }

    log.info("QueryTemplate enrichment: %d / %d processed.", len(results), len(questions))
    return results
