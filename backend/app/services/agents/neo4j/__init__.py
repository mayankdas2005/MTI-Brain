"""Neo4j graph client — split into single-responsibility modules.

Import from here for backward compat, or import from submodules directly.
"""

from .client import (
    init_neo4j,
    close_neo4j,
    get_driver,
    _neo4j_run,
    _neo4j_run_single,
    _neo4j_write,
)
from .table_search import (
    search_tables_vector,
    search_tables_fulltext,
    search_tables_via_intents,
    search_tables_via_community,
    search_tables_via_domain,
    search_tables_via_joinpaths,
    search_tables_via_business_terms,
    search_tables_via_columns,
    search_tables_from_query_patterns,
    search_tables_via_filter_values,
    get_tables_with_context,
    get_table_relevant_intents,
    get_structurally_similar_tables,
    get_community_bridges,
    get_business_term_table_edges,
    search_tables_via_query_templates,
    get_business_terms_with_related_tables,
)
from .column_search import (
    search_columns_vector,
    search_columns_fulltext,
    get_columns_for_tables,
    get_columns_by_ids,
    get_join_critical_columns,
    get_semantically_similar_columns,
    resolve_columns,
    find_join_by_value_overlap,
)
from .join_resolution import (
    load_join_path,
    load_join_path_yens,
    load_join_path_dijkstra,
    collect_all_join_paths,
    load_best_join_path,
    get_direct_joins,
    get_join_paths_by_ids,
    search_join_path_by_semantics,
    get_joinpath_joins,
    find_bridge_table,
)
from .hub_detection import (
    get_dimension_hub_for_communities,
    find_common_dimension_hub,
)
from .template_search import (
    search_query_templates,
    search_query_templates_fulltext,
    search_query_patterns,
    search_query_patterns_fulltext,
    search_anti_patterns,
    search_intents,
    search_intents_fulltext,
    search_business_terms_vector,
    search_business_terms_fulltext,
    lookup_business_terms,
    get_query_templates_by_ids,
    get_query_patterns_by_ids,
    get_anti_patterns_by_ids,
    get_business_terms_by_terms,
    get_all_domain_names,
    get_all_intent_names,
)
from .write import (
    write_join_path,
    write_query_pattern,
    write_anti_pattern,
    update_pattern_feedback,
    promote_pattern_to_template,
    write_schema_gap,
)

__all__ = [
    "init_neo4j", "close_neo4j", "get_driver",
    "search_tables_vector", "search_tables_fulltext",
    "search_tables_via_intents", "search_tables_via_community",
    "search_tables_via_domain", "search_tables_via_joinpaths",
    "search_tables_via_business_terms", "search_tables_via_columns",
    "search_tables_from_query_patterns", "search_tables_via_filter_values",
    "get_tables_with_context", "get_table_relevant_intents",
    "get_structurally_similar_tables", "get_community_bridges", "get_business_term_table_edges",
    "search_tables_via_query_templates", "get_business_terms_with_related_tables",
    "search_columns_vector", "search_columns_fulltext",
    "get_columns_for_tables", "get_columns_by_ids", "get_join_critical_columns",
    "get_semantically_similar_columns", "resolve_columns", "find_join_by_value_overlap",
    "load_join_path", "load_join_path_yens", "load_join_path_dijkstra",
    "collect_all_join_paths", "load_best_join_path", "get_direct_joins",
    "get_join_paths_by_ids", "search_join_path_by_semantics", "get_joinpath_joins",
    "find_bridge_table",
    "get_dimension_hub_for_communities", "find_common_dimension_hub",
    "search_query_templates", "search_query_templates_fulltext",
    "search_query_patterns", "search_query_patterns_fulltext",
    "search_anti_patterns", "search_intents", "search_intents_fulltext",
    "search_business_terms_vector", "search_business_terms_fulltext",
    "lookup_business_terms",
    "get_query_templates_by_ids", "get_query_patterns_by_ids",
    "get_anti_patterns_by_ids", "get_business_terms_by_terms",
    "get_all_domain_names", "get_all_intent_names",
    "write_join_path", "write_query_pattern", "write_anti_pattern",
    "update_pattern_feedback", "promote_pattern_to_template", "write_schema_gap",
]
