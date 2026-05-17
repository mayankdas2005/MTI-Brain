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
_R2RML_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "lpp-r2rml.ttl"

_ontology_dict: dict = {}
_ontology_summary: str = ""
_r2rml_summary: str = ""


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


def _load_r2rml_summary() -> str:
    """Extract a compact table→class mapping from the R2RML file."""
    if not _R2RML_PATH.exists():
        return ""
    try:
        from rdflib import Graph, Namespace, RDF
        RR = Namespace("http://www.w3.org/ns/r2rml#")
        g = Graph()
        g.parse(str(_R2RML_PATH), format="turtle")

        rows: list[str] = []
        seen: set[str] = set()
        for subj in set(g.subjects()):
            for lt in g.objects(subj, RR.logicalTable):
                table_val = g.value(lt, RR.tableName) or g.value(lt, RR.sqlQuery)
                if not table_val:
                    continue
                table_str = str(table_val).split("\n")[0][:60]
                if table_str in seen:
                    continue
                seen.add(table_str)
                classes: list[str] = []
                for sm in g.objects(subj, RR.subjectMap):
                    for cls in g.objects(sm, RR["class"]):
                        local = str(cls).split("#")[-1] if "#" in str(cls) else str(cls).split("/")[-1]
                        classes.append(local)
                rows.append(f"  {table_str} → {', '.join(classes)}" if classes else f"  {table_str}")

        if not rows:
            return ""
        return "MAPPED TABLES (source → KG class):\n" + "\n".join(sorted(rows))
    except Exception as e:
        logger.warning(f"R2RML summary load failed: {e}")
        return ""


def init_ontology() -> None:
    """Load and cache the ontology. Call once at startup."""
    global _ontology_dict, _ontology_summary, _r2rml_summary
    try:
        _ontology_dict = _load_ontology()
        _ontology_summary = _build_summary(_ontology_dict)
        _r2rml_summary = _load_r2rml_summary()
        cls_count = len(_ontology_dict.get("classes", []))
        prop_count = len(_ontology_dict.get("object_properties", [])) + len(_ontology_dict.get("datatype_properties", []))
        logger.info(f"Ontology loaded: {cls_count} classes, {prop_count} properties")
        if _r2rml_summary:
            logger.info(f"R2RML mapping loaded: {_r2rml_summary.count(chr(10))} table entries")
    except Exception as e:
        logger.error(f"Ontology load failed: {e}")
        _ontology_dict = {}
        _ontology_summary = ""
        _r2rml_summary = ""


def get_ontology_summary() -> str:
    """Return the compact ontology string for prompt injection."""
    return _ontology_summary


def get_class_names_summary() -> str:
    """Return a single-line class label list — minimal context for classification nodes."""
    classes = _ontology_dict.get("classes", [])
    if not classes:
        return ""
    return "KG classes: " + ", ".join(c["label"] for c in classes)


def get_ontology_dict() -> dict:
    """Return the full structured ontology dict."""
    return _ontology_dict


def get_r2rml_summary() -> str:
    """Return the compact R2RML table→class mapping for prompt injection."""
    return _r2rml_summary


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
