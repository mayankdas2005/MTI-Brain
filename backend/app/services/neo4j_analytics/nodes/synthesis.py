"""Node 4: synthesis — generates narrative answer from query results.

Uses Sonnet. Respects persona tone. Cites actual data numbers only.
Includes reliability flag instructions in the prompt context.
"""

from __future__ import annotations

import json

from app.core.logger import logger
from app.services.agents.helpers import parse_tag
from app.services.neo4j_analytics.prompts import REASONING_DIRECTIVE_NORMAL, REASONING_DIRECTIVE_DEEP, SYNTHESIS_PROMPT
from app.services.neo4j_analytics.state import AnalyticsState

_FLAG_INSTRUCTIONS = {
    "unexpected_row_count": (
        "Note: This query returned more rows than expected for a KPI metric. "
        "Results may aggregate across multiple matching records. Mention this uncertainty."
    ),
    "trend_insufficient_data": (
        "Note: Only 1 data point was returned. You cannot draw a trend from a single point. "
        "Say so clearly to the user."
    ),
    "low_confidence_filter": (
        "Note: One or more filter values were matched approximately (not exact match). "
        "Results may include slightly different data than intended. Mention the matched values."
    ),
}


async def synthesis(state: AnalyticsState, config: dict) -> dict:
    logger.info("synthesis START | thread={} | persona={} | no_data={}", state["thread_id"], state.get("persona"), state.get("no_data"))

    query_summary = state.get("query_summary") or {}
    no_data = state.get("no_data", False)
    reliability_flags = state.get("reliability_flags") or []
    low_confidence_filters = state.get("low_confidence_filters") or []
    zero_row_probe_result = state.get("zero_row_probe_result") or ""
    ir_list = state.get("semantic_ir_list") or []
    anchor_tables = ir_list[0].get("anchor_tables", []) if ir_list else []

    flag_instructions = "\n".join(
        _FLAG_INSTRUCTIONS.get(flag, "") for flag in reliability_flags if flag in _FLAG_INSTRUCTIONS
    )

    reasoning_directive = REASONING_DIRECTIVE_DEEP if state.get("persona") == "analyst" else REASONING_DIRECTIVE_NORMAL

    prompt = SYNTHESIS_PROMPT.format_messages(
        persona=state.get("persona", "analyst"),
        question=state["question"],
        anchor_tables=", ".join(anchor_tables),
        reliability_flags=", ".join(reliability_flags) if reliability_flags else "none",
        reliability_flag_instructions=flag_instructions or "No special reliability concerns.",
        no_data=str(no_data),
        zero_row_probe_result=zero_row_probe_result,
        low_confidence_filters=json.dumps(low_confidence_filters) if low_confidence_filters else "none",
        query_summary=json.dumps(query_summary, indent=2, default=str)[:3000],
        reasoning_directive=reasoning_directive,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.langfuse_integration import create_callback_handler, langfuse_context

    llm = get_llm("balanced")
    handler = create_callback_handler()
    callbacks = [handler] if handler else []

    try:
        with langfuse_context(session_id=state["thread_id"], user_id=state["user_id"], tags=["neo4j_analytics", "synthesis"]):
            response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    except Exception as e:
        logger.error("synthesis LLM failed | thread={} | error={}", state["thread_id"], e)
        return {"answer": "I encountered an error preparing your answer. Please try again.", "follow_ups": []}

    raw = response.content or ""
    answer = parse_tag(raw, "answer") or raw.strip()
    follow_ups = _parse_follow_ups(raw)

    logger.info("synthesis DONE | thread={} | answer_len={} | follow_ups={}", state["thread_id"], len(answer), len(follow_ups))
    return {"answer": answer, "follow_ups": follow_ups}


def _parse_follow_ups(raw: str) -> list[str]:
    from json_repair import loads as json_loads
    tag_content = parse_tag(raw, "follow_ups")
    if not tag_content:
        return []
    try:
        result = json_loads(tag_content)
        if isinstance(result, list):
            return [str(q) for q in result[:3]]
    except Exception:
        pass
    return []
