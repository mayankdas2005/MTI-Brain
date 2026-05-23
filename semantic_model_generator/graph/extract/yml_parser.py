"""
Parse semantic_model.yml → TableMeta list + FKEdge list (is_ontology=True).

159 R2RML subdomains → ~90 unique physical Table nodes.
Multiple subdomains for the same physical table are merged.

Two-pass parse:
  Pass 1 — build entity_type → pk_col map from each subdomain's columns.
  Pass 2 — use that map so FK edges have the correct to_col (target PK),
            not the source FK column name that was erroneously extracted
            from the IRI template placeholder.
"""

from __future__ import annotations

from pathlib import Path
from collections import defaultdict

import yaml

from ..models import ColumnMeta, FKEdge, TableMeta

_SCHEMA = "lpp"
_NEW_YML = Path(__file__).resolve().parents[2] / "output" / "semantic_model.yml"

_PK_PREFERENCE = ("code", "id", "uuid", "ref", "key")


def _target_type_to_fqn(target_entity_type: str) -> str:
    return f"{_SCHEMA}.{target_entity_type.replace('-', '_')}"


def _infer_pk_col(columns: dict) -> str:
    """Return most likely PK column name from a subdomain's columns dict."""
    col_names = list(columns.keys())
    for preferred in _PK_PREFERENCE:
        if preferred in col_names:
            return preferred
    return col_names[0] if col_names else "code"


def _is_derived_only(source: dict) -> bool:
    if source.get("type") != "sql_query":
        return False
    tables = source.get("tables", [])
    sql = source.get("sql", "")
    if len(tables) > 1:
        return True
    if "group by" in sql.lower() or "join " in sql.lower():
        return True
    return False


def parse(yml_path: Path = _NEW_YML) -> tuple[list[TableMeta], list[ColumnMeta], list[FKEdge]]:
    """
    Returns:
        tables  — one TableMeta per unique physical FQN
        columns — ColumnMeta list (all columns across all tables)
        edges   — FKEdge list with is_ontology=True
    """
    with open(yml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    subdomains: dict = data.get("business_subdomains", {})

    # ── Pass 1: build entity_type → pk_col map ────────────────────────────
    # Keyed by both the hyphenated form (target_entity_type in connections)
    # and the snake_case form (table name) so lookups work either way.
    entity_pk: dict[str, str] = {}
    for sd_name, sd in subdomains.items():
        source = sd.get("source", {})
        src_type = source.get("type", "table")
        if src_type == "table":
            fqn = source.get("table", "")
        else:
            tables_list = source.get("tables", [])
            fqn = tables_list[0] if tables_list else ""
        if not fqn:
            continue
        table_short = fqn.split(".")[-1]          # e.g. "bank_account"
        hyphenated  = table_short.replace("_", "-")  # e.g. "bank-account"
        columns_raw = sd.get("columns") or {}
        pk = _infer_pk_col(columns_raw)
        for key in (table_short, hyphenated):
            if key not in entity_pk:              # first subdomain wins
                entity_pk[key] = pk

    # ── Pass 2: aggregate table / column / edge data ──────────────────────
    table_map: dict[str, dict] = {}

    for subdomain_name, sd in subdomains.items():
        source = sd.get("source", {})
        src_type = source.get("type", "table")

        if src_type == "table":
            fqn = source.get("table", "")
        else:
            tables_list = source.get("tables", [])
            if not tables_list:
                continue
            fqn = tables_list[0]

        if not fqn:
            continue

        is_derived = _is_derived_only(source)
        ontology_class = sd.get("ontology_class", "")
        columns_raw: dict = sd.get("columns", {}) or {}
        connections_raw: list = sd.get("connections") or []

        if fqn not in table_map:
            table_map[fqn] = {
                "fqn": fqn,
                "name": fqn.split(".")[-1],
                "schema": fqn.split(".")[0] if "." in fqn else _SCHEMA,
                "ontology_class": ontology_class,
                "is_derived": is_derived,
                "columns": {},
                "connections": [],
            }

        entry = table_map[fqn]

        if not entry["ontology_class"] and ontology_class:
            entry["ontology_class"] = ontology_class

        for col_name, col_info in columns_raw.items():
            if col_name not in entry["columns"]:
                entry["columns"][col_name] = {
                    "name": col_name,
                    "data_type": col_info.get("type", "varchar"),
                    "predicate": col_info.get("predicate", ""),
                }

        for conn in connections_raw:
            via = conn.get("via_columns", [])
            if not via:
                continue
            from_col = via[0]
            target_type = conn.get("target_entity_type", "")
            to_fqn = _target_type_to_fqn(target_type)

            # Resolve to_col from the target table's inferred PK column.
            # entity_pk is keyed by both "bank_account" and "bank-account" forms.
            to_col = entity_pk.get(target_type) or entity_pk.get(
                target_type.replace("-", "_"), "code"
            )

            key = (from_col, to_fqn, to_col)
            if key not in {(c["from_col"], c["to_fqn"], c["to_col"]) for c in entry["connections"]}:
                entry["connections"].append({
                    "from_col": from_col,
                    "to_fqn": to_fqn,
                    "to_col": to_col,
                    "predicate": conn.get("predicate", ""),
                })

    tables: list[TableMeta] = []
    columns: list[ColumnMeta] = []
    edges: list[FKEdge] = []

    for fqn, entry in table_map.items():
        name = entry["name"]
        schema = entry["schema"]
        table_type = "derived" if entry["is_derived"] else ""

        tm = TableMeta(
            fqn=fqn,
            name=name,
            schema=schema,
            table_type_db="",
            ontology_class=entry["ontology_class"],
            table_type=table_type,
        )
        tables.append(tm)

        for pos, (col_name, col_info) in enumerate(entry["columns"].items(), start=1):
            cm = ColumnMeta(
                table_fqn=fqn,
                name=col_name,
                data_type=col_info["data_type"],
                ordinal_position=pos,
            )
            columns.append(cm)

        for conn in entry["connections"]:
            edge = FKEdge(
                from_table=fqn,
                from_col=conn["from_col"],
                to_table=conn["to_fqn"],
                to_col=conn["to_col"],
                confidence=1.0,
                source="semantic_model_yml",
                is_ontology=True,
                predicate=conn["predicate"],
            )
            edges.append(edge)

    return tables, columns, edges
