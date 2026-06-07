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

    @field_validator("table_fqn", "column_name", "alias", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        """LLM sometimes emits a single-element list instead of a string."""
        if isinstance(v, list):
            return str(v[0]) if v else ""
        return str(v) if v is not None else ""

    @field_validator("aggregation", mode="before")
    @classmethod
    def _coerce_aggregation(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            v = v[0] if v else None
        if not v or str(v).upper() in ("NONE", "NULL", ""):
            return None
        return str(v).upper()

    @field_validator("semantic_type", mode="before")
    @classmethod
    def _coerce_semantic_type(cls, v):
        if not v or (isinstance(v, list) and not v):
            return "dimension"
        if isinstance(v, list):
            v = v[0]
        return str(v).lower() if v else "dimension"


class FilterSpec(BaseModel):
    table_fqn: str
    column_name: str
    operator: str = "="                 # =, >=, <=, IN, BETWEEN, BETWEEN_SQL, LIKE
    value: str | list[str] = ""
    raw_user_value: str = ""
    resolved: bool = False
    is_raw_sql: bool = False            # when True, value is a SQL expression — written unquoted
    is_having: bool = False             # when True, condition goes in HAVING not WHERE

    @field_validator("table_fqn", "column_name", mode="before")
    @classmethod
    def _coerce_str_field(cls, v):
        if isinstance(v, list):
            return str(v[0]) if v else ""
        return str(v) if v is not None else ""

    @field_validator("operator", mode="before")
    @classmethod
    def _coerce_operator(cls, v):
        if isinstance(v, list):
            v = v[0] if v else "="
        op = str(v).strip().upper() if v else "="
        # Normalize "BETWEEN" to "BETWEEN_SQL" when paired with SQL expressions
        # The actual decision is deferred — keep both forms; downstream renders correctly.
        return op if op else "="

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v):
        # None → empty string
        if v is None:
            return ""
        # Nested list [[a, b]] → [a, b]
        if isinstance(v, list) and len(v) == 1 and isinstance(v[0], list):
            return v[0]
        return v

    @field_validator("raw_user_value", mode="before")
    @classmethod
    def _coerce_raw_user_value(cls, v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v) if v is not None else ""

    @field_validator("resolved", "is_raw_sql", "is_having", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)


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

    @field_validator("anchor_tables", "join_clauses", "path_tables", "join_types",
                     "join_path_ids", "cte_steps", "order_by", mode="before")
    @classmethod
    def _coerce_str_list(cls, v):
        """Wrap bare string in list; flatten nested lists; drop empty/None items."""
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            out = []
            for item in v:
                if isinstance(item, list):
                    out.extend(str(x) for x in item if x)
                elif item is not None and str(item).strip():
                    out.append(str(item))
            return out
        return list(v)

    @field_validator("temporal_grains", mode="before")
    @classmethod
    def _flatten_temporal_grains(cls, v):
        """Accept temporal_grains (list) OR temporal_grain (str/null) from older prompt format.
        Flatten nested lists — LLM occasionally emits [["month"]] instead of ["month"].
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if not isinstance(v, list):
            return []
        result = []
        for item in v:
            if isinstance(item, list):
                result.extend(str(x) for x in item if x)
            elif item:
                result.append(str(item))
        return result

    @field_validator("limit", mode="before")
    @classmethod
    def _coerce_limit(cls, v):
        if v is None:
            return None
        try:
            return int(float(str(v)))
        except (ValueError, TypeError):
            return None

    @field_validator("complexity", mode="before")
    @classmethod
    def _coerce_complexity(cls, v):
        valid = {"simple", "complex", "advanced"}
        s = str(v).lower().strip() if v else "simple"
        return s if s in valid else "simple"

    @field_validator("result_shape", mode="before")
    @classmethod
    def _coerce_result_shape(cls, v):
        valid = {"kpi", "table", "ratio", "time_series", "comparison"}
        if v is None:
            return None
        s = str(v).lower().strip()
        return s if s in valid else "table"

    # Extended IR fields for complex queries
    derived_measures: list[DerivedMeasure] = Field(default_factory=list)
    threshold_specs: list[ThresholdSpec] = Field(default_factory=list)
    cte_chain: list[CTEStepSpec] = Field(default_factory=list)  # structured; replaces cte_steps list[str]
    cte_steps: list[str] = Field(default_factory=list)          # legacy hints — kept for backward compat

    # Auto-inferred dimensions (from entity_hints + time_filter context)
    inferred_dimensions: list[ColumnRef] = Field(default_factory=list)

    # Explicit failure signals — nodes downstream read these instead of guessing from empty strings
    unresolved_join_pairs: list[dict] = Field(default_factory=list)  # {from_fqn, to_fqn, reason}
    hallucinated_columns: list[str] = Field(default_factory=list)    # "table.col" pairs dropped by ir_validation

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
    was_truncated: bool = False          # True when result exceeded the row cap
    true_total_rows: int | None = None   # COUNT(*) from full result (no LIMIT)
    stats_source: str = "capped"         # "full_result" | "capped"
