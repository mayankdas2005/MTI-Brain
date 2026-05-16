"""human_in_loop_node — HIL approval gate for Executive + Advanced queries.

In production this node would yield an interrupt and await an external
approval signal written back into the checkpoint. For now it auto-approves
so the pipeline is testable end-to-end while HIL infrastructure is built out.
"""

from __future__ import annotations

import time

from app.core.logger import logger
from app.services.agents.state import State


async def human_in_loop_node(state: State) -> dict:
    hil_required = state.get("hil_required", False)
    hil_approved = state.get("hil_approved")
    t0 = time.perf_counter()

    if not hil_required:
        step = {"node": "human_in_loop", "label": "HIL not required",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"hil_approved": True, "pipeline_steps": state.get("pipeline_steps", []) + [step]}

    if hil_approved is not None:
        step = {"node": "human_in_loop", "label": f"HIL {'approved' if hil_approved else 'rejected'}",
                "duration_ms": round((time.perf_counter() - t0) * 1000)}
        return {"pipeline_steps": state.get("pipeline_steps", []) + [step]}

    logger.info("HIL approval required — auto-approving (HIL infrastructure pending)")
    step = {"node": "human_in_loop", "label": "HIL auto-approved (pending production HIL)",
            "duration_ms": round((time.perf_counter() - t0) * 1000)}
    return {
        "hil_approved": True,
        "pipeline_steps": state.get("pipeline_steps", []) + [step],
    }
