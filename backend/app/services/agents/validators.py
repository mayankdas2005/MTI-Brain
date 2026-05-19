"""SPARQL validation utilities for the MTI Brain pipeline.

Provides:
  validate_sparql_syntax  — deterministic parse check (no network call)
  validate_predicates     — async predicate existence check via Fuseki ASK
  extract_sparql_uris     — pull all full-URI references from a SPARQL query
"""

from __future__ import annotations

import re

from app.core.logger import logger


# ─── Syntax validation ────────────────────────────────────────────────────────

def validate_sparql_syntax(query: str) -> tuple[bool, str]:
    """Parse the SPARQL query with rdflib and return (ok, error_msg).

    Only SELECT and ASK queries are permitted; CONSTRUCT/UPDATE/DROP are
    rejected as unsafe.
    """
    if not query or not query.strip():
        return False, "Empty SPARQL — LLM did not produce a query."

    first_word = query.strip().upper().split()[0]
    if first_word not in ("SELECT", "ASK", "PREFIX"):
        if first_word in ("INSERT", "DELETE", "UPDATE", "DROP", "CLEAR", "CREATE"):
            return False, f"Only SELECT/ASK queries are allowed (got: {first_word})."

    # Detect SELECT after PREFIX block
    stripped = re.sub(r"PREFIX\s+\S+\s*<[^>]+>\s*", "", query, flags=re.IGNORECASE).strip()
    first_effective = stripped.upper().split()[0] if stripped.split() else ""
    if first_effective not in ("SELECT", "ASK", ""):
        if first_effective in ("INSERT", "DELETE", "UPDATE", "DROP", "CLEAR"):
            return False, f"Only SELECT/ASK queries are allowed (got: {first_effective})."

    try:
        from rdflib.plugins.sparql import prepareQuery
        prepareQuery(query)
        return True, ""
    except ImportError:
        # rdflib not available — do lightweight brace-balance check
        return _brace_balance_check(query)
    except Exception as e:
        return False, f"SPARQL parse error: {e}"


def _brace_balance_check(query: str) -> tuple[bool, str]:
    depth = 0
    for ch in query:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            return False, "Unmatched closing brace in SPARQL."
    if depth != 0:
        return False, f"Unbalanced braces in SPARQL (depth={depth})."
    if "SELECT" not in query.upper() and "ASK" not in query.upper():
        return False, "SPARQL must contain SELECT or ASK."
    return True, ""


# ─── Predicate existence validation ──────────────────────────────────────────

def extract_sparql_uris(query: str) -> list[str]:
    """Return all full-URI references (<...>) in the SPARQL query."""
    return re.findall(r"<(https?://[^>]+)>", query)


def extract_lpp_predicates(query: str) -> list[str]:
    """Return all lpp:-prefixed predicate URIs referenced in the query.

    Handles both PREFIX-expanded (<lpp:xxx>) and prefixed (lpp:xxx) forms.
    """
    LPP_NS = "https://lpp.example/ontology#"
    uris: list[str] = []

    for match in re.findall(r"<(https://lpp\.example/ontology#[^>]+)>", query):
        uris.append(match)

    for local in re.findall(r"lpp:(\w+)", query):
        uris.append(f"{LPP_NS}{local}")

    return list(set(uris))


def extract_subject_types(query: str) -> set[str]:
    """Return the set of lpp: class local-names used as subject types in the query.

    Matches '?var a lpp:Cls' and '?var rdf:type lpp:Cls' patterns, including
    occurrences after a semicolon continuation (';' on its own line before a
    predicate has no preceding '?var').  We only need the class names — not
    which variable has them — to validate domain constraints.
    """
    return set(re.findall(
        r'(?:^|[\s{;,.])\?\w+\s+(?:a|rdf:type)\s+lpp:(\w+)',
        query,
        re.MULTILINE | re.IGNORECASE,
    ))


def _build_subclass_map(ont: dict) -> dict[str, set[str]]:
    """Return {local: set_of_ancestor_locals} using transitive rdfs:subClassOf closure."""
    direct: dict[str, list[str]] = {
        c["local"]: c.get("subClassOf", []) for c in ont.get("classes", [])
    }
    cache: dict[str, set[str]] = {}

    def ancestors(local: str) -> set[str]:
        if local in cache:
            return cache[local]
        result: set[str] = set()
        for parent in direct.get(local, []):
            result.add(parent)
            result |= ancestors(parent)
        cache[local] = result
        return result

    return {local: ancestors(local) for local in direct}


def _domain_compatible(
    declared_domain: str,
    subject_classes: set[str],
    subclass_map: dict[str, set[str]],
) -> bool:
    """Return True if any subject class equals or inherits from declared_domain."""
    for cls in subject_classes:
        if cls == declared_domain:
            return True
        if declared_domain in subclass_map.get(cls, set()):
            return True
    return False


async def validate_predicates(
    query: str,
    fuseki_client=None,
    skip_classes: bool = False,
) -> tuple[bool, str]:
    """Check that every lpp: predicate in the query is defined in the ontology,
    and that each object-property is used with a subject type that matches its
    declared rdfs:domain.

    Uses the in-memory ontology dict (loaded from lpp-ontology.ttl at startup)
    instead of querying Fuseki. Fuseki ASK checks data existence, not schema
    validity — valid terms with no instances would fail even when correct.
    """
    from app.services.agents.ontology_loader import get_ontology_dict

    predicates = extract_lpp_predicates(query)
    if not predicates:
        return True, ""

    ont = get_ontology_dict()
    if not ont:
        logger.warning("Ontology not loaded; skipping predicate validation")
        return True, ""

    known: set[str] = set()
    for c in ont.get("classes", []):
        known.add(c["local"])
    for p in ont.get("object_properties", []):
        known.add(p["local"])
    for p in ont.get("datatype_properties", []):
        known.add(p["local"])

    missing = [
        uri.split("#")[-1]
        for uri in predicates
        if uri.split("#")[-1] not in known
    ]

    if missing:
        return False, f"Predicates not found in ontology: {', '.join(missing)}"

    # ── Domain check: verify object-property domain matches query subject types ──
    # Only reliable when there is exactly one explicit subject type — in join queries
    # with multiple typed variables, we cannot determine which predicate belongs to
    # which variable without per-variable tracking, causing false positives.
    subject_classes = extract_subject_types(query)
    if subject_classes and len(subject_classes) == 1:
        prop_domain: dict[str, str | None] = {
            p["local"]: p.get("domain")
            for p in ont.get("object_properties", [])
        }
        subclass_map = _build_subclass_map(ont)
        used_locals = {uri.split("#")[-1] for uri in predicates}
        for local in used_locals:
            declared_domain = prop_domain.get(local)
            if declared_domain and not _domain_compatible(declared_domain, subject_classes, subclass_map):
                # Build a hint: find properties with the same range whose domain
                # DOES appear in the query — those are the correct alternatives.
                pred_range = next(
                    (p.get("range") for p in ont.get("object_properties", []) if p["local"] == local),
                    None,
                )
                alternatives = [
                    p["local"]
                    for p in ont.get("object_properties", [])
                    if p.get("domain") in subject_classes
                    and p.get("range") == pred_range
                    and p["local"] != local
                ]
                hint = (
                    f" Consider: {', '.join(f'lpp:{a}' for a in alternatives)}"
                    if alternatives
                    else ""
                )
                return False, (
                    f"lpp:{local} has domain lpp:{declared_domain} but the query uses "
                    f"{', '.join(f'lpp:{c}' for c in sorted(subject_classes))} as subject types. "
                    f"Wrong predicate for this class.{hint}"
                )

    return True, ""
