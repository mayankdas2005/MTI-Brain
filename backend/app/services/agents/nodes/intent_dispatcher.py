"""Node 1d: intent_dispatcher — deterministic fan-out node.

Uses LangGraph's Send API to dispatch state to 3 parallel specialist nodes:
  - measure_specialist
  - filter_specialist
  - dimension_specialist

Each specialist receives the same state and writes its output to specialist_outputs
(an Annotated[list, operator.add] field that accumulates all three results).

intent_assembler (defer=True) waits for all three before proceeding.
"""

from __future__ import annotations

from langgraph.types import Command, Send

from app.core.logger import logger
from app.services.agents.state import AnalyticsState


def intent_dispatcher(state: AnalyticsState) -> Command:
    logger.info(
        "intent_dispatcher | fanning out to 3 specialists | thread={}",
        state.get("thread_id", ""),
    )
    return Command(goto=[
        Send("measure_specialist", state),
        Send("filter_specialist", state),
        Send("dimension_specialist", state),
    ])
