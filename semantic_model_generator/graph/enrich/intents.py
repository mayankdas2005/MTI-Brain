"""
Intent layer — deterministic pipeline from intent_classes.json.

Builds:
  - Intent nodes (16) with LLM descriptions
  - RELEVANT_TO edges (Table → Intent) with confidence
    confidence = clamp(1 / log2(1 + intent_count_for_class), 0.2, 1.0)
  - intent_tags / intent_tags_text / intent_tags_scored on Table nodes
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)


def load_intent_classes(json_path: Path) -> dict[str, list[str]]:
    """
    Load intent_classes.json.
    Returns {intent_name: [camelCase class names, ...]}
    """
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)
    # Accept both {"intent": [...]} and {"intents": {"intent": [...]}} shapes
    if isinstance(raw, dict):
        if "intents" in raw:
            return raw["intents"]
        return raw
    raise ValueError(f"Unexpected intent_classes.json shape: {type(raw)}")


def compute_relevant_to_edges(
    intent_classes: dict[str, list[str]],
    table_fqn_by_ontology_class: dict[str, list[str]],
) -> list[dict]:
    """
    Build RELEVANT_TO edge dicts from intent_classes mapping.

    table_fqn_by_ontology_class: {camelCaseClass: [fqn, ...]}
      — tables whose ontology_class ends with that camelCase name

    confidence = clamp(1 / log2(1 + N), 0.2, 1.0)
    where N = number of intents this class appears in
    (exclusive tables → high confidence; catch-all tables in general_analytics → low confidence)
    """
    # Count how many intents each class appears in
    class_intent_count: dict[str, int] = {}
    for intent, classes in intent_classes.items():
        for cls in classes:
            class_intent_count[cls] = class_intent_count.get(cls, 0) + 1

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for intent, classes in intent_classes.items():
        for cls in classes:
            fqns = table_fqn_by_ontology_class.get(cls, [])
            n = class_intent_count.get(cls, 1)
            raw_conf = 1.0 / math.log2(1 + n)
            confidence = round(max(0.2, min(1.0, raw_conf)), 4)

            for fqn in fqns:
                key = (fqn, intent)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "table_fqn":    fqn,
                    "intent_name":  intent,
                    "confidence":   confidence,
                })

    log.info("Computed %d RELEVANT_TO edges for %d intents.", len(edges), len(intent_classes))
    return edges


def build_intent_node_dicts(
    intent_classes: dict[str, list[str]],
    intent_descriptions: dict[str, str],
    model_arn: str,
) -> list[dict]:
    """
    Build Intent node property dicts ready for neo4j_loader.load_intent_nodes().
    """
    nodes = []
    for intent_name, classes in intent_classes.items():
        desc_data = intent_descriptions.get(intent_name) or {}
        description = desc_data.get("description", "") if isinstance(desc_data, dict) else str(desc_data)
        nodes.append({
            "name":              intent_name,
            "class_count":       len(classes),
            "description":       description,
            "description_model": model_arn,
        })
    return nodes


def resolve_anchor_table_fqns(
    anchor_classes: list[str],
    table_fqn_by_ontology_class: dict[str, list[str]],
) -> list[str]:
    """
    Resolve a list of camelCase ontology class names to table FQNs.
    Used for QueryTemplate.anchor_table_fqns.
    """
    fqns: list[str] = []
    for cls in anchor_classes:
        fqns.extend(table_fqn_by_ontology_class.get(cls, []))
    return list(dict.fromkeys(fqns))  # deduplicate, preserve order
