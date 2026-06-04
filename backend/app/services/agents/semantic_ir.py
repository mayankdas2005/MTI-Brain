"""SemanticIR — the intermediate representation between intent and SQL.

Produced by query_compiler, consumed by executor.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


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


class DerivedMeasure(BaseModel):
    """A computed measure that requires an expression, not just a column aggregation."""
    alias: str
    expression: str                     # SQL expression: "forecast_amount * seasonality_factor"
    aggregation: str = "NONE"           # SUM/AVG/NONE — applied after expression
    semantic_type: str = "measure"


class ThresholdSpec(BaseModel):
    """A comparison threshold against a computed position — expressed as a CTE CASE WHEN."""
    expression: str                     # computed expression: "cumulative_net_position"
    operator: str                       # <, >, <=, >=
    value: float                        # 200000000.0
    label: str                          # "below_threshold" — boolean flag column alias
    is_having: bool = False


class CTEStepSpec(BaseModel):
    """One named CTE in a structured CTE chain."""
    name: str                           # CTE alias used in WITH clause
    description: str                    # plain English: what this CTE computes
    source_ctes: list[str] = Field(default_factory=list)   # upstream CTE names (empty = reads real tables)
    group_by_aliases: list[str] = Field(default_factory=list)
    select_expressions: list[str] = Field(default_factory=list)  # may include window functions, CASE WHEN


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

    # temporal_grains replaces the single temporal_grain string.
    # Ordered most-granular first: ["week", "month"] for dual horizon, ["day"] for single.
    temporal_grains: list[str] = Field(default_factory=list)

    @field_validator("temporal_grains", mode="before")
    @classmethod
    def _flatten_temporal_grains(cls, v):
        """Flatten nested lists — LLM occasionally emits [["month"]] instead of ["month"]."""
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            if isinstance(item, list):
                result.extend(str(x) for x in item if x)
            elif item:
                result.append(str(item))
        return result

    # Extended IR fields for complex queries
    derived_measures: list[DerivedMeasure] = Field(default_factory=list)
    threshold_specs: list[ThresholdSpec] = Field(default_factory=list)
    cte_chain: list[CTEStepSpec] = Field(default_factory=list)  # structured; replaces cte_steps list[str]
    cte_steps: list[str] = Field(default_factory=list)          # legacy hints — kept for backward compat

    # Auto-inferred dimensions (from entity_hints + time_filter context)
    inferred_dimensions: list[ColumnRef] = Field(default_factory=list)

    # Explicit failure signals — nodes downstream read these instead of guessing from empty strings
    unresolved_join_pairs: list[dict] = Field(default_factory=list)  # {from_fqn, to_fqn, reason}

    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    sub_query_index: int | None = None
    candidate_join_paths: list[dict] | None = None  # all tiers per table pair

    @property
    def temporal_grain(self) -> str | None:
        """Backward-compat: return first grain from temporal_grains list."""
        return self.temporal_grains[0] if self.temporal_grains else None


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
    std: float | None = None
    p25: float | None = None
    p75: float | None = None
    mode: str | float | None = None
    top_values: list[tuple[str, int]] | None = None  # string cols only


class QuerySummary(BaseModel):
    total_rows: int = 0
    columns: list[ColumnStat] = Field(default_factory=list)
    sample_rows: list[dict] = Field(default_factory=list)
    result_shape: str = "flat"          # time_series | ranking | cross_tab | kpi | flat
    reliability_flags: list[str] = Field(default_factory=list)
    result_label: str | None = None
