"""SemanticIR — the intermediate representation between intent and SQL.

Produced by query_compiler, consumed by executor.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnRef(BaseModel):
    table_fqn: str
    column_name: str
    alias: str
    aggregation: str | None = None      # SUM/AVG/COUNT/NONE — SQL LLM decides when null
    semantic_type: str = "dimension"    # amount/percentage/measure/dimension/date/identifier/code/flag/ratio


class FilterSpec(BaseModel):
    table_fqn: str
    column_name: str
    operator: str = "="                 # =, >=, <=, IN, BETWEEN, BETWEEN_SQL, LIKE
    value: str | list[str] = ""
    raw_user_value: str = ""
    resolved: bool = False
    is_raw_sql: bool = False            # when True, value is a SQL expression — written unquoted
    is_having: bool = False             # when True, condition goes in HAVING not WHERE


class SemanticIR(BaseModel):
    template_id: str = ""
    intent: str = ""
    complexity: str = "simple"
    result_shape: str | None = None     # kpi | table | ratio | time_series | comparison — drives SQL structure
    anchor_tables: list[str] = Field(default_factory=list)
    join_path_ids: list[str] = Field(default_factory=list)
    join_clauses: list[str] = Field(default_factory=list)
    path_tables: list[str] = Field(default_factory=list)
    join_types: list[str] = Field(default_factory=list)
    measures: list[ColumnRef] = Field(default_factory=list)
    dimensions: list[ColumnRef] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_filter: FilterSpec | None = None
    temporal_grain: str | None = None
    cte_steps: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    sub_query_index: int | None = None
    candidate_join_paths: list[dict] | None = None  # all tiers per table pair: {from_fqn, to_fqn, tier, direction, join_clauses, path_tables, hop_count}


class ColumnStat(BaseModel):
    name: str
    dtype: str = ""
    null_count: int = 0
    total_count: int = 0
    distinct_count: int = 0
    min: float | str | None = None
    max: float | str | None = None
    mean: float | None = None
    median: float | None = None
    mode: str | float | None = None
    top_values: list[tuple[str, int]] | None = None  # string cols only


class QuerySummary(BaseModel):
    total_rows: int = 0
    columns: list[ColumnStat] = Field(default_factory=list)
    sample_rows: list[dict] = Field(default_factory=list)
    result_shape: str = "flat"          # time_series | ranking | cross_tab | kpi | flat
    reliability_flags: list[str] = Field(default_factory=list)
    result_label: str | None = None
