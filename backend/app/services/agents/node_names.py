"""Node name constants and configuration for the analytics pipeline.

Loads node definitions from backend/node_names.yml — edit that file to change
node IDs, labels, tiers, or streaming config.

Usage:
    from app.services.agents.node_names import N, NODE_MESSAGE, NODE_STREAM

    graph.add_node(N.INTAKE_CLASSIFIER, intake_classifier)
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import yaml


class NodeSpec(NamedTuple):
    id: str
    label: str | None
    tier: str
    stream: str | tuple | None


def _load_specs() -> tuple[NodeSpec, ...]:
    yml_path = Path(__file__).parents[3] / "node_names.yml"
    with yml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    specs: list[NodeSpec] = []
    for entry in data["nodes"]:
        stream = entry.get("stream")
        if isinstance(stream, list):
            stream = tuple(stream)
        specs.append(NodeSpec(
            id=entry["id"],
            label=entry.get("label"),
            tier=entry["tier"],
            stream=stream,
        ))
    return tuple(specs)


_SPECS = _load_specs()

# Build a lookup so N.<ATTR> == node id; attr name comes from the yml `attr` key
# if present, otherwise the id uppercased.
_id_map: dict[str, str] = {s.id: s.id for s in _SPECS}


class N:
    """Node identifier constants, sourced from node_names.yml."""


for _s in _SPECS:
    setattr(N, _s.id.upper(), _s.id)

# ── Auto-generated collections ────────────────────────────────────────────────

ALL_NODES: tuple[str, ...] = tuple(s.id for s in _SPECS)

NODE_TIER: dict[str, str] = {s.id: s.tier for s in _SPECS}

NODE_MESSAGE: dict[str, str] = {
    s.id: s.label
    for s in _SPECS
    if s.label is not None
}

NODE_STREAM: dict[str, str | tuple | None] = {
    s.id: s.stream
    for s in _SPECS
    if s.label is not None
}

# ── Backward-compatible module-level exports ──────────────────────────────────
# Use string IDs directly so these never break if N attribute naming changes.

INTAKE               = _id_map["intake_classifier"]
GENERAL_CHAT         = _id_map["general_chat"]
CONTEXT_FETCHER      = _id_map["context_fetcher"]
TRIBAL_RETRIEVAL     = _id_map["tribal_retrieval"]
ANCHOR_RESOLVER      = _id_map["anchor_resolver"]
SCHEMA_ENRICHER      = _id_map["schema_enricher"]
MEASURE_SPECIALIST   = _id_map["measure_specialist"]
FILTER_SPECIALIST    = _id_map["filter_specialist"]
DIMENSION_SPECIALIST = _id_map["dimension_specialist"]
INTENT_ASSEMBLER     = _id_map["intent_assembler"]
DIRECTIVE_WRITER     = _id_map["directive_writer"]
QUERY_PLANNER        = _id_map["query_planner"]
SCHEMA_GAP_RESOLVER  = _id_map["schema_gap_resolver"]
INTENT_RESOLVER      = _id_map["intent_resolver"]   # legacy fallback path
CLARIFICATION        = _id_map["clarification"]
QUERY_COMPILER       = _id_map["query_compiler"]
FILTER_RESOLVER      = _id_map["filter_resolver"]
SQL_GENERATOR        = _id_map["sql_generator"]
SQL_VALIDATOR        = _id_map["sql_validator"]
EXECUTOR             = _id_map["executor"]
DATA_QUALITY_CHECKER = _id_map["data_quality_checker"]
DEEP_SENSITIVITY     = _id_map["deep_sensitivity"]
DEEP_DENOMINATOR     = _id_map["deep_denominator"]
DEEP_PROJECTION      = _id_map["deep_projection"]
SYNTHESIS            = _id_map["synthesis"]
CHART_AGENT          = _id_map["chart_agent"]
ERROR_RESPONSE       = _id_map["error_response"]
COMPRESS             = _id_map["compress"]
