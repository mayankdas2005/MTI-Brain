"""
Parse lpp_semantic_model.yml → TableMeta list + ColumnMeta list + FKEdge list.

YAML structure (lpp_semantic_model.yml):
  version, schema, database, host, generated_at, table_count
  relationships:   # 200 manually-identified FK entries
    - from_table, from_column, to_table, to_column
  tables:          # 105 tables
    - name, schema, row_count, size_mb, dist_style
      primary_keys: [col, col, ...]   # contains DUPLICATES — must dedup
      foreign_keys:                   # optional per-table FK array
        - from_column, to_table, to_column
      columns:
        - name, data_type, ordinal_position, nullable, default
          is_primary_key, is_foreign_key, is_distkey, sortkey_position, encoding
          references:                 # optional, when is_foreign_key=true
            table: <table_name>
            column: <column_name>

Design decisions:
- ALL 200 declared relationships are loaded as JOINS_TO edges unconditionally —
  including UUID-target joins which are manually verified valid.
- UUID filter (is_uuid_col) is NOT applied here. It applies only in fk_infer.py
  for inferred edges.
- uuid-named columns are stored as Column nodes with is_surrogate_key=True.
- No bidirectional dedup — keep both directions. _pass_is_canonical() handles priority.
- Confidence is derived from whether to_col is a PK on the target table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..models import ColumnMeta, FKEdge, TableMeta
from ..utils import is_uuid_col

_SCHEMA = "lpp"
_YML_PATH = Path(__file__).resolve().parents[2] / "output" / "lpp_semantic_model.yml"

_NUMERIC_TYPES = {
    "numeric", "decimal", "integer", "bigint", "smallint",
    "double precision", "real", "float", "int",
}
_GROUPABLE_TYPES = {
    "character varying", "varchar", "char", "character",
    "boolean", "date",
    "timestamp", "timestamp with time zone", "timestamp without time zone",
    "timestamptz",
}
_TEXT_TYPES = {"character varying", "varchar", "char", "character", "text", "nvarchar"}


def _fqn(table_name: str) -> str:
    return f"{_SCHEMA}.{table_name}"


def _dedup_pk_columns(raw_pks: list[str]) -> list[str]:
    """Remove duplicates from primary_keys array while preserving order."""
    return list(dict.fromkeys(raw_pks))


def _parse_distkey_col(dist_style: str) -> str:
    """Extract column name from dist_style like 'KEY(account_ref)' → 'account_ref'."""
    if "KEY(" in dist_style.upper():
        return dist_style.split("(")[-1].split(")")[0].strip()
    return ""


def _derive_is_measurable(col: dict) -> bool:
    dt = col.get("data_type", "").lower()
    base = dt.split("(")[0].strip()
    if base not in _NUMERIC_TYPES:
        return False
    if col.get("is_primary_key") or col.get("is_foreign_key"):
        return False
    return True


def _derive_is_groupable(col: dict) -> bool:
    dt = col.get("data_type", "").lower()
    base = dt.split("(")[0].strip()
    if col.get("is_primary_key"):
        return False
    return base in _GROUPABLE_TYPES


def _classify_confidence(to_col: str, to_table_pks: set[str],
                         from_dtype: str, to_dtype: str) -> tuple[float, str]:
    """Return (confidence, source) for a declared FK edge."""
    to_col_is_pk = to_col in to_table_pks
    from_is_text = from_dtype.split("(")[0].strip() in _TEXT_TYPES
    to_is_text   = to_dtype.split("(")[0].strip() in _TEXT_TYPES

    if to_col_is_pk:
        return 1.0, "declared_fk"
    if from_is_text and to_is_text:
        return 0.70, "declared_fk_weak"
    return 0.85, "declared_fk"


def parse(yml_path: Path = _YML_PATH) -> tuple[list[TableMeta], list[ColumnMeta], list[FKEdge]]:
    """
    Returns:
        tables  — one TableMeta per table (105 full tables)
        columns — all columns including surrogate uuid columns (marked is_surrogate_key)
        edges   — all 200 declared FK edges (no UUID filter, no bidirectional dedup)
    """
    with open(yml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    schema = data.get("schema", _SCHEMA)

    # ── Pass 1: build pk_map and col_type_map for confidence classification ───
    pk_map: dict[str, set[str]] = {}      # table_name → set of PK column names
    col_dtype_map: dict[tuple[str, str], str] = {}  # (table_name, col_name) → data_type

    for tbl in data.get("tables", []):
        tbl_name = tbl["name"]
        raw_pks = _dedup_pk_columns(tbl.get("primary_keys", []))
        pk_map[tbl_name] = set(raw_pks)
        for col in tbl.get("columns", []):
            col_dtype_map[(tbl_name, col["name"])] = (col.get("data_type") or "").lower()

    # ── Collect FK edges from all three YAML sources ─────────────────────────
    # No UUID filter — all 200 declared relationships loaded unconditionally.
    # No bidirectional dedup — keep both directions; _pass_is_canonical() handles priority.
    seen_keys: set[tuple[str, str, str, str]] = set()
    raw_edges: list[dict[str, Any]] = []

    def _add_fk(from_table: str, from_col: str, to_table: str, to_col: str):
        key = (from_table, from_col, to_table, to_col)
        if key in seen_keys:
            return
        seen_keys.add(key)

        from_dtype = col_dtype_map.get((from_table, from_col), "")
        to_dtype   = col_dtype_map.get((to_table,   to_col),   "")
        to_pks     = pk_map.get(to_table, set())
        confidence, source = _classify_confidence(to_col, to_pks, from_dtype, to_dtype)

        raw_edges.append({
            "from_table":   _fqn(from_table),
            "from_col":     from_col,
            "to_table":     _fqn(to_table),
            "to_col":       to_col,
            "confidence":   confidence,
            "source":       source,
            "to_col_is_pk": to_col in to_pks,
            "is_self_join": from_table == to_table,
        })

    # Source 1: top-level relationships array
    for rel in data.get("relationships", []):
        _add_fk(rel["from_table"], rel["from_column"],
                rel["to_table"],   rel["to_column"])

    # Source 2 & 3: per-table foreign_keys + column-level references
    for tbl in data.get("tables", []):
        tbl_name = tbl["name"]
        for fk in tbl.get("foreign_keys", []):
            _add_fk(tbl_name, fk["from_column"], fk["to_table"], fk["to_column"])
        for col in tbl.get("columns", []):
            if col.get("is_foreign_key") and col.get("references"):
                refs = col["references"]
                _add_fk(tbl_name, col["name"], refs["table"], refs.get("column", ""))

    # ── Build col_ref_map for ColumnMeta FK resolution ───────────────────────
    col_ref_map: dict[tuple[str, str], tuple[str, str]] = {}
    for item in raw_edges:
        from_tbl = item["from_table"].split(".")[-1]
        col_ref_map[(from_tbl, item["from_col"])] = (item["to_table"], item["to_col"])

    # ── Parse tables and columns ─────────────────────────────────────────────
    tables: list[TableMeta] = []
    columns: list[ColumnMeta] = []

    for tbl in data.get("tables", []):
        tbl_name = tbl["name"]
        tbl_fqn  = _fqn(tbl_name)
        tbl_schema = tbl.get("schema", schema)

        raw_pks = tbl.get("primary_keys", [])
        pk_cols_all = _dedup_pk_columns(raw_pks)
        # Keep all pk columns including uuid-named ones (they'll be flagged is_surrogate_key)
        pk_cols = pk_cols_all

        dist_style   = tbl.get("dist_style", "")
        distkey_col  = _parse_distkey_col(dist_style)
        is_view      = tbl_name.startswith("v_")

        tm = TableMeta(
            fqn=tbl_fqn,
            name=tbl_name,
            schema=tbl_schema,
            row_count=tbl.get("row_count") or 0,
            diststyle=dist_style,
            distkey_col=distkey_col,
            pk_columns=pk_cols,
            is_view=is_view,
        )
        tables.append(tm)

        # ALL columns — including uuid-named ones (marked is_surrogate_key below)
        for col in tbl.get("columns", []):
            col_name  = col["name"]
            data_type = col.get("data_type", "")
            is_pk     = bool(col.get("is_primary_key", False))
            is_fk     = bool(col.get("is_foreign_key", False))
            nullable  = col.get("nullable", True)
            if nullable is None:
                nullable = True

            # Surrogate UUID key — no join or aggregation semantics
            is_surrogate = (col_name == "uuid" or col_name.endswith("_uuid")) and is_pk

            ref_table_fqn = ""
            ref_col = ""
            refs = col.get("references")
            if refs:
                ref_table_fqn = _fqn(refs["table"])
                ref_col = refs.get("column", "")
            elif (tbl_name, col_name) in col_ref_map:
                ref_table_fqn, ref_col = col_ref_map[(tbl_name, col_name)]

            # is_surrogate_fk: FK column that references a uuid surrogate PK on the target table
            is_surrogate_fk = is_fk and is_uuid_col(ref_col) if ref_col else False

            cm = ColumnMeta(
                table_fqn=tbl_fqn,
                name=col_name,
                data_type=data_type,
                ordinal_position=col.get("ordinal_position", 0),
                is_nullable=nullable,
                is_pk=is_pk,
                is_foreign_key=is_fk,
                is_surrogate_key=is_surrogate,
                is_surrogate_fk=is_surrogate_fk,
                referenced_table_fqn=ref_table_fqn,
                referenced_column=ref_col,
                is_measurable=_derive_is_measurable(col) and not is_surrogate,
                is_groupable=_derive_is_groupable(col) and not is_surrogate,
            )
            columns.append(cm)

    # ── Build FKEdge objects ──────────────────────────────────────────────────
    edges: list[FKEdge] = [
        FKEdge(
            from_table=item["from_table"],
            from_col=item["from_col"],
            to_table=item["to_table"],
            to_col=item["to_col"],
            confidence=item["confidence"],
            source=item["source"],
            is_declared=True,
            is_ontology=False,
            to_col_is_pk=item["to_col_is_pk"],
            is_self_join=item["is_self_join"],
        )
        for item in raw_edges
    ]

    return tables, columns, edges
