"""SemanticIR — the intermediate representation between intent and SQL.

Produced by query_compiler, consumed by sql_compiler and executor.
"""

from __future__ import annotations

from pydantic import BaseModel


class ColumnRef(BaseModel):
    table_fqn: str
    column_name: str
    alias: str
    aggregation: str | None    # SUM/AVG/COUNT/NONE from Column.default_aggregation
    semantic_type: str         # amount/percentage/measure/dimension/date/identifier/code/flag/ratio


class FilterSpec(BaseModel):
    table_fqn: str
    column_name: str
    operator: str              # =, >=, <=, IN, BETWEEN, BETWEEN_SQL, LIKE
    value: str | list[str]
    raw_user_value: str
    resolved: bool
    is_raw_sql: bool = False   # when True, value is a SQL expression — written unquoted
    is_having: bool = False    # when True, condition goes in HAVING not WHERE


class SemanticIR(BaseModel):
    template_id: str
    intent: str
    complexity: str
    anchor_tables: list[str]
    join_path_ids: list[str]   # one per consecutive table pair — length = len(anchor_tables)-1
    join_clauses: list[str]    # ["account_ref = code"] — col names only, no table prefix
    path_tables: list[str]     # full ordered list of tables across all join paths
    join_types: list[str]
    measures: list[ColumnRef]
    dimensions: list[ColumnRef]
    filters: list[FilterSpec]
    time_filter: FilterSpec | None
    temporal_grain: str | None
    cte_steps: list[str]       # CTE name prefix from QueryTemplate.cte_steps (suggestive only)
    order_by: list[str]
    limit: int | None
    sub_query_index: int | None
    depends_on: int | None
    merge_key: list[str] | None
    merge_strategy: str | None


class ColumnStat(BaseModel):
    name: str
    dtype: str
    null_count: int
    total_count: int
    distinct_count: int
    min: float | str | None
    max: float | str | None
    mean: float | None
    median: float | None
    mode: str | float | None
    top_values: list[tuple[str, int]] | None  # string cols only


class QuerySummary(BaseModel):
    total_rows: int
    columns: list[ColumnStat]
    sample_rows: list[dict]    # ≤20 rows
    result_shape: str          # time_series | ranking | cross_tab | kpi | flat
    reliability_flags: list[str]
    result_label: str | None
