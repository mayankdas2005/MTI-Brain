"""compute_confidence — LLM self-verification of answer quality.

Called in stream_pipeline (pipeline.py) after the astream_events loop completes,
using the locally-captured state dict. Not a LangGraph node.

The LLM is asked: "Does this answer actually answer the question?" and returns
a score 0-100 with a one-sentence plain-English explanation.

Returns a dict: {score: int 0-100, label: str, explanation: str}
Returns None for general_chat (no data grounding applicable).
"""

import json_repair

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.agents.bedrock import get_llm
from app.services.agents.prompts import CONFIDENCE_JUDGE_PROMPT


def _label(score: int) -> str:
    if score >= 75:
        return "High"
    if score >= 55:
        return "Medium"
    if score >= 35:
        return "Low"
    return "Very Low"


async def compute_confidence(state: dict) -> dict | None:
    if state.get("question_type") == "general_chat":
        return None

    if state.get("no_data"):
        return {
            "score": 15,
            "label": "Very Low",
            "explanation": "No matching records were found for the requested criteria.",
        }

    question = state.get("question", "")
    answer = (state.get("answer", "") or "")

    prompt = CONFIDENCE_JUDGE_PROMPT.format(question=question, answer=answer)

    try:
        from app.core.retry import retry_async
        _llm = get_llm("fast")
        response = await retry_async(
            lambda: _llm.ainvoke([
                SystemMessage(content="You are a strict answer-grounding evaluator. Return only valid JSON."),
                HumanMessage(content=prompt),
            ]),
            service="bedrock-confidence",
            max_attempts=2,
            backoff_base=5.0,
        )
        raw = (response.content or "").strip()
        parsed = json_repair.loads(raw)
        if not isinstance(parsed, dict):
            return {"score": 70, "label": "Medium", "explanation": ""}
        score = max(5, min(95, int(parsed.get("score", 70))))
        label = _label(score)
        explanation = parsed.get("explanation") or ""
        return {"score": score, "label": label, "explanation": explanation}
    except Exception:
        return {"score": 70, "label": "Medium", "explanation": ""}
