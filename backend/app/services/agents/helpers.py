"""Streaming helpers for the MTI Brain analytics pipeline."""

from __future__ import annotations

import re
from collections import Counter


# ─── neo4j_raw_graph accumulation helper ─────────────────────────────────────

def _raw_node_key(node: dict) -> str:
    label = node.get("_label", "")
    if label == "Table":
        return f"Table:{node.get('fqn', '')}"
    if label == "Column":
        return f"Column:{node.get('table_fqn', '')}.{node.get('name', '')}"
    if label == "BusinessTerm":
        return f"BusinessTerm:{node.get('term', '')}"
    if label == "Intent":
        return f"Intent:{node.get('name', '')}"
    if label == "QueryTemplate":
        return f"QueryTemplate:{node.get('id', '')}"
    if label == "QueryPattern":
        return f"QueryPattern:{node.get('id', '')}"
    if label == "AntiPattern":
        return f"AntiPattern:{node.get('id', '')}"
    if label == "JoinPath":
        return f"JoinPath:{node.get('id') or node.get('from_fqn', '') + '→' + node.get('to_fqn', '')}"
    if label == "Community":
        return f"Community:{node.get('id', '')}"
    if label == "Domain":
        return f"Domain:{node.get('name', '')}"
    return f"{label}:{id(node)}"


def _raw_edge_key(edge: dict) -> str:
    t = edge.get("_type", "")
    if t == "JOINS_TO":
        return f"JOINS_TO:{edge.get('from_fqn','')}:{edge.get('to_fqn','')}:{edge.get('from_col','')}:{edge.get('to_col','')}"
    if t == "BRIDGES_TO":
        return f"BRIDGES_TO:{edge.get('from_community_id','')}:{edge.get('to_community_id','')}"
    if t == "HAS_COLUMN":
        return f"HAS_COLUMN:{edge.get('table_fqn','')}:{edge.get('column_name','')}"
    if t == "REFERENCES_TABLE":
        return f"REFERENCES_TABLE:{edge.get('term','')}:{edge.get('table_fqn','')}"
    if t == "RELEVANT_TO":
        return f"RELEVANT_TO:{edge.get('table_fqn','')}:{edge.get('intent_name','')}"
    if t == "CONTAINS_TABLE":
        return f"CONTAINS_TABLE:{edge.get('community_id','')}:{edge.get('table_fqn','')}"
    if t == "BELONGS_TO":
        return f"BELONGS_TO:{edge.get('table_fqn','')}:{edge.get('domain_name','')}"
    if t == "REQUIRES_TABLE":
        return f"REQUIRES_TABLE:{edge.get('template_id','')}:{edge.get('table_fqn','')}"
    if t == "SEMANTICALLY_SIMILAR":
        a, b = edge.get("from_col_id", ""), edge.get("to_col_id", "")
        return f"SEMANTICALLY_SIMILAR:{min(a,b)}:{max(a,b)}"
    if t == "STRUCTURALLY_SIMILAR":
        a, b = edge.get("from_fqn", ""), edge.get("to_fqn", "")
        return f"STRUCTURALLY_SIMILAR:{min(a,b)}:{max(a,b)}"
    return f"{t}:{edge}"


def merge_neo4j_raw_graph(
    existing: dict,
    new_nodes: list[dict],
    new_edges: list[dict],
) -> dict:
    """Append new_nodes/new_edges into existing neo4j_raw_graph, deduplicating by stable key.

    Each node must carry a '_label' field; each edge must carry a '_type' field.
    Returns a new dict with merged nodes and edges lists.
    """
    nodes: list[dict] = list(existing.get("nodes") or [])
    edges: list[dict] = list(existing.get("edges") or [])

    seen_nodes = {_raw_node_key(n) for n in nodes}
    for n in (new_nodes or []):
        k = _raw_node_key(n)
        if k not in seen_nodes:
            nodes.append(n)
            seen_nodes.add(k)

    seen_edges = {_raw_edge_key(e) for e in edges}
    for e in (new_edges or []):
        k = _raw_edge_key(e)
        if k not in seen_edges:
            edges.append(e)
            seen_edges.add(k)

    return {"nodes": nodes, "edges": edges}


# ─── FilterSpec.value rendering helpers ──────────────────────────────────────
# FilterSpec.value is typed str | list[str].
#   str   → single-bound operators: =, >=, <=, >, <, LIKE, ILIKE
#   list  → two-element BETWEEN / BETWEEN_SQL: ["start_expr", "end_expr"]
#           OR multi-element IN:               ["val1", "val2", ...]
# Every consumer must go through these helpers — never call .replace() or
# f-string-interpolate a value directly without checking its type first.

def render_filter_value(operator: str, value) -> str:
    """Render a FilterSpec (operator, value) pair as a SQL-ready clause fragment.

    Examples
    --------
    render_filter_value(">=", "DATEADD(day,-60,CURRENT_DATE)")
        → ">= DATEADD(day,-60,CURRENT_DATE)"

    render_filter_value("BETWEEN_SQL", ["CURRENT_DATE", "DATEADD(day,90,CURRENT_DATE)"])
        → "BETWEEN CURRENT_DATE AND DATEADD(day,90,CURRENT_DATE)"

    render_filter_value("IN", ["USD", "EUR"])
        → "IN ('USD', 'EUR')"
    """
    op = (operator or "=").upper()
    if op in ("BETWEEN", "BETWEEN_SQL"):
        if isinstance(value, list) and len(value) == 2:
            return f"BETWEEN {value[0]} AND {value[1]}"
        v = value[0] if isinstance(value, list) else value
        return f"BETWEEN {v} AND {v}"
    if op == "IN" or isinstance(value, list):
        vals = value if isinstance(value, list) else [value]
        quoted = ", ".join(f"'{v}'" for v in vals)
        return f"IN ({quoted})"
    v = value if isinstance(value, str) else str(value)
    if op == "ILIKE":
        return f"ILIKE '{v}'"
    if op == "LIKE":
        return f"LIKE '{v}'"
    return f"{operator} {v}"


def apply_stale_fallback(operator: str, value, col_name: str, table_fqn: str):
    """Return a LEAST(CURRENT_DATE, MAX(col))-anchored copy of value for stale-data safety.

    Replaces CURRENT_DATE with LEAST(CURRENT_DATE, (SELECT MAX(col_name)::DATE FROM table_fqn)).
    This single anchor date handles both cases correctly:
      - Stale data (MAX < CURRENT_DATE): LEAST = MAX → window anchored to latest available data.
      - Future-dated rows (MAX > CURRENT_DATE): LEAST = CURRENT_DATE → caps at today, no future leak.

    Returns None when no CURRENT_DATE is present (no substitution needed).
    """
    least_expr = f"LEAST(CURRENT_DATE, (SELECT MAX({col_name})::DATE FROM {table_fqn}))"

    if isinstance(value, list):
        # BETWEEN_SQL with two bounds — replace in both
        replaced = [v.replace("CURRENT_DATE", least_expr) if isinstance(v, str) else str(v)
                    for v in value]
        if replaced == list(value):
            return None  # nothing changed — no CURRENT_DATE to replace
        return replaced

    if not isinstance(value, str):
        return None
    if "CURRENT_DATE" not in value:
        return None
    return value.replace("CURRENT_DATE", least_expr)


def _strip_dead_ctes(parsed) -> None:
    """Remove CTEs that are defined but never referenced downstream (sqlglot AST in-place)."""
    from sqlglot import exp
    with_clause = parsed.args.get("with")
    if not with_clause:
        return
    cte_aliases = {cte.alias for cte in with_clause.expressions}
    if not cte_aliases:
        return
    table_refs = {t.name for t in parsed.find_all(exp.Table)}
    dead = cte_aliases - table_refs
    if not dead:
        return
    live = [cte for cte in with_clause.expressions if cte.alias not in dead]
    if live:
        with_clause.set("expressions", live)
    else:
        parsed.args.pop("with", None)


def format_sql(sql: str) -> str:
    """Pretty-print SQL using sqlglot (Redshift dialect). Falls back to stripped string on parse error."""
    if not sql.strip():
        return sql
    try:
        import sqlglot
        parsed = sqlglot.parse_one(
            sql, read="redshift", error_level=sqlglot.ErrorLevel.IGNORE
        )
        if parsed:
            _strip_dead_ctes(parsed)
            return parsed.sql(dialect="redshift", pretty=True)
    except Exception:
        pass
    return sql.strip()




def _build_data_summary(
    columns: list[str],
    rows: list[list],
) -> tuple[str, str, list[list]]:
    """Compute per-column stats and a representative row sample for LLM prompts.

    Returns:
        col_stats    — one line per column: type, range/top-values, null rate
        null_notes   — human-readable note for columns with >20% nulls (empty string if none)
        sampled_rows — spread-sampled rows capped at 20, as raw lists
    """
    import pandas as pd

    if not columns or not rows:
        return "", "", rows or []

    df = pd.DataFrame(rows, columns=columns)
    total = len(df)
    stat_lines: list[str] = []
    null_note_lines: list[str] = []

    for col in columns:
        series = df[col]
        null_count = int(series.isna().sum())
        non_null = series.dropna()
        null_suffix = f" | null={null_count}/{total}" if null_count else ""

        if null_count and null_count / total > 0.2:
            null_note_lines.append(f"{col}: {null_count}/{total} nulls ({null_count * 100 // total}%)")

        if non_null.empty:
            stat_lines.append(f"{col} [unknown]: all null")
            continue

        if pd.api.types.is_numeric_dtype(series):
            nums = non_null.astype(float)
            stat_lines.append(
                f"{col} [numeric]: min={nums.min():g} max={nums.max():g} mean={nums.mean():.4g}{null_suffix}"
            )
        elif non_null.astype(str).str.match(r"\d{4}-\d{2}-\d{2}").any():
            sv = sorted(non_null.astype(str).tolist())
            stat_lines.append(f"{col} [date]: {sv[0]} → {sv[-1]}{null_suffix}")
        else:
            sv = non_null.astype(str)
            top = list(sv.value_counts().head(5).items())
            top_str = ", ".join(f'"{v}"({c})' for v, c in top)
            stat_lines.append(
                f"{col} [string]: top=[{top_str}] | distinct={int(sv.nunique())}{null_suffix}"
            )

    return "\n".join(stat_lines), "; ".join(null_note_lines), _spread_sample(rows, cap=20)


def _spread_sample(rows: list[list], cap: int = 50) -> list[list]:
    """Return a representative spread sample from start, Q1, Q3, and end bands.

    Rows with more than half their values null are filtered out first.
    Used by dashboard_prompt, _build_data_summary, and synthesis nodes so that
    all LLM prompts see the same sampling strategy.
    """
    if not rows:
        return []

    n_cols = max(len(r) for r in rows) if rows else 1
    filtered = [r for r in rows if sum(1 for v in r if v is None) <= n_cols // 2] or rows

    n = len(filtered)
    if n <= cap:
        return filtered

    band = max(1, cap // 4)

    start = filtered[:band]
    q1_idx = max(band, int(n * 0.25) - band // 2)
    q1 = filtered[q1_idx:q1_idx + band]
    q3_idx = max(q1_idx + band, int(n * 0.75) - band // 2)
    q3 = filtered[q3_idx:q3_idx + band]
    end = filtered[max(q3_idx + band, n - band):]

    seen: set[int] = set()
    sampled: list[list] = []
    for r in start + q1 + q3 + end:
        if id(r) not in seen:
            seen.add(id(r))
            sampled.append(r)

    return sampled[:cap]


# ─── Data profile builder (shared by synthesis and chart_agent) ───────────────

def _build_data_profile(
    columns: list[str],
    rows: list[list],
    query_summary: dict | None = None,
) -> str:
    """Build a structured QUERY RESULTS block for LLM prompts.

    Used by both synthesis.py and chart_agent.py so both LLMs see identical
    structured data: column stats + strategic DATA SAMPLE.

    When query_summary.sample_rows is present (set by result_summarizer._smart_sample),
    that 50-row canonical sample is used for the DATA SAMPLE section — all three LLM
    consumers (synthesis, confidence, chart planning) see identical rows.
    When query_summary.was_truncated is True, a warning is prepended so every LLM
    knows the display rows are a sample of a larger result.
    """
    qs = query_summary or {}
    total_rows = qs.get("total_rows", len(rows))
    result_shape = qs.get("result_shape", "")
    was_truncated = qs.get("was_truncated", False)
    true_total_rows = qs.get("true_total_rows")
    stats_source = qs.get("stats_source", "capped")

    if total_rows == 0 or not columns:
        return "--- QUERY RESULTS ---\n\nTotal rows: 0\n(no records match the query criteria)"

    lines: list[str] = ["--- QUERY RESULTS ---", ""]

    if was_truncated:
        cap = total_rows
        count_part = f"{true_total_rows:,} total rows" if true_total_rows else f">{cap} rows"
        src_label = (
            "full Redshift aggregate (exact)"
            if stats_source == "full_result"
            else f"first {cap} rows (approximate — stats query timed out)"
        )
        lines += [
            f"TRUNCATION WARNING: This query returned {count_part} but only {cap} rows are "
            f"available for display. The DATA SAMPLE below is a stratified {min(50, cap)}-row "
            f"selection. Stats (distinct counts, min, max, mean) are from the {src_label}. "
            "Do NOT extrapolate totals from the sample rows — use the stats above.",
            "",
        ]

    header = f"Total rows (display cap): {total_rows}"
    if true_total_rows and was_truncated:
        header += f"   True total: {true_total_rows:,}"
    if result_shape:
        header += f"   Result shape: {result_shape}"
    lines += [header, "", "COLUMN PROFILES:", ""]

    qs_col_map = {c["name"]: c for c in qs.get("columns", [])}

    for i, col in enumerate(columns):
        meta = qs_col_map.get(col, {})
        dtype = meta.get("dtype") or _infer_col_type_from_rows(rows, i)
        lines.append(f"  {col}   {dtype}")

        norm = (dtype or "").lower()
        if any(t in norm for t in ("int", "float", "numeric", "decimal", "number")):
            mn, mx = meta.get("min"), meta.get("max")
            mean, median = meta.get("mean"), meta.get("median")
            if mn is None and mx is None:
                vals = [float(r[i]) for r in rows if i < len(r) and r[i] is not None]
                if vals:
                    mn, mx = min(vals), max(vals)
                    mean = sum(vals) / len(vals)
            if mn is not None:
                parts = [f"Min: {mn}", f"Max: {mx}"]
                if mean is not None:
                    parts.append(f"Mean: {round(float(mean), 2)}")
                if median is not None:
                    parts.append(f"Median: {round(float(median), 2)}")
                lines.append(f"    {'   '.join(parts)}")

        elif any(t in norm for t in ("date", "time", "timestamp")):
            mn, mx = meta.get("min"), meta.get("max")
            if not mn:
                vals = sorted(str(r[i]) for r in rows if i < len(r) and r[i] is not None)
                mn, mx = (vals[0], vals[-1]) if vals else (None, None)
            if mn:
                distinct_periods = meta.get("distinct_count") or len(
                    set(str(r[i]) for r in rows if i < len(r) and r[i] is not None)
                )
                src_note = " (full result)" if was_truncated and stats_source == "full_result" else ""
                lines.append(f"    Range: {mn}  →  {mx}   Distinct: {distinct_periods} periods{src_note}")

        else:  # varchar / text / string
            distinct = meta.get("distinct_count")
            top_vals = meta.get("top_values", [])
            if distinct:
                src_note = " (full result)" if was_truncated and stats_source == "full_result" else ""
                lines.append(f"    Distinct values: {distinct}{src_note}")
            if top_vals:
                tv_text = "  |  ".join(f"{v} ({n} rows)" for v, n in top_vals[:5])
                lines.append(f"    Top values:  {tv_text}")
                lines.append("    Note: row counts above are category frequency — not the measure column value.")
            elif not distinct:
                vals_str = [str(r[i]) for r in rows if i < len(r) and r[i] is not None]
                if vals_str:
                    counts = Counter(vals_str).most_common(5)
                    lines.append(f"    Distinct values: {len(set(vals_str))}")
                    lines.append(f"    Top values:  " + "  |  ".join(f"{v} ({n} rows)" for v, n in counts))
                    lines.append("    Note: row counts above are category frequency — not the measure column value.")

        lines.append("")

    sample_dicts = qs.get("sample_rows")
    if sample_dicts:
        sample_cols = [k for k in sample_dicts[0].keys() if k != "_sample_tier"] if sample_dicts else columns
        note = (
            f"DATA SAMPLE ({len(sample_dicts)} rows — stratified: boundary/outlier/coverage/representative"
            + (f" — do NOT compute averages; full result has {true_total_rows:,} rows" if true_total_rows else "")
            + "):"
        )
        lines.append(note)
        for rd in sample_dicts:
            tier = rd.get("_sample_tier", "")
            tier_tag = f"[{tier}] " if tier else ""
            lines.append(
                "  " + tier_tag
                + "   ".join(f"{c} = {rd.get(c)}" for c in sample_cols)
            )
    else:
        non_null = [r for r in rows if any(v is not None and v != "" for v in r)]
        if not non_null:
            lines.append("DATA SAMPLE: (all returned rows contain only null values)")
        elif len(non_null) <= 20:
            lines.append(f"DATA SAMPLE (all {len(non_null)} rows):")
            for r in non_null:
                lines.append("  " + "   ".join(f"{c} = {v}" for c, v in zip(columns, r)))
        else:
            sampled = _spread_sample(non_null, cap=20)
            lines.append(f"DATA SAMPLE ({len(sampled)} of {len(non_null)} rows — spread sample):")
            for r in sampled:
                lines.append("  " + "   ".join(f"{c} = {v}" for c, v in zip(columns, r)))

    return "\n".join(lines)


def _infer_col_type_from_rows(rows: list[list], col_idx: int) -> str:
    import pandas as pd
    vals = [r[col_idx] for r in rows[:20] if col_idx < len(r)]
    series = pd.Series(vals).dropna()
    if series.empty:
        return "unknown"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    if series.astype(str).str.match(r"\d{4}-\d{2}-\d{2}").any():
        return "date"
    return "varchar"


# ─── Tag parsing ──────────────────────────────────────────────────────────────

def parse_tag(text: str, tag: str) -> str:
    """Extract content from an XML-style tag in LLM output.

    For sql tags, strict extraction is used — never falls back to raw text,
    because leaked reasoning would corrupt the query. For all other tags,
    falls back to stripping XML wrappers.
    """
    m = re.search(f"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    if tag.lower() == "sql":
        m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            first = candidate.upper().split()[0] if candidate.split() else ""
            if first in ("SELECT", "WITH"):
                return candidate
        return ""

    stripped = re.sub(r"</?[a-zA-Z_]+>", "", text).strip()
    if stripped and not stripped.startswith("["):
        return stripped
    return ""


# ─── Directive section builder ────────────────────────────────────────────────

def build_directive_section(state: dict) -> str:
    """Build the three-directive block for LLM prompts.

    Three directives injected before QUERY SPECIFICATION:
    - EXECUTE INSTRUCTIONS: SQL execution requirements from intent resolver (formulas, computed predicates)
    - CONTEXT: Structural guidance from intent resolver (tables, joins, schema gaps)
    - SCHEMA DIRECTIVE: Code-verified structure from ir_builder (confirmed tables, join clauses)
    - FILTER DIRECTIVE: Code-verified values from filter_resolver (DB codes, temporal SQL)
    - CONFLICT RESOLUTION: explicit authority rules for all filter and join types

    Returns empty string if no directives are present.
    """
    instructions = (state.get("intent_directive_instructions") or "").strip()
    context      = (state.get("intent_directive_context")      or "").strip()
    # Fallback: old-format directive (no sub-sections) → treat as context
    if not instructions and not context:
        context = (state.get("intent_directive") or "").strip()

    schema  = (state.get("schema_directive")  or "").strip()
    filters = (state.get("filter_directive")  or "").strip()
    parts: list[str] = []

    if instructions:
        parts.append(
            "QUERY DIRECTIVE — EXECUTE THESE SQL INSTRUCTIONS (required, implement exactly):\n"
            + instructions
        )
    if context:
        parts.append(
            "QUERY DIRECTIVE — CONTEXT (structural guidance, informational):\n" + context
        )
    if schema:
        parts.append(
            "SCHEMA DIRECTIVE — code-verified structure "
            "(authoritative: confirmed anchor tables, join ON clauses, measures/dimensions):\n"
            + schema
        )
    if filters:
        parts.append(
            "FILTER DIRECTIVE — code-verified values "
            "(authoritative: DB codes, normalized numerics, temporal SQL; "
            "skip [WARNING: non-anchor] entries):\n"
            + filters
        )

    if any([instructions, context, schema, filters]):
        parts.append(
            "CONFLICT RESOLUTION (priority highest to lowest):\n"
            "  1. FILTER DIRECTIVE — resolved DB codes are authoritative; never substitute\n"
            "  2. SCHEMA DIRECTIVE — code-verified join clauses; copy ON clauses verbatim\n"
            "  3. EXECUTE INSTRUCTIONS — computation/CTE logic; preserve formulas\n"
            "  4. CONTEXT — informational only; shapes intent but never overrides above\n"
            "\n"
            "ADDITIVE RULE (not a conflict): COMPUTED_FILTER and FILTER DIRECTIVE target different columns.\n"
            "  FILTER DIRECTIVE = base table column filters (WHERE bank.name ~* 'JPM'). Apply always.\n"
            "  COMPUTED_FILTER = predicates on derived values (WHERE running_total < 200000000). Apply always.\n"
            "  Never substitute one for the other. Both appear in the final SQL simultaneously.\n"
            "\n"
            "TIME_FILTER UNIQUENESS: If EXECUTE INSTRUCTIONS contains multiple TIME_FILTER lines, use\n"
            "  the FIRST one only. A second TIME_FILTER is a planner error — do not create a second\n"
            "  date range filter from it. MULTI_GRAIN lines handle multi-grain sub-partitioning, not\n"
            "  a second outer date window."
        )
    return "\n\n".join(parts) if parts else ""


def build_refinement_section(state: dict, role: str = "generic") -> str:
    """Returns REFINEMENT CONTEXT block for specialist/directive prompts.

    Returns empty string when is_refinement is not set, prior_sql is absent,
    or when recompile_count > 0 (recompile is handled by _build_prior_sql_section).
    """
    if not state.get("is_refinement"):
        return ""
    if (state.get("recompile_count") or 0) > 0:
        return ""
    prior_sql = state.get("prior_sql") or ""
    if not prior_sql:
        return ""
    role_instructions = {
        "measures": (
            "The user instruction does NOT mention changing measures. "
            "Preserve the existing measures from the prior SQL unless explicitly asked to change them."
        ),
        "filters": (
            "The user is adding or modifying filters on an existing SELECT query. "
            "Extract the new filter. Do NOT interpret 'add' as INSERT/UPDATE/DELETE."
        ),
        "dimensions": (
            "The user instruction does NOT mention changing groupings. "
            "Preserve the existing dimensions from the prior SQL unless explicitly asked to change them."
        ),
        "directive": (
            "Write a directive to MODIFY the prior SELECT query — not to create a new one. "
            "The output MUST remain a SELECT statement."
        ),
    }
    instruction = role_instructions.get(role, "")
    return (
        f"\nREFINEMENT CONTEXT — user is modifying an existing query:\n"
        f"<prior_sql>\n{prior_sql}\n</prior_sql>\n"
        f"{instruction}\n"
    )


def build_joinable_table_graph_section(anchor_join_paths: list[dict] | None) -> str:
    """Format confirmed join paths as a JOINABLE TABLE GRAPH for specialist prompts.

    Specialists use this to avoid picking columns from tables that can't be joined
    to the primary anchor table.
    """
    if not anchor_join_paths:
        return ""
    lines = [
        "JOINABLE TABLE GRAPH — only output columns from tables connected here.",
        "Do not reference tables absent from this graph.",
    ]
    for p in anchor_join_paths:
        from_fqn = p.get("from_fqn", "")
        to_fqn = p.get("to_fqn", "")
        clauses = p.get("join_clauses") or []
        if from_fqn and to_fqn:
            clause_str = " | ".join(clauses[:2]) if clauses else "(join clause not confirmed)"
            lines.append(f"  {from_fqn} ↔ {to_fqn}  ON: {clause_str}")
    return "\n".join(lines)


# ─── Shared LLM context section builders ─────────────────────────────────────

def _build_entity_tokens_section(entity_tokens: list[str] | None) -> str:
    """Format entity tokens extracted by intake_classifier for injection into LLM prompts.

    These are proper nouns / named entities from the user question (e.g. "JPMorgan",
    "operating account") that should guide column/table selection in each specialist.
    Returns empty string when entity_tokens is None or empty.
    """
    if not entity_tokens:
        return ""
    lines = ["NAMED ENTITIES from user question (filter / dimension candidates):"]
    for tok in entity_tokens[:5]:
        lines.append(f"  • {tok}")
    return "\n".join(lines)


def _build_concept_mappings_section(concept_mappings: dict | None) -> str:
    """Format BusinessTerm concept mappings for injection into LLM prompts.

    concept_mappings: {term_name: {"definition": str, "computation": str, ...}}
    Tells the LLM the business meaning and computation formula for derived terms
    so it picks the right measure columns instead of inferring from column names.
    Returns empty string when concept_mappings is None or empty.
    """
    if not concept_mappings:
        return ""
    lines = ["BUSINESS TERM DEFINITIONS (use these computations exactly):"]
    for term, meta in list(concept_mappings.items())[:10]:
        defn = (meta.get("definition") or "").strip()
        comp = (meta.get("computation") or "").strip()
        line = f"  {term}"
        if defn:
            line += f": {defn}"
        if comp:
            line += f"  → COMPUTATION: {comp}"
        lines.append(line)
    return "\n".join(lines)


def build_mission_context(state: dict, role: str, feeds: str) -> str:
    """Return a MISSION block prepended to any LLM node's prompt.

    Universal — works for any question type and any domain.
    Provides:
      - The original user question
      - The structured intent lines from intake_classifier (GOAL/TIME/CONDITION/…)
      - This node's single responsibility and what the next node needs from it
      - An alignment check the LLM must perform before finalizing output

    Args:
        state: LangGraph AgentState dict.
        role:  One-line description of what THIS node is responsible for.
        feeds: One-line description of what the NEXT node(s) need from this output.
    """
    question = state.get("question") or ""
    intent_lines: list[str] = state.get("query_intent") or []
    complexity: str = state.get("complexity") or "unset"
    intent_block = (
        "\n".join(f"  {line}" for line in intent_lines)
        if intent_lines
        else "  (not available — intake_classifier may have routed via general_chat)"
    )
    return (
        "=== MISSION ===\n"
        f"QUESTION: {question}\n"
        f"INTENT:\n{intent_block}\n"
        f"COMPLEXITY: {complexity}\n"
        f"YOUR ROLE: {role}\n"
        f"YOUR OUTPUT FEEDS: {feeds}\n"
        "ALIGNMENT CHECK: Before finalizing your output:\n"
        "  1. Scan each GOAL line — does your output serve it?\n"
        "  2. Scan each CONDITION line — is every threshold, flag, or constraint handled?\n"
        "  3. Cross-check against what YOUR OUTPUT FEEDS expects from you.\n"
        "  4. If an upstream decision conflicts with the GOAL/CONDITION lines, correct it"
        " and note the correction in your reasoning.\n"
        "  Anything unaddressed must be flagged explicitly — never silently omit.\n"
        "===============\n"
    )


# ─── Section streamers (mirror quest exactly) ─────────────────────────────────

class MultiSectionStreamer:
    """Stream content from multiple XML sections in sequence.

    Each section has its own tag and SSE event type. As tokens arrive,
    the streamer detects which section is active and emits content with
    the corresponding event type.
    """

    def __init__(self, sections: list[tuple[str, str]]) -> None:
        self.sections = [
            {
                "open": f"<{tag.lower()}>",
                "close": f"</{tag.lower()}>",
                "etype": etype,
                "done": False,
            }
            for tag, etype in sections
        ]
        self.active_idx: int | None = None
        self.buf = ""
        self.done = False

    def reset(self) -> None:
        for s in self.sections:
            s["done"] = False
        self.active_idx = None
        self.buf = ""
        self.done = False

    def feed(self, token: str) -> tuple[str, str]:
        """Feed a token and return (emittable_text, sse_event_type).

        Returns ("", "") if nothing to emit yet.
        """
        if self.done:
            return "", ""
        self.buf += token
        buf_lower = self.buf.lower()

        if self.active_idx is None:
            for i, s in enumerate(self.sections):
                if s["done"]:
                    continue
                if s["open"] in buf_lower:
                    self.active_idx = i
                    idx = buf_lower.index(s["open"]) + len(s["open"])
                    self.buf = self.buf[idx:]
                    break
            else:
                max_tag_len = max(len(s["open"]) for s in self.sections)
                if len(self.buf) > max_tag_len:
                    self.buf = self.buf[-(max_tag_len - 1):]
                return "", ""

        s = self.sections[self.active_idx]
        buf_lower = self.buf.lower()
        if s["close"] in buf_lower:
            idx = buf_lower.index(s["close"])
            emit = self.buf[:idx]
            self.buf = self.buf[idx + len(s["close"]):]
            s["done"] = True
            etype = s["etype"]
            self.active_idx = None
            if all(sec["done"] for sec in self.sections):
                self.done = True
            return emit, etype

        hold = len(s["close"]) - 1
        if len(self.buf) > hold:
            emit, self.buf = self.buf[:-hold], self.buf[-hold:]
            return emit, s["etype"]
        return "", ""


class SectionStreamer:
    """Stream text content from XML sections token by token.

    Handles every occurrence of the tag within a node invocation so that
    retry blocks all reach the UI, not just the first.
    """

    def __init__(self, tag: str) -> None:
        self.open_tag = f"<{tag.lower()}>"
        self.close_tag = f"</{tag.lower()}>"
        self.active = False
        self.buf = ""

    def reset(self) -> None:
        self.active = False
        self.buf = ""

    def feed(self, token: str) -> str:
        """Feed a token and return emittable text (empty string when buffering)."""
        self.buf += token
        emitted: list[str] = []
        while True:
            buf_lower = self.buf.lower()
            if not self.active:
                if self.open_tag in buf_lower:
                    self.active = True
                    idx = buf_lower.index(self.open_tag) + len(self.open_tag)
                    self.buf = self.buf[idx:]
                    continue
                hold = len(self.open_tag) - 1
                if len(self.buf) > hold:
                    self.buf = self.buf[-hold:]
                break
            buf_lower = self.buf.lower()
            if self.close_tag in buf_lower:
                idx = buf_lower.index(self.close_tag)
                if idx:
                    emitted.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.close_tag):]
                self.active = False
                continue
            hold = len(self.close_tag) - 1
            if len(self.buf) > hold:
                emitted.append(self.buf[:-hold])
                self.buf = self.buf[-hold:]
            break
        return "".join(emitted)

