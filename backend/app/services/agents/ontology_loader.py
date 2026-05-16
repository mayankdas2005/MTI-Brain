"""Parse and cache the LPP ontology for use in pipeline prompts.

Loads ``lpp-ontology.ttl`` once at startup and exposes:
  - ``get_ontology_summary()``  — compact string injected into planner/SPARQL prompts
  - ``get_ontology_dict()``     — structured dict for programmatic term resolution
  - ``resolve_term(label)``     — fuzzy match a natural-language term to an lpp: URI
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.logger import logger

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "lpp-ontology.ttl"

_ontology_dict: dict = {}
_ontology_summary: str = ""


def _load_ontology() -> dict:
    """Parse the TTL file with rdflib and build a structured dict."""
    try:
        from rdflib import Graph, Namespace, RDF, RDFS, OWL
        from rdflib.namespace import SKOS
    except ImportError:
        logger.warning("rdflib not installed — ontology loader disabled.")
        return {}

    g = Graph()
    g.parse(str(_ONTOLOGY_PATH), format="turtle")

    LPP = Namespace("https://lpp.example/ontology#")

    classes: list[dict] = []
    object_props: list[dict] = []
    datatype_props: list[dict] = []
    prefixes = {
        "lpp": "https://lpp.example/ontology#",
        "lppid": "https://lpp.example/id/",
    }

    for s, _, _ in g.triples((None, RDF.type, OWL.Class)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri:
            continue
        label_node = g.value(s, RDFS.label)
        label = str(label_node).split("@")[0] if label_node else uri.split("#")[-1]
        subclass_of = [str(o).split("#")[-1] for _, _, o in g.triples((s, RDFS.subClassOf, None))]
        classes.append({"uri": uri, "local": uri.split("#")[-1], "label": label, "subClassOf": subclass_of})

    for s, _, _ in g.triples((None, RDF.type, OWL.ObjectProperty)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri:
            continue
        label_node = g.value(s, RDFS.label)
        label = str(label_node).split("@")[0] if label_node else uri.split("#")[-1]
        domain_node = g.value(s, RDFS.domain)
        range_node = g.value(s, RDFS.range)
        object_props.append({
            "uri": uri,
            "local": uri.split("#")[-1],
            "label": label,
            "domain": str(domain_node).split("#")[-1] if domain_node else None,
            "range": str(range_node).split("#")[-1] if range_node else None,
        })

    for s, _, _ in g.triples((None, RDF.type, OWL.DatatypeProperty)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri:
            continue
        label_node = g.value(s, RDFS.label)
        label = str(label_node).split("@")[0] if label_node else uri.split("#")[-1]
        range_node = g.value(s, RDFS.range)
        datatype_props.append({
            "uri": uri,
            "local": uri.split("#")[-1],
            "label": label,
            "range": str(range_node).split("#")[-1] if range_node else "string",
        })

    return {
        "prefixes": prefixes,
        "classes": classes,
        "object_properties": object_props,
        "datatype_properties": datatype_props,
    }


def _build_summary(d: dict) -> str:
    if not d:
        return ""
    lines = [
        "PREFIX lpp: <https://lpp.example/ontology#>",
        "",
        "CLASSES:",
    ]
    for c in d.get("classes", []):
        sub = f" (subClassOf: {', '.join(c['subClassOf'])})" if c.get("subClassOf") else ""
        lines.append(f"  lpp:{c['local']} — {c['label']}{sub}")

    lines.append("")
    lines.append("OBJECT PROPERTIES (relationships / joins):")
    for p in d.get("object_properties", []):
        domain = f"{p['domain']} " if p.get("domain") else ""
        rng = f"→ {p['range']}" if p.get("range") else ""
        lines.append(f"  lpp:{p['local']} : {domain}{rng}")

    lines.append("")
    lines.append("DATATYPE PROPERTIES (literal values):")
    for p in d.get("datatype_properties", []):
        lines.append(f"  lpp:{p['local']} : {p.get('range', 'string')}")

    return "\n".join(lines)


def init_ontology() -> None:
    """Load and cache the ontology. Call once at startup."""
    global _ontology_dict, _ontology_summary
    try:
        _ontology_dict = _load_ontology()
        _ontology_summary = _build_summary(_ontology_dict)
        cls_count = len(_ontology_dict.get("classes", []))
        prop_count = len(_ontology_dict.get("object_properties", [])) + len(_ontology_dict.get("datatype_properties", []))
        logger.info(f"Ontology loaded: {cls_count} classes, {prop_count} properties")
    except Exception as e:
        logger.error(f"Ontology load failed: {e}")
        _ontology_dict = {}
        _ontology_summary = ""


def get_ontology_summary() -> str:
    """Return the compact ontology string for prompt injection."""
    return _ontology_summary


def get_ontology_dict() -> dict:
    """Return the full structured ontology dict."""
    return _ontology_dict


def resolve_term(label: str) -> list[dict]:
    """Fuzzy-match a natural-language label to lpp: URIs.

    Checks classes and properties. Returns a list of matching term dicts.
    Matching is case-insensitive substring + word-overlap.
    """
    if not _ontology_dict:
        return []

    label_lower = label.lower()
    words = set(re.findall(r"\w+", label_lower))
    matches: list[dict] = []

    all_terms = (
        [{"type": "class", **c} for c in _ontology_dict.get("classes", [])]
        + [{"type": "object_property", **p} for p in _ontology_dict.get("object_properties", [])]
        + [{"type": "datatype_property", **p} for p in _ontology_dict.get("datatype_properties", [])]
    )

    for term in all_terms:
        term_label = term.get("label", term.get("local", "")).lower()
        term_local = term.get("local", "").lower()
        term_words = set(re.findall(r"\w+", term_label + " " + term_local))
        if label_lower in term_label or label_lower in term_local:
            matches.append(term)
        elif words & term_words:
            matches.append(term)

    return matches
