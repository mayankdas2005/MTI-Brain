"""Parse and cache the LPP ontology for use in pipeline prompts.

Loads ``lpp-ontology.ttl`` once at startup and exposes:
  - ``get_ontology_summary()``        — compact string injected into planner/SPARQL prompts
  - ``get_ontology_dict()``           — structured dict for programmatic term resolution
  - ``get_r2rml_class_properties()``  — {ClassName: [property_local, ...]} from R2RML mappings
  - ``get_concept_values()``          — {ClassName: [label, ...]} for SKOS enum concepts
  - ``resolve_term(label)``           — fuzzy match a natural-language term to an lpp: URI
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.logger import logger

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "lpp-ontology.ttl"
_R2RML_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "lpp-r2rml.ttl"

_ontology_dict: dict = {}
_ontology_summary: str = ""
_r2rml_class_properties: dict[str, list[str]] = {}
_concept_values: dict[str, list[str]] = {}


def _load_ontology() -> tuple[dict, dict[str, list[str]]]:
    """Parse lpp-ontology.ttl and return (structured_dict, concept_values)."""
    try:
        from rdflib import Graph, Namespace, RDF, RDFS, OWL
        from rdflib.namespace import SKOS
    except ImportError:
        logger.warning("rdflib not installed — ontology loader disabled.")
        return {}, {}

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

    def _str(node, fallback: str = "") -> str:
        return str(node).split("@")[0].strip() if node else fallback

    for s, _, _ in g.triples((None, RDF.type, OWL.Class)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri:
            continue
        label = _str(g.value(s, RDFS.label), uri.split("#")[-1])
        comment = _str(g.value(s, RDFS.comment))
        subclass_of = [str(o).split("#")[-1] for _, _, o in g.triples((s, RDFS.subClassOf, None))]
        classes.append({
            "uri": uri,
            "local": uri.split("#")[-1],
            "label": label,
            "comment": comment,
            "subClassOf": subclass_of,
        })

    # Second pass: pick up subclasses declared only via rdfs:subClassOf (no explicit owl:Class triple).
    # e.g. lpp:WireTransfer rdfs:subClassOf lpp:PaymentTransaction — valid OWL but missed by the loop above.
    existing_class_uris = {c["uri"] for c in classes}
    for s, _, _ in g.triples((None, RDFS.subClassOf, None)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri or uri in existing_class_uris:
            continue
        label = _str(g.value(s, RDFS.label), uri.split("#")[-1])
        comment = _str(g.value(s, RDFS.comment))
        subclass_of = [str(o).split("#")[-1] for _, _, o in g.triples((s, RDFS.subClassOf, None))]
        classes.append({
            "uri": uri,
            "local": uri.split("#")[-1],
            "label": label,
            "comment": comment,
            "subClassOf": subclass_of,
        })
        existing_class_uris.add(uri)

    for s, _, _ in g.triples((None, RDF.type, OWL.ObjectProperty)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri:
            continue
        label = _str(g.value(s, RDFS.label), uri.split("#")[-1])
        comment = _str(g.value(s, RDFS.comment))
        domain_node = g.value(s, RDFS.domain)
        range_node = g.value(s, RDFS.range)
        object_props.append({
            "uri": uri,
            "local": uri.split("#")[-1],
            "label": label,
            "comment": comment,
            "domain": str(domain_node).split("#")[-1] if domain_node else None,
            "range": str(range_node).split("#")[-1] if range_node else None,
        })

    for s, _, _ in g.triples((None, RDF.type, OWL.DatatypeProperty)):
        uri = str(s)
        if "lpp.example/ontology#" not in uri:
            continue
        label = _str(g.value(s, RDFS.label), uri.split("#")[-1])
        comment = _str(g.value(s, RDFS.comment))
        range_node = g.value(s, RDFS.range)
        datatype_props.append({
            "uri": uri,
            "local": uri.split("#")[-1],
            "label": label,
            "comment": comment,
            "range": str(range_node).split("#")[-1] if range_node else "string",
        })

    # ── SKOS concept instances — collect valid filter values per class ──────────
    concept_values: dict[str, list[str]] = {}
    for s, _, cls in g.triples((None, RDF.type, None)):
        cls_str = str(cls)
        if "lpp.example/ontology#" not in cls_str:
            continue
        parent = cls_str.split("#")[-1]
        pref_label = g.value(s, SKOS.prefLabel)
        if pref_label:
            val = _str(pref_label)
            concept_values.setdefault(parent, [])
            if val not in concept_values[parent]:
                concept_values[parent].append(val)

    return {
        "prefixes": prefixes,
        "classes": classes,
        "object_properties": object_props,
        "datatype_properties": datatype_props,
    }, concept_values


def _build_summary(d: dict, concept_vals: dict[str, list[str]]) -> str:
    if not d:
        return ""
    lines = [
        "PREFIX lpp: <https://lpp.example/ontology#>",
        "",
        "CLASSES:",
    ]
    for c in d.get("classes", []):
        sub = f" (subClassOf: {', '.join(c['subClassOf'])})" if c.get("subClassOf") else ""
        comment = f"  # {c['comment']}" if c.get("comment") else ""
        enums = concept_vals.get(c["local"], [])
        enum_note = f"  [values: {', '.join(enums)}]" if enums else ""
        lines.append(f"  lpp:{c['local']} — {c['label']}{sub}{comment}{enum_note}")

    lines.append("")
    lines.append("OBJECT PROPERTIES (relationships / joins):")
    for p in d.get("object_properties", []):
        domain = f"{p['domain']} " if p.get("domain") else ""
        rng = f"→ {p['range']}" if p.get("range") else ""
        comment = f"  # {p['comment']}" if p.get("comment") else ""
        lines.append(f"  lpp:{p['local']} : {domain}{rng}{comment}")

    lines.append("")
    lines.append("DATATYPE PROPERTIES (literal values):")
    for p in d.get("datatype_properties", []):
        comment = f"  # {p['comment']}" if p.get("comment") else ""
        lines.append(f"  lpp:{p['local']} : {p.get('range', 'string')}{comment}")

    return "\n".join(lines)


def _load_r2rml_class_properties() -> dict[str, list[str]]:
    """Extract class → [property_local, ...] from R2RML rr:predicateObjectMap entries.

    This is the authoritative source for which lpp: properties are actually
    materialized for each class in Fuseki — more reliable than rdfs:domain declarations
    for properties that have no domain restriction in the ontology.
    """
    if not _R2RML_PATH.exists():
        return {}
    try:
        from rdflib import Graph, Namespace, RDF
        RR = Namespace("http://www.w3.org/ns/r2rml#")
        g = Graph()
        g.parse(str(_R2RML_PATH), format="turtle")

        result: dict[str, list[str]] = {}
        for subj in set(g.subjects()):
            # Collect class names for this TriplesMap
            cls_names: list[str] = []
            for sm in g.objects(subj, RR.subjectMap):
                for cls in g.objects(sm, RR["class"]):
                    cls_str = str(cls)
                    local = cls_str.split("#")[-1] if "#" in cls_str else cls_str.split("/")[-1]
                    if local:
                        cls_names.append(local)

            if not cls_names:
                continue

            # Collect lpp: predicates for this TriplesMap
            preds: list[str] = []
            for pom in g.objects(subj, RR.predicateObjectMap):
                pred = g.value(pom, RR.predicate)
                if pred and "lpp.example/ontology#" in str(pred):
                    preds.append(str(pred).split("#")[-1])

            for cls in cls_names:
                result.setdefault(cls, [])
                for p in preds:
                    if p not in result[cls]:
                        result[cls].append(p)

        return result
    except Exception as e:
        logger.warning(f"R2RML class property load failed: {e}")
        return {}


def init_ontology() -> None:
    """Load and cache the ontology. Call once at startup."""
    global _ontology_dict, _ontology_summary, _r2rml_class_properties, _concept_values
    try:
        _ontology_dict, _concept_values = _load_ontology()
        _ontology_summary = _build_summary(_ontology_dict, _concept_values)
        _r2rml_class_properties = _load_r2rml_class_properties()
        cls_count = len(_ontology_dict.get("classes", []))
        cls_with_comment = sum(1 for c in _ontology_dict.get("classes", []) if c.get("comment"))
        obj_props = _ontology_dict.get("object_properties", [])
        dt_props = _ontology_dict.get("datatype_properties", [])
        obj_with_comment = sum(1 for p in obj_props if p.get("comment"))
        dt_with_comment = sum(1 for p in dt_props if p.get("comment"))
        top_r2rml = sorted(_r2rml_class_properties.items(), key=lambda x: -len(x[1]))[:5]
        top_r2rml_str = ", ".join(f"{cls}({len(props)})" for cls, props in top_r2rml)
        skos_str = ", ".join(f"{k}={len(v)}" for k, v in sorted(_concept_values.items()))
        logger.info(
            f"Ontology loaded: {cls_count} classes ({cls_with_comment} with comments), "
            f"{len(obj_props)} object props ({obj_with_comment} with comments), "
            f"{len(dt_props)} datatype props ({dt_with_comment} with comments)"
        )
        logger.info(f"R2RML mappings: {len(_r2rml_class_properties)} classes — top: {top_r2rml_str}")
        logger.info(f"SKOS concept groups ({len(_concept_values)}): {skos_str}")
    except Exception as e:
        logger.error(f"Ontology load failed: {e}")
        _ontology_dict = {}
        _ontology_summary = ""
        _r2rml_class_properties = {}
        _concept_values = {}


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


def get_r2rml_class_properties() -> dict[str, list[str]]:
    """Return the R2RML-derived class → [property_local, ...] mapping."""
    return _r2rml_class_properties


def get_concept_values() -> dict[str, list[str]]:
    """Return {ClassName: [prefLabel, ...]} for SKOS concept instances."""
    return _concept_values


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
