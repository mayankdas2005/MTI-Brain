"""Node pre-synthesis: data_quality_checker — single job: DATA_INTEGRITY_GATE scan.

Runs after executor, before synthesis. Haiku model — fast scan, not reasoning.
Scans result values for implausible figures (balance > $100B, % > 10000%, etc.)

Separates the "check data quality" concern from "write narrative" concern.
synthesis.py receives data_quality_flag and data_quality_reason — no longer
needs to scan values itself, eliminating the "check before writing" impossibility.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import _build_data_profile
from app.services.agents.prompts import DATA_QUALITY_CHECKER_PROMPT
from app.services.agents.state import AnalyticsState


async def data_quality_checker(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("data_quality_checker START | thread={}", state.get("thread_id", ""))

    # If no data, no quality check needed
    if state.get("no_data"):
        return {"data_quality_flag": False, "data_quality_reason": None}

    query_summary = state.get("query_summary") or {}
    result_list = state.get("result_list") or []

    all_columns: list[str] = []
    all_rows: list[list] = []
    for res in result_list:
        if res.get("rows"):
            if not all_columns and res.get("columns"):
                all_columns = res["columns"]
            all_rows.extend(res["rows"])
    if not all_rows:
        return {"data_quality_flag": False, "data_quality_reason": None}

    import datetime
    today = (state.get("current_date") or datetime.date.today().isoformat())

    # X6+Q4: pass decision_type so DQC applies context-aware rules
    _decision_type = state.get("decision_type") or "lookup"
    decision_type_section = f"decision_type = \"{_decision_type}\""

    data_profile = _build_data_profile(all_columns, all_rows, query_summary)

    prompt_text = DATA_QUALITY_CHECKER_PROMPT.format(
        today=today,
        data_profile=data_profile,
        decision_type_section=decision_type_section,
    )

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker
    from langchain_core.messages import HumanMessage

    llm = get_llm("fast")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke([HumanMessage(content=prompt_text)], config=config), service="bedrock-data-quality-checker", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
        raw = response.content if isinstance(response.content, str) else ""
        import json_repair
        parsed = json_repair.loads(raw)
        triggered = bool(parsed.get("triggered", False))
        reason = parsed.get("reason") or None
    except Exception as e:
        logger.warning("data_quality_checker | failed | thread={} | error={}", state.get("thread_id"), e)
        triggered = False
        reason = None

    if triggered:
        logger.warning("data_quality_checker | TRIGGERED | thread={} | reason={}", state.get("thread_id"), reason)
    else:
        logger.debug("data_quality_checker | clean | thread={}", state.get("thread_id"))

    return {"data_quality_flag": triggered, "data_quality_reason": reason}
