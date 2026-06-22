"""Node 1b: intent_resolver — constrained LLM intent extraction.

Uses Sonnet. Extracts structured intent from the question, constrained to
identifiers from SemanticContext. Validates every identifier post-LLM.
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents.helpers import build_mission_context, parse_tag
from app.services.agents.prompts import INTENT_RESOLVE_PROMPT, REASONING_DIRECTIVE_DEEP, REASONING_DIRECTIVE_NORMAL
from app.services.agents.state import AnalyticsState


async def intent_resolver(state: AnalyticsState, config: RunnableConfig) -> dict:
    logger.info("intent_resolver START | thread={}", state["thread_id"])

    prompt = _build_prompt(state)
    _mission = build_mission_context(
        state,
        role="Recover from failed intent assembly by re-inferring structured intent from scratch",
        feeds="query_compiler → sql_generator (recovery path)",
    )
    prompt[0].content = _mission + "\n\n" + prompt[0].content

    from app.services.agents.bedrock import get_llm
    from app.core.circuit_breaker import llm_breaker

    llm = get_llm("balanced")

    @llm_breaker
    async def _call():
        from app.core.retry import retry_async
        return await retry_async(lambda: llm.ainvoke(prompt, config=config), service="bedrock-intent-resolver", max_attempts=2, backoff_base=5.0)

    try:
        response = await _call()
    except Exception as e:
        logger.error("intent_resolver LLM failed | thread={} | error={}", state["thread_id"], e)
        return {"error": "LLM unavailable", "needs_clarification": False}

    raw = response.content or ""
    directive_raw = parse_tag(raw, "directive") or ""
    # Extract structured sub-sections from the directive
    instructions_text = parse_tag(directive_raw, "instructions").strip()
    context_text      = parse_tag(directive_raw, "context").strip()
    # Fallback: if no sub-sections (old format), treat full directive as context
    if not instructions_text and not context_text:
        context_text = directive_raw

    if directive_raw:
        logger.info(
            "intent_resolver | DIRECTIVE | thread={}\n"
            "=INSTRUCTIONS=\n{}\n=CONTEXT=\n{}",
            state["thread_id"], instructions_text or "(none)", context_text or "(none)",
        )
    else:
        logger.warning("intent_resolver | no <directive> tag emitted | thread={}", state["thread_id"])
    resolved = _parse_response(raw, state["thread_id"])

    if resolved is None:
        logger.warning("intent_resolver parse failed | thread={} | raw_len={}", state["thread_id"], len(raw))
        return {"error": "intent_parse_failed", "needs_clarification": False}

    validation_error = _validate_identifiers(resolved, state.get("semantic_context") or {})
    if validation_error:
        logger.warning("intent_resolver hallucination | thread={} | error={} | proceeding best-effort", state["thread_id"], validation_error)

    confidence = resolved.get("confidence", 0.0)

    logger.info(
        "intent_resolver DONE | template={} | confidence={:.2f} | complexity={}",
        resolved.get("template_id"), confidence, resolved.get("complexity"),
    )
    return {
        "resolved_intent": resolved,
        "intent_directive": directive_raw,
        "intent_directive_instructions": instructions_text,
        "intent_directive_context": context_text,
        "needs_clarification": False,
        "clarification_reason": None,
        "execution_error": None,
        "repair_count": 0,
        "recompile_count": (state.get("recompile_count") or 0) + (1 if state.get("execution_error") else 0),
    }


_LLM_STRIP = {"retrieval_paths", "score", "matched_via", "community_id"}


def _format_recent_messages(messages: list) -> str:
    """Format last 3 messages as conversation context for turns without a session_summary."""
    from langchain_core.messages import HumanMessage
    lines = []
    for m in messages[-3:]:
        role = "User" if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human" else "Assistant"
        content = (m.content or "")[:300]
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else ""


def _build_schema_candidates_text(semantic_context: dict) -> str:
    """Build structured SCHEMA CANDIDATES text for the intent_resolver LLM.

    Section order (LLM reads top-to-bottom):
    1. CRITICAL CONSTRAINT — prevents hallucination
    2. TABLES — grain + hub info, no numeric scores
    3. COLUMNS — [JOIN KEY] first, description + synonyms + values
    4. BUSINESS TERMS
    5. INTENT PATTERNS
    6. QUERY STRUCTURE HINTS — templates at bottom, NO anchor_table_fqns shown

    filter_values = value_vocabulary (set in context_fetcher, not from Redshift).
    """
    templates     = semantic_context.get("templates", [])[:5]
    tables_raw    = semantic_context.get("tables", [])[:14]
    columns_raw   = semantic_context.get("columns", [])[:80]
    business_terms = semantic_context.get("business_terms", [])[:5]
    intents       = semantic_context.get("intents", [])[:3]

    # Join-critical set for [JOIN KEY] tagging
    join_crit_set: set[tuple] = set()
    for item in (semantic_context.get("join_critical_cols") or []):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            join_crit_set.add(tuple(item))

    tables  = [{k: v for k, v in t.items() if k not in _LLM_STRIP} for t in tables_raw]
    columns = [{k: v for k, v in c.items() if k not in _LLM_STRIP} for c in columns_raw]

    lines = [
        "--- SCHEMA CANDIDATES ---",
        "",
        "CRITICAL: Only reference tables in TABLES and columns in COLUMNS below.",
        "Do NOT invent table names, column names, or schema prefixes not listed here.",
        "",
    ]

    # ── ENTITY VALUE MATCHES ─────────────────────────────────────────────────
    entity_hints = semantic_context.get("entity_hints") or []
    if entity_hints:
        lines += [
            "ENTITY VALUE MATCHES — question tokens matched schema vocabulary directly.",
            "RULE: If the same token matches a primary entity table (e.g., lpp.bank) AND a FK",
            "      column on a child table (e.g., lpp.bank_account.branch_ref), prefer the PRIMARY",
            "      entity table: add it to anchor_tables and set the WHERE filter there.",
            "      The child table's FK column is the join key, NOT the filter column.",
        ]
        for eh in entity_hints[:5]:
            lines.append(
                f"  '{eh.get('token')}' -> {eh.get('table_fqn')}.{eh.get('column')}"
                f" (matched: {str(eh.get('matched_value', ''))[:80]})"
                " — add as a WHERE filter on this column"
            )
        lines += [""]

    # ── TABLES ────────────────────────────────────────────────────────────────
    lines += ["TABLES (ranked by number of discovery paths that confirmed each table):"]
    for t in tables:
        fqn  = t.get("fqn", "")
        role = t.get("typical_join_role", "") or t.get("table_type", "")
        desc = (t.get("description", "") or "")[:100]
        grain = t.get("grain", "")

        role_str = f" {role:<12}" if role else "             "
        lines.append(f"  {fqn:<45}{role_str}— {desc}".rstrip())
        if grain:
            lines.append(f"    grain: {grain}")
        if t.get("is_dimension_hub") and t.get("hub_join_col"):
            lines.append(f"    [dimension hub — joins {t.get('in_degree', '?')} tables via '{t['hub_join_col']}']")

    # ── COLUMNS ───────────────────────────────────────────────────────────────
    lines += ["", "---", "", "COLUMNS (join-critical always shown; others ranked by relevance to question):"]
    for c in columns:
        table_fqn = c.get("table_fqn", "")
        name      = c.get("name", "")
        dtype     = c.get("data_type", "") or c.get("semantic_type", "")
        desc      = c.get("description", "")
        synonyms  = c.get("synonyms") or []
        is_jk     = (table_fqn, name) in join_crit_set
        is_meas   = c.get("semantic_type", "").lower() in ("amount", "measure", "percentage", "ratio")

        # Use filter_values (= value_vocabulary set in context_fetcher) — not sample_values
        samples = c.get("filter_values") or []

        # Parse value_aliases for meanings display
        raw_al = c.get("value_aliases") or []
        alias_map: dict = {}
        if isinstance(raw_al, list):
            for s in raw_al:
                for sep in (" -> ", " → ", "->"):
                    if sep in str(s):
                        parts = str(s).split(sep, 1)
                        alias_map[parts[0].strip()] = parts[1].strip()
                        break
        elif isinstance(raw_al, dict):
            alias_map = raw_al

        tag = "[JOIN KEY] " if is_jk else "           "
        col_ref = f"{table_fqn}.{name}"
        norm_dtype = (dtype or "").lower()

        line = f"  {tag}{col_ref:<48} {dtype:<12}"
        if desc:
            max_desc = 200 if is_jk else (100 if is_meas else 60)
            line += f'  "{desc[:max_desc]}"'

        lines.append(line)

        # Synonyms line (for join-critical and measurable only)
        if synonyms and (is_jk or is_meas):
            lines.append(f"             also known as: {', '.join(synonyms[:3 if is_jk else 2])}")

        # Values line
        if samples:
            if any(x in norm_dtype for x in ("date", "time", "timestamp")):
                lines.append(f"             sample: {samples[0]}")
            else:
                lines.append("             values: " + " | ".join(str(v) for v in samples[:5]))
            # Meanings line (value_aliases)
            if alias_map:
                meanings = " | ".join(f"{k}={v}" for k, v in list(alias_map.items())[:4])
                lines.append(f"             meanings: {meanings}")
        elif is_meas or any(x in norm_dtype for x in ("int", "float", "numeric", "decimal", "real", "money", "bigint")):
            lines.append("             (SUM or AVG)")

    # ── BUSINESS TERMS ────────────────────────────────────────────────────────
    if business_terms:
        lines += ["", "---", "", "BUSINESS TERMS:"]
        for bt in business_terms:
            if isinstance(bt, dict):
                term       = bt.get("term", "")
                definition = bt.get("definition", "") or bt.get("description", "")
                if term:
                    lines.append(f'  "{term}": {definition}')

    # ── INTENT PATTERNS ───────────────────────────────────────────────────────
    if intents:
        lines += ["", "INTENT PATTERNS:"]
        for intent in intents:
            if isinstance(intent, dict):
                n = intent.get("name", "")
                d = intent.get("description", "")
                if n:
                    lines.append(f"  {n:<25} — {d}" if d else f"  {n}")
            elif isinstance(intent, str) and intent:
                lines.append(f"  {intent}")

    # ── QUERY STRUCTURE HINTS (templates at BOTTOM — NO anchor_table_fqns) ───
    lines += ["", "---", "", "QUERY STRUCTURE HINTS (structural patterns from similar questions — suggestions only, do NOT override TABLES above):"]
    if templates:
        for t in templates:
            sql_pat = t.get("sql_pattern", "")
            cte_s   = " -> ".join((t.get("cte_steps") or [])[:5])
            if sql_pat or cte_s:
                lines.append(f"  pattern={sql_pat}   cte_steps: {cte_s[:120]}")
    else:
        lines.append("  [No similar historical queries found]")

    return "\n".join(lines)


def _build_prompt(state: AnalyticsState) -> list:
    semantic_context = state.get("semantic_context") or {}

    logger.info(
        "intent_resolver | llm_context | tables={} | columns={}",
        [t.get("fqn") for t in semantic_context.get("tables", [])[:10]],
        [(c.get("table_fqn"), c.get("name")) for c in semantic_context.get("columns", [])[:40]],
    )

    schema_candidates_text = _build_schema_candidates_text(semantic_context)

    session_summary = semantic_context.get("session_summary") or state.get("summary") or ""
    conversation_context = state.get("conversation_history") or session_summary or _format_recent_messages(state.get("messages", []))

    execution_error = state.get("execution_error")
    prior_sql = state.get("prior_sql")
    if execution_error:
        if prior_sql:
            execution_error_section = (
                "\nPREVIOUS EXECUTION FAILURE — re-interpret to avoid repeating the same approach:\n"
                f"SQL that failed:\n<prior_sql>{prior_sql}</prior_sql>\n"
                f"Error: <execution_error>{execution_error}</execution_error>\n"
                "Choose different tables, columns, or join strategy.\n"
            )
        else:
            execution_error_section = (
                f"\nPREVIOUS EXECUTION FAILURE — the SQL generated from your last interpretation "
                f"was rejected by the database with this error. Re-interpret the question choosing "
                f"different columns, joins, or tables to avoid the same failure:\n"
                f"<execution_error>{execution_error}</execution_error>\n"
            )
    elif state.get("is_refinement") and prior_sql and not (state.get("recompile_count") or 0):
        execution_error_section = (
            "\nREFINEMENT CONTEXT — user is modifying an existing query:\n"
            f"<prior_sql>\n{prior_sql}\n</prior_sql>\n"
            "The user's current question is a modification of the SQL above. "
            "Apply whatever the user is asking — filter, table swap, grouping, measure, or structural rewrite. "
            "IMPORTANT: The output MUST remain a SELECT statement. "
            "When the user says 'add' or 'include', this means adding a WHERE filter or JOIN — "
            "NOT an INSERT, UPDATE, or DELETE statement.\n"
        )
    else:
        execution_error_section = ""

    return INTENT_RESOLVE_PROMPT.format_messages(
        question=state.get("effective_question") or state["question"],
        persona=state.get("persona", "analyst"),
        feedback_context=state.get("feedback_context", ""),
        conversation_context=conversation_context,
        memory_context=semantic_context.get("memory_context", ""),
        schema_candidates_text=schema_candidates_text,
        execution_error_section=execution_error_section,
        reasoning_directive=REASONING_DIRECTIVE_DEEP if state.get("deep_analysis") else REASONING_DIRECTIVE_NORMAL,
    )


def _parse_response(raw: str, thread_id: str) -> dict | None:
    from json_repair import loads as json_loads
    output_tag = parse_tag(raw, "output")
    if not output_tag:
        logger.warning("intent_resolver | no <output> tag | thread={}", thread_id)
        return None
    try:
        return json_loads(output_tag)
    except Exception as e:
        logger.warning("intent_resolver | json_repair failed | thread={} | error={}", thread_id, e)
        return None


def _validate_identifiers(resolved: dict, semantic_context: dict) -> str | None:
    """Check that every measure/dimension table+column exists in semantic_context.

    Uses _column_lookup (ALL anchor table columns, not just display subset of 40).
    Also validates anchor_tables against known table FQNs — catches hallucinated tables.
    """
    lookup = semantic_context.get("_column_lookup") or {}
    known_col_refs = {f"{tfqn}.{cname}" for (tfqn, cname) in lookup}
    # Fallback to display columns when _column_lookup not yet populated
    if not known_col_refs:
        for col in semantic_context.get("columns", []):
            if col.get("table_fqn") and col.get("name"):
                known_col_refs.add(f"{col['table_fqn']}.{col['name']}")

    known_tables = {t["fqn"] for t in semantic_context.get("tables", []) if t.get("fqn")}

    for table_fqn in (resolved.get("anchor_tables") or []):
        if known_tables and table_fqn not in known_tables:
            return f"Table {table_fqn} not found in data catalog — likely hallucinated"

    for measure in resolved.get("measures", []):
        col_ref = f"{measure.get('table_fqn', '')}.{measure.get('column_name', '')}"
        if col_ref and "." in col_ref and col_ref not in known_col_refs and known_col_refs:
            return f"Column {col_ref} not found — will attempt fuzzy remap in ir/validation"

    for dim in resolved.get("dimensions", []):
        col_ref = f"{dim.get('table_fqn', '')}.{dim.get('column_name', '')}"
        if col_ref and "." in col_ref and col_ref not in known_col_refs and known_col_refs:
            return f"Column {col_ref} not found — will attempt fuzzy remap in ir/validation"

    return None
