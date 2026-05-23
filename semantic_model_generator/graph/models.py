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
    table_type_db: str = ""
    row_count: int = 0
    size_mb: float = 0.0
    diststyle: str = ""
    distkey_col: str = ""
    sortkey1: str = ""
    sortkey_type: str = "NONE"
    encoded_pct: float = 0.0
    ontology_class: str = ""
    table_type: str = ""
    type_confidence: float = 0.0
    business_domain: str = ""
    description: str = ""
    wcc_component_id: int = -1
    community_id: int = -1
    pagerank_score: float = 0.0
    betweenness_score: float = 0.0
    cohere_embedding: list[float] = field(default_factory=list)
    is_isolated: bool = False
    is_weakly_bridged: bool = False
    source_hash: str = ""
    version: int = 1
    enrichment_status: str = "pending"

    def compute_source_hash(self, column_names: list[str]) -> str:
        return _md5({
            "row_count": self.row_count,
            "size_mb": round(self.size_mb, 1),
            "diststyle": self.diststyle,
            "distkey_col": self.distkey_col,
            "sortkey1": self.sortkey1,
            "encoded_pct": round(self.encoded_pct, 1),
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
    is_notnull: bool = False
    sample_values: list[str] = field(default_factory=list)
    description: str = ""
    semantic_type: str = ""
    synonyms: list[str] = field(default_factory=list)
    synonyms_text: str = ""
    is_pii: bool = False
    pii_type: str = ""
    top_freq_values: list[str] = field(default_factory=list)
    temporal_grain: str = ""
    default_aggregation: str = ""
    value_aliases: list[str] = field(default_factory=list)
    value_scale: str = ""
    cohere_embedding: list[float] = field(default_factory=list)
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
    is_ontology: bool = False
    is_wcc_bridge: bool = False
    predicate: str = ""
    frequency: int = 1

    @property
    def join_cost(self) -> float:
        return round(1.0 / self.confidence, 4)

    @property
    def leiden_weight(self) -> float:
        if self.is_ontology:
            return 2.0
        if self.source == "query_history":
            return 2.0
        if self.is_declared:
            return 1.5
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
