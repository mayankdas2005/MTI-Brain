"""Token usage extraction and cost calculation for LLM responses.

Reads usage_metadata / response_metadata from LangChain AIMessage objects
emitted by on_chat_model_end events in the astream_events loop.
"""
from __future__ import annotations

from app.services.agents.node_names import N

# All model definitions in one place.
# Keys are matched as substrings of the model name (lowercase, longest first) for
# local cost calculation.  Entries that also have ``lf_name`` + ``lf_pattern``
# are registered in Langfuse at startup so costs appear on traces.
# Ordered most-specific first (required for correct Langfuse first-match).
# Prices per 1M tokens in USD (Anthropic on-demand, US West).
MODELS: dict[str, dict] = {
    # ── Claude 4.x family ─────────────────────────────────────────────────────
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25,  "cache_read": 0.50,
                          "lf_name": "bedrock-claude-opus-4-7",   "lf_pattern": r"(?i)claude.*opus.*4.*7|4.*7.*opus"},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30,
                          "lf_name": "bedrock-claude-sonnet-4-6", "lf_pattern": r"(?i)claude.*sonnet.*4.*6|4.*6.*sonnet"},
    "claude-opus-4-6":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25,  "cache_read": 0.50,
                          "lf_name": "bedrock-claude-opus-4-6",   "lf_pattern": r"(?i)claude.*opus.*4.*6|4.*6.*opus"},
    "claude-opus-4-5":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25,  "cache_read": 0.50},
    "claude-haiku-4-5":  {"input": 1.00,  "output":  5.00, "cache_write": 1.25,  "cache_read": 0.10,
                          "lf_name": "bedrock-claude-haiku-4-5",  "lf_pattern": r"(?i)claude.*haiku.*4.*5|4.*5.*haiku"},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30,
                          "lf_name": "bedrock-claude-sonnet-4-5", "lf_pattern": r"(?i)claude.*sonnet.*4.*5|4.*5.*sonnet"},
    "claude-sonnet-4":   {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30,
                          "lf_name": "bedrock-claude-sonnet-4",   "lf_pattern": r"(?i)claude.*sonnet.*4-[0-9]|claude.*4-[0-9].*sonnet"},
    # ── Claude 3.x family ─────────────────────────────────────────────────────
    "claude-3-5-haiku":  {"input": 0.80,  "output":  4.00, "cache_write": 1.00,  "cache_read": 0.08,
                          "lf_name": "bedrock-claude-3-5-haiku",  "lf_pattern": r"(?i)claude.*3.*5.*haiku|3-5-haiku"},
    "claude-3-5-sonnet": {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30,
                          "lf_name": "bedrock-claude-3-5-sonnet", "lf_pattern": r"(?i)claude.*3.*5.*sonnet|3-5-sonnet"},
    "claude-3-opus":     {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50,
                          "lf_name": "bedrock-claude-3-opus",     "lf_pattern": r"(?i)claude.*3.*opus"},
    "claude-3-sonnet":   {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30,
                          "lf_name": "bedrock-claude-3-sonnet",   "lf_pattern": r"(?i)claude.*3.*sonnet"},
    "claude-3-haiku":    {"input": 0.25,  "output":  1.25, "cache_write": 0.30,  "cache_read": 0.03,
                          "lf_name": "bedrock-claude-3-haiku",    "lf_pattern": r"(?i)claude.*3.*haiku"},
}

_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}

# Maps graph node name → LLM tier (for labelling; actual cost uses model name from response).
NODE_TIER: dict[str, str] = {
    N.INTAKE_CLASSIFY:    "fast",
    N.GENERAL_CHAT:       "fast",
    N.VISUALIZATION:      "fast",
    N.DOMAIN_SPECIALIST:  "balanced",
    N.ONTOLOGY_LOOKUP:    "balanced",
    N.BRAIN_RETRIEVAL:    "balanced",
    N.SPARQL_VALIDATE:    "balanced",
    N.GOVERNANCE_GATE:    "balanced",
    N.GRAPH_REASONING:    "balanced",
    N.ANSWER_SYNTHESIS:   "balanced",
    N.PLAN:               "balanced",
    N.PLAN_VALIDATOR:     "balanced",
    N.STEP_REFLECTOR:     "balanced",
    N.FINAL_REFLECTOR:    "balanced",
    N.COMPRESS:           "balanced",
    N.VERIFIER:           "balanced",
    N.EXECUTOR:           "balanced",
    N.HUMAN_IN_LOOP:      "balanced",
    N.SPARQL_GEN:         "deep",
    N.REPAIRER:           "deep",
}


def _get_pricing(model_name: str) -> dict:
    normalized = model_name.lower().replace("_", "-")
    for key in sorted(MODELS, key=len, reverse=True):
        if key in normalized:
            return MODELS[key]
    return _DEFAULT_PRICING


def calculate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    p = _get_pricing(model_name)
    per_m = 1_000_000
    cost = (
        input_tokens * p["input"] / per_m
        + output_tokens * p["output"] / per_m
        + cache_creation_tokens * p["cache_write"] / per_m
        + cache_read_tokens * p["cache_read"] / per_m
    )
    return round(cost, 8)


def extract_usage(ai_message, node: str, tier: str = "balanced") -> dict | None:
    """Extract token counts and compute cost from an AIMessage.

    Returns None if usage_metadata is absent or all-zero.
    """
    usage_meta = getattr(ai_message, "usage_metadata", None) or {}
    if not usage_meta:
        return None

    resp_meta = getattr(ai_message, "response_metadata", {}) or {}
    model_name = (
        resp_meta.get("model_name")
        or resp_meta.get("model_id")
        or resp_meta.get("model")
        or tier
    )

    input_tokens = int(usage_meta.get("input_tokens") or 0)
    output_tokens = int(usage_meta.get("output_tokens") or 0)
    total_tokens = int(usage_meta.get("total_tokens") or 0) or (input_tokens + output_tokens)
    token_details = usage_meta.get("input_token_details") or {}
    cache_creation = int(token_details.get("cache_creation") or 0)
    cache_read = int(token_details.get("cache_read") or 0)

    if total_tokens == 0:
        return None

    cost_usd = calculate_cost(model_name, input_tokens, output_tokens, cache_creation, cache_read)

    return {
        "node": node,
        "tier": tier,
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "cost_usd": cost_usd,
    }


def aggregate_token_usage(records: list[dict]) -> dict:
    """Aggregate per-call token records into a pipeline-level summary."""
    if not records:
        return {}
    return {
        "total_input_tokens": sum(r["input_tokens"] for r in records),
        "total_output_tokens": sum(r["output_tokens"] for r in records),
        "total_tokens": sum(r["total_tokens"] for r in records),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 8),
        "cache_creation_tokens": sum(r["cache_creation_tokens"] for r in records),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in records),
        "by_node": records,
    }
