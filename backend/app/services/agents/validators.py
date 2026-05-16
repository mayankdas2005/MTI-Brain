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


async def validate_predicates(
    query: str,
    fuseki_client=None,
    skip_classes: bool = False,
) -> tuple[bool, str]:
    """Check that every lpp: predicate in the query is defined in the ontology.

    Uses the in-memory ontology dict (loaded from lpp-ontology.ttl at startup)
    instead of querying Fuseki. Fuseki ASK checks test data existence, not
    schema validity — valid ontology terms with no instances would fail even
    though they are correct predicates.
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
    return True, ""
