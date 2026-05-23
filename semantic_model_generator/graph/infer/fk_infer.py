"""
Three-tier FK inference for lpp (no naming conventions).

Tier 1 — Query history (STL_QUERYTEXT): confirmed joins, confidence 0.90–0.99
Tier 2 — Exact column name match + type + cardinality: confidence ~0.85
Tier 3 — Normalized name match (strip suffixes) + integer type: confidence 0.75+

SME edges (is_ontology=True) from new.yml are never overwritten.
"""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import NamedTuple

from ..models import ColumnMeta, FKEdge, TableMeta

_FK_SUFFIXES = ("_key", "_id", "_sk", "_nk", "_fk", "_ref", "_code",
                "_no", "_num", "_seq", "_ref_id")

# Generic column names that are PKs in many tables but carry no FK signal
# when they appear bare (without a table-name prefix). SME edges cover valid joins.
_GENERIC_PK_NAMES = frozenset({
    "code", "name", "description", "type", "status", "flag", "category",
    "class", "group", "level", "label", "value", "key", "id",
})
_NUMERIC_TYPES = frozenset({
    "integer", "bigint", "smallint", "int", "int2", "int4", "int8",
    "numeric", "decimal", "float", "float4", "float8",
    "real", "double precision",
})
_CHAR_TYPES_PARTIAL = ("char", "varchar", "text", "string", "nvarchar")

# regex for ON a.col = b.col pattern in SQL
_JOIN_ON_RE = re.compile(
    r"\bON\s+([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)",
    re.IGNORECASE,
)
_FROM_TABLE_RE = re.compile(
    r"(?:FROM|JOIN)\s+lpp\.([a-zA-Z0-9_]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_]+))?",
    re.IGNORECASE,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    n = name.lower()
    for suf in sorted(_FK_SUFFIXES, key=len, reverse=True):
        if n.endswith(suf):
            return n[: -len(suf)]
    return n


def _type_compat(t1: str, t2: str) -> bool:
    t1, t2 = t1.lower(), t2.lower()
    if t1 == t2:
        return True
    if t1 in _NUMERIC_TYPES and t2 in _NUMERIC_TYPES:
        return True
    both_char = (
        any(c in t1 for c in _CHAR_TYPES_PARTIAL)
        and any(c in t2 for c in _CHAR_TYPES_PARTIAL)
    )
    return both_char


def _type_compat_strong(t1: str, t2: str) -> bool:
    t1, t2 = t1.lower(), t2.lower()
    both_int = t1 in {"integer", "bigint", "int", "int4", "int8"} and t2 in {
        "integer", "bigint", "int", "int4", "int8",
    }
    both_char = any(c in t1 for c in _CHAR_TYPES_PARTIAL) and any(
        c in t2 for c in _CHAR_TYPES_PARTIAL
    )
    return both_int or both_char


def _cardinality_ok(fk_col: ColumnMeta, pk_col: ColumnMeta) -> bool:
    fk_d, pk_d = fk_col.n_distinct, pk_col.n_distinct
    if fk_d == 0 or pk_d == 0:
        return True
    if pk_d == -1.0:
        return True
    return abs(fk_d) <= abs(pk_d) * 1.15


def _name_sim(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.88
    return SequenceMatcher(None, na, nb).ratio()


# ─── Query-history join extractor ─────────────────────────────────────────────

def extract_joins_from_sql(sql: str) -> list[dict]:
    """Extract (from_table, from_col, to_table, to_col) from a SQL string."""
    alias_map: dict[str, str] = {}
    for m in _FROM_TABLE_RE.finditer(sql):
        tbl = m.group(1).lower()
        alias = (m.group(2) or tbl).lower()
        alias_map[alias] = tbl
        alias_map[tbl] = tbl

    edges = []
    for m in _JOIN_ON_RE.finditer(sql):
        a_alias, a_col, b_alias, b_col = (
            m.group(1).lower(), m.group(2).lower(),
            m.group(3).lower(), m.group(4).lower(),
        )
        a_tbl = alias_map.get(a_alias)
        b_tbl = alias_map.get(b_alias)
        if not a_tbl or not b_tbl or a_tbl == b_tbl:
            continue
        edges.append({
            "from_table": f"lpp.{a_tbl}",
            "from_col": a_col,
            "to_table": f"lpp.{b_tbl}",
            "to_col": b_col,
        })
    return edges


def build_query_history_edges(query_rows: list[dict]) -> list[FKEdge]:
    """Process Q12 rows → deduplicated FKEdges with frequency-scaled confidence."""
    freq: dict[tuple, int] = {}
    meta: dict[tuple, dict] = {}

    for row in query_rows:
        sql = row.get("full_query_text") or row.get("query_fragment") or ""
        for e in extract_joins_from_sql(sql):
            key = tuple(sorted([
                f"{e['from_table']}.{e['from_col']}",
                f"{e['to_table']}.{e['to_col']}",
            ]))
            freq[key] = freq.get(key, 0) + 1
            meta[key] = e

    edges = []
    for key, count in freq.items():
        e = meta[key]
        confidence = min(0.90 + (count - 1) * 0.01, 0.99)
        edges.append(FKEdge(
            from_table=e["from_table"],
            from_col=e["from_col"],
            to_table=e["to_table"],
            to_col=e["to_col"],
            confidence=round(confidence, 3),
            source="query_history",
            frequency=count,
        ))
    return sorted(edges, key=lambda x: x.frequency, reverse=True)


# ─── Main inference ────────────────────────────────────────────────────────────

def infer_fks(
    tables: list[TableMeta],
    col_map: dict[str, list[ColumnMeta]],
    existing_edges: list[FKEdge],
    query_history_rows: list[dict],
) -> list[FKEdge]:
    """
    Parameters
    ----------
    tables            : all TableMeta objects
    col_map           : {fqn: [ColumnMeta]}
    existing_edges    : SME + declared edges already known (won't be duplicated)
    query_history_rows: Q12 rows

    Returns new inferred FKEdge list (does not include existing_edges).
    """
    # Build seen-pairs set from existing edges
    seen: set[tuple] = set()
    for e in existing_edges:
        key = (e.from_table, e.from_col, e.to_table, e.to_col)
        seen.add(key)
        seen.add((e.to_table, e.to_col, e.from_table, e.from_col))

    results: list[FKEdge] = []

    # ── Tier 1: Query history ──────────────────────────────────────────────
    qh_edges = build_query_history_edges(query_history_rows)
    for e in qh_edges:
        key = (e.from_table, e.from_col, e.to_table, e.to_col)
        rkey = (e.to_table, e.to_col, e.from_table, e.from_col)
        if key not in seen and rkey not in seen:
            seen.add(key)
            results.append(e)

    # ── Build PK index ─────────────────────────────────────────────────────
    pk_exact: dict[str, list[tuple]] = defaultdict(list)
    pk_norm: dict[str, list[tuple]] = defaultdict(list)

    for t in tables:
        for c in col_map.get(t.fqn, []):
            is_pk = (
                c.is_pk
                or c.n_distinct == -1.0
                or (c.is_notnull and c.null_frac < 0.005)
            )
            if not is_pk:
                continue
            pk_exact[c.name.lower()].append((t.fqn, c))
            norm = _normalize(c.name)
            if norm != c.name.lower():
                pk_norm[norm].append((t.fqn, c))

    # ── Tier 2: Exact name match ───────────────────────────────────────────
    for t in tables:
        for col in col_map.get(t.fqn, []):
            if col.is_pk:
                continue
            col_lower = col.name.lower()
            for ref_fqn, ref_col in pk_exact.get(col_lower, []):
                if ref_fqn == t.fqn:
                    continue
                # Skip generic names unless the column name embeds the target table name
                ref_table_short = ref_fqn.split(".")[-1].replace("_", "")
                if col_lower in _GENERIC_PK_NAMES and ref_table_short not in col_lower.replace("_", ""):
                    continue
                key = (t.fqn, col.name, ref_fqn, ref_col.name)
                rkey = (ref_fqn, ref_col.name, t.fqn, col.name)
                if key in seen or rkey in seen:
                    continue
                if not _type_compat(col.data_type, ref_col.data_type):
                    continue
                card_ok = _cardinality_ok(col, ref_col)
                confidence = round(
                    0.60
                    + (0.25 if card_ok else 0)
                    + (0.15 if _type_compat_strong(col.data_type, ref_col.data_type) else 0.05),
                    3,
                )
                if confidence < 0.70:
                    continue
                seen.add(key)
                results.append(FKEdge(
                    from_table=t.fqn, from_col=col.name,
                    to_table=ref_fqn, to_col=ref_col.name,
                    confidence=confidence, source="exact_name_match",
                ))

    # ── Tier 3: Normalized name match (integer cols only) ─────────────────
    for t in tables:
        for col in col_map.get(t.fqn, []):
            if col.is_pk:
                continue
            if col.data_type.lower() not in _NUMERIC_TYPES:
                continue
            col_lower = col.name.lower()
            norm = _normalize(col_lower)
            for ref_fqn, ref_col in pk_norm.get(norm, []):
                if ref_fqn == t.fqn:
                    continue
                ref_table_short = ref_fqn.split(".")[-1].replace("_", "")
                if norm in _GENERIC_PK_NAMES and ref_table_short not in col_lower.replace("_", ""):
                    continue
                key = (t.fqn, col.name, ref_fqn, ref_col.name)
                rkey = (ref_fqn, ref_col.name, t.fqn, col.name)
                if key in seen or rkey in seen:
                    continue
                nsim = _name_sim(col.name, ref_col.name)
                if nsim < 0.75:
                    continue
                if not _type_compat(col.data_type, ref_col.data_type):
                    continue
                card_ok = _cardinality_ok(col, ref_col)
                confidence = round(
                    nsim * 0.50
                    + (0.25 if card_ok else 0)
                    + (0.15 if _type_compat_strong(col.data_type, ref_col.data_type) else 0.05),
                    3,
                )
                if confidence < 0.75:
                    continue
                seen.add(key)
                results.append(FKEdge(
                    from_table=t.fqn, from_col=col.name,
                    to_table=ref_fqn, to_col=ref_col.name,
                    confidence=confidence, source="normalized_name",
                ))

    return sorted(results, key=lambda x: x.confidence, reverse=True)
