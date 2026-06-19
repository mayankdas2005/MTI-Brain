"""Node 1c: query_planner — extract structured output spec from the user's question.

Runs after anchor_resolver, before schema_enricher.
Fast Haiku call — reads ONLY the user question, no schema knowledge.

Produces query_plan that flows to all three specialists as an explicit contract:
  - expected_output_cols: metric/concept names the user explicitly mentioned
  - required_groupings: how the user wants data broken down
  - required_time_period: exact time phrase from the question
  - is_detail_request: True if user asked for individual records (not aggregated)
  - explicit_entities: named entities to filter on (JPMorgan, USD, wire transfer, etc.)

Graceful degradation: if the LLM call fails, query_plan=None and specialists run
without a contract (same behaviour as before this node existed).
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.prompts import QUERY_PLANNER_PROMPT, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


async def query_planner(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("query_planner START | thread={} | question={}", state["thread_id"], state["question"][:80])

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("fast")

    prompt = QUERY_PLANNER_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        reasoning_directive=REASONING_DIRECTIVE_NORMAL,
        available_tables_section="",
        entity_tokens_section="",
    )

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(
            lambda: llm.ainvoke(prompt, config=config),
            service="bedrock-query-planner",
            max_attempts=2,
            backoff_base=5.0,
        )

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
    except Exception as e:
        logger.warning("query_planner | LLM failed (non-fatal) | thread={} | error={}", state["thread_id"], e)
        return {"query_plan": None}

    m = re.search(r"<output>(.*?)</output>", raw, re.DOTALL | re.IGNORECASE)
    json_str = m.group(1).strip() if m else raw

    try:
        import json_repair
        plan = json_repair.loads(json_str)
        if isinstance(plan, list):
            plan = plan[0] if plan else {}
        if not isinstance(plan, dict):
            raise ValueError(f"unexpected type {type(plan).__name__}")
    except Exception:
        logger.warning("query_planner | JSON parse failed (non-fatal) | thread={} | raw={}", state["thread_id"], raw[:200])
        return {"query_plan": None}

    logger.info(
        "query_planner DONE | thread={} | output_cols={} | groupings={} | time_period={} | is_detail={} | entities={} | fx_required={} | output_slots={}",
        state["thread_id"],
        plan.get("expected_output_cols"),
        plan.get("required_groupings"),
        plan.get("required_time_period"),
        plan.get("is_detail_request"),
        plan.get("explicit_entities"),
        plan.get("fx_required"),
        plan.get("output_slots"),
    )
    return {"query_plan": plan}
