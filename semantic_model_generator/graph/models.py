from dataclasses import dataclass, field
import hashlib
import json


def _md5(data: dict) -> str:
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass
class TableMeta:
    fqn: str
    name: str
    schema: str
    row_count: int = 0
    diststyle: str = ""
    distkey_col: str = ""
    sortkey1: str = ""
    ontology_class: str = ""
    table_type: str = ""
    business_domain: str = ""
    description: str = ""
    # Structural properties derived from YAML/Redshift (no LLM)
    grain: str = ""
    typical_join_role: str = ""
    is_time_series: bool = False
    natural_dimensions: list[str] = field(default_factory=list)
    natural_measures: list[str] = field(default_factory=list)
    column_count: int = 0
    pk_columns: list[str] = field(default_factory=list)
    is_view: bool = False
    # Hub / cross-domain
    is_dimension_hub: bool = False
    hub_join_col: str = ""
    # Seasonality
    has_seasonality_pattern: bool = False
    typical_lookback_days: int = 0
    # GDS algorithm outputs
    in_degree: int = 0
    out_degree: int = 0
    triangle_count: int = 0
    scc_id: str = ""
    community_id: int = -1
    pagerank_score: float = 0.0
    betweenness_score: float = 0.0
    cohere_embedding: list[float] = field(default_factory=list)
    # Pipeline internal
    source_hash: str = ""
    version: int = 1
    enrichment_status: str = "pending"

    def compute_source_hash(self, column_names: list[str]) -> str:
        return _md5({
            "row_count": self.row_count,
            "diststyle": self.diststyle,
            "distkey_col": self.distkey_col,
            "sortkey1": self.sortkey1,
            "columns": sorted(column_names),
        })


@dataclass
class ColumnMeta:
    table_fqn: str
    name: str
    data_type: str
    ordinal_position: int
    is_nullable: bool = True
    column_default: str | None = None
    null_frac: float = 0.0
    n_distinct: float = 0.0
    most_common_vals: list[str] = field(default_factory=list)
    most_common_freqs: list[float] = field(default_factory=list)
    is_pk: bool = False
    # FK metadata from YAML
    is_foreign_key: bool = False
    is_surrogate_key: bool = False      # uuid-named PK — no join semantics
    is_surrogate_fk: bool = False       # is a FK reference column
    referenced_table_fqn: str = ""
    referenced_column: str = ""
    # Value data (from Redshift stats + queries)
    sample_values: list[str] = field(default_factory=list)
    top_freq_values: list[str] = field(default_factory=list)
    distinct_values: list[str] = field(default_factory=list)
    # Derived structural properties (no LLM)
    is_measurable: bool = False
    is_groupable: bool = False
    has_data: bool = True               # null_frac < 0.95 AND n_distinct != 0
    filter_selectivity: str = ""
    value_vocabulary: list[str] = field(default_factory=list)
    # LLM-enriched natural-language properties
    description: str = ""
    semantic_type: str = ""
    synonyms: list[str] = field(default_factory=list)
    is_pii: bool = False
    pii_type: str = ""
    temporal_grain: str = ""
    default_aggregation: str = ""
    value_aliases: list[str] = field(default_factory=list)  # ["DB_VALUE -> business label", ...]
    cohere_embedding: list[float] = field(default_factory=list)
    # Pipeline internal
    source_hash: str = ""
    version: int = 1
    enrichment_status: str = "pending"

    @property
    def id(self) -> str:
        return f"{self.table_fqn}.{self.name}"

    def compute_source_hash(self) -> str:
        return _md5({
            "data_type": self.data_type,
            "is_nullable": self.is_nullable,
            "null_frac": round(self.null_frac, 2),
            "n_distinct": round(self.n_distinct, 2),
            "sample_vals": self.most_common_vals[:5],
        })


@dataclass
class FKEdge:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    confidence: float
    source: str
    is_declared: bool = False
    is_ontology: bool = False       # kept for backward compat, functionally replaced by source
    is_wcc_bridge: bool = False
    is_inferred: bool = False
    to_col_is_pk: bool = True       # whether to_col is a PK on the target table
    is_self_join: bool = False      # from_table == to_table
    from_col_null_frac: float = 0.0
    join_likely_sparse: bool = False  # from_col_null_frac > 0.5
    predicate: str = ""
    frequency: int = 1

    @property
    def join_cost(self) -> float:
        return round(1.0 / max(self.confidence, 0.01), 4)

    @property
    def leiden_weight(self) -> float:
        if self.source == "query_history":
            return 2.0
        if self.is_declared and self.to_col_is_pk:
            return 1.5
        if self.is_declared:
            return 1.0
        if self.is_wcc_bridge:
            return 0.6
        return round(self.confidence * 1.5, 3)

    def compute_source_hash(self) -> str:
        return _md5({
            "from_fqn": self.from_table,
            "from_col": self.from_col,
            "to_fqn": self.to_table,
            "to_col": self.to_col,
            "confidence": round(self.confidence, 3),
            "source": self.source,
        })
