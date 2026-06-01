"""
Schema contract — shared lists of properties returned to the agent at retrieval time.

Both neo4j_loader.py (what to SET on nodes) and neo4j_client.py (what to RETURN
in queries) import from this module. Any property change must be reflected in both.
"""

TABLE_RETRIEVAL_PROPS: list[str] = [
    "fqn", "name", "schema",
    "row_count",
    "grain",
    "table_type", "typical_join_role",
    "business_domain", "ontology_class",
    "description", "synonyms",
    "natural_dimensions", "natural_measures",
    "is_time_series", "time_dimension_col", "time_dimension_grain",
    "has_seasonality_pattern", "typical_lookback_days",
    "intent_tags",
    "community_id",
    "betweenness_score", "pagerank_score", "in_degree",
    "is_dimension_hub", "hub_join_col",
    "pk_columns",
    "is_view",
]

COLUMN_RETRIEVAL_PROPS: list[str] = [
    "id", "name", "table_fqn",
    "data_type",
    "is_pk", "is_nullable",
    "is_foreign_key", "is_surrogate_key",
    "is_measurable", "is_groupable",
    "filter_selectivity",
    "null_frac", "n_distinct",
    "has_data",
    "semantic_type",
    "description", "synonyms",
    "temporal_grain", "default_aggregation",
    "is_pii", "pii_type",
    "value_vocabulary", "value_aliases",
    "sample_values", "top_freq_values", "distinct_values",
]

JOINPATH_RETRIEVAL_PROPS: list[str] = [
    "id", "from_fqn", "to_fqn",
    "algorithm", "k_rank",
    "join_clauses", "path_tables",
    "hop_count", "quality_score",
    "is_cross_community",
]

JOINSTO_RETRIEVAL_PROPS: list[str] = [
    "from_col", "to_col", "from_table", "to_table",
    "confidence", "source",
    "is_declared", "is_inferred", "is_canonical",
    "recommended_join_type",
    "to_col_is_pk", "is_self_join",
    "ambiguity_risk", "frequency",
    "from_col_null_frac", "join_likely_sparse",
]

QUERYTEMPLATE_RETRIEVAL_PROPS: list[str] = [
    "id", "question_text", "description",
    "primary_intent", "intent_scores",
    "complexity",
    "anchor_table_fqns_resolved",  # resolved only — never raw anchor_table_fqns
    "cte_steps", "required_aggregations", "required_filters",
    "time_windowed",
    "is_cross_domain", "sql_pattern",
    "min_cte_count", "max_cte_count",
    "template_confidence", "is_validated", "source",
]

INTENT_RETRIEVAL_PROPS: list[str] = [
    "name", "description",
]

BUSINESSTERM_RETRIEVAL_PROPS: list[str] = [
    "term", "variants", "term_type", "description",
    "related_table_fqns", "term_category",
]

COMMUNITY_RETRIEVAL_PROPS: list[str] = [
    "id", "description", "dominant_domain", "dominant_domain_confidence", "table_count",
]

BRIDGES_TO_RETRIEVAL_PROPS: list[str] = [
    "bridge_type", "hub_table_fqn", "hub_join_col",
    "shared_dimension_columns", "join_safe", "bridge_count",
]
