"""Central registry of LangGraph node name strings.

Import ``N`` and use ``N.DOMAIN_SPECIALIST`` etc. wherever a node name string
is needed.  Renaming a node requires a single change here — all of graph.py,
token_tracker.py, and any future consumer update automatically.
"""

from __future__ import annotations


class N:
    """All LangGraph pipeline node name constants."""

    # ── Intake ───────────────────────────────────────────────────────────────
    INTAKE_CLASSIFY = "intake_classify"
    GENERAL_CHAT    = "general_chat"
    REJECTED        = "rejected"

    # ── Inner pipeline ────────────────────────────────────────────────────────
    DOMAIN_SPECIALIST = "domain_specialist"
    ONTOLOGY_LOOKUP   = "ontology_lookup"
    BRAIN_RETRIEVAL   = "brain_retrieval"
    SPARQL_GEN        = "sparql_gen"
    SPARQL_VALIDATE   = "sparql_validate"
    GOVERNANCE_GATE   = "governance_gate"
    SPARQL_EXECUTE    = "sparql_execute"
    GRAPH_REASONING   = "graph_reasoning"
    VERIFIER          = "verifier"

    # ── Outer plan-execute loop ───────────────────────────────────────────────
    PLAN            = "plan"
    PLAN_VALIDATOR  = "plan_validator"
    EXECUTOR        = "executor"
    STEP_REFLECTOR  = "step_reflector"
    REPAIRER        = "repairer"
    FINAL_REFLECTOR = "final_reflector"

    # ── Convergence ───────────────────────────────────────────────────────────
    ANSWER_SYNTHESIS = "answer_synthesis"
    VISUALIZATION    = "visualization"
    HUMAN_IN_LOOP    = "human_in_loop"
    COMPRESS         = "compress"

    # ── Routing labels (not real nodes) ──────────────────────────────────────
    # SKIP_VIZ is returned by route_synthesis_to_viz and mapped to HUMAN_IN_LOOP
    # in the conditional-edges path map.
    SKIP_VIZ = "skip_viz"
