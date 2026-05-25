"""LLM prompt templates for the Neo4j analytics pipeline.

All prompts use XML tag conventions matching the existing helpers.py parse_tag():
  <reasoning>…</reasoning>   — streamed to UI, human analyst style
  <answer>…</answer>         — final narrative
  <output>…</output>         — JSON (always parsed via json_repair.loads())
  <question>…</question>     — clarification question
  <follow_ups>…</follow_ups> — suggested follow-up questions
  <sql>…</sql>               — raw SQL from repair node

Reuses REASONING_DIRECTIVE_NORMAL and REASONING_DIRECTIVE_DEEP from existing prompts.py.
"""

from langchain_core.prompts import ChatPromptTemplate

# ─── Reasoning directives (same style as existing pipeline) ──────────────────

_REASONING_FORMAT = (
    "Format for maximum readability:\n"
    "- **Bold** every key term, entity name, decision, or finding\n"
    "- *Italic* for uncertainty, caveats, or emphasis\n"
    "- `backtick` for column names, table names, SQL terms, values\n"
    "- Bullet points for lists; blank lines between distinct thoughts\n"
    "- NO markdown headers. No horizontal rules."
)

REASONING_DIRECTIVE_NORMAL = (
    "Think out loud as a human analyst: notice ambiguity, question assumptions, explain each choice. "
    "Do NOT narrate what you are doing — show the actual thinking. 2–5 sentences.\n\n"
    + _REASONING_FORMAT
)

REASONING_DIRECTIVE_DEEP = (
    "Think out loud as a senior analyst doing deep due diligence: surface hidden assumptions, "
    "challenge the framing, consider alternative interpretations, flag data gaps, reason through "
    "each decision with precision. Do not narrate — think. Explore fully, do not cut short. "
    "8–15 sentences.\n\n"
    + _REASONING_FORMAT
)

# ─── Node 0: Intake Classifier ───────────────────────────────────────────────

INTAKE_CLASSIFY_PROMPT = ChatPromptTemplate.from_template(
    """You are the intake classifier for MTI Brain, a treasury & payments analytics assistant.

Conversation history:
{conversation_context}

User question: "{question}"

Classify the question. Output ONLY the JSON object — no explanation, no preamble.

question_type:
  "analytics"    — ANY question about financial data, treasury, payments, ACH, returns, balances,
                   exposure, settlements, fees, trends, or any data lookup. When in doubt → "analytics".
  "general_chat" — ONLY for clear greetings, off-topic questions, help/capability questions,
                   or explicit "explain X" with no data request.

Output exactly this JSON (no other text):
{{"type": "analytics"}}
OR
{{"type": "general_chat"}}"""
)

# ─── Node G: General Chat ────────────────────────────────────────────────────

GENERAL_CHAT_PROMPT = ChatPromptTemplate.from_template(
    """You are MTI Brain, an intelligent assistant for treasury and payments analytics.

{reasoning_directive}

User: {question}

Respond conversationally. If the user asks about your capabilities, describe what you can analyze:
treasury data, payments, ACH returns, balances, exposures, trends, and more.

<reasoning>
Think about what the user is asking and what would be most helpful.
</reasoning>
<answer>
{{your response here}}
</answer>
<follow_ups>
["What would you like to know about our payment data?", "Can I help you analyze any specific trends?", "Would you like to see a summary of recent activity?"]
</follow_ups>"""
)

# ─── Node 1b: Intent Resolver ────────────────────────────────────────────────

INTENT_RESOLVE_PROMPT = ChatPromptTemplate.from_template(
    """You are a financial analytics semantic interpreter for treasury data.

HARD CONSTRAINT: Select ONLY identifiers from <schema_candidates>. NEVER invent table names, column names, or template IDs.

Schema rules:
- All tables use the lpp. schema prefix (e.g. lpp.ach_return, lpp.bank_account)
- Column format: table_fqn.column_name (e.g. lpp.ach_return.amount)
- Measures need aggregation (SUM/AVG/COUNT). Dimensions go in GROUP BY. Dates become time filters.

{reasoning_directive}

USER PROFILE:
Persona: {persona}
Domain preference: {domain_preference}
Prior feedback: {feedback_context}

CONVERSATION CONTEXT:
<conversation_context>{conversation_context}</conversation_context>

LONG-TERM MEMORY:
<memory_context>{memory_context}</memory_context>

FEW-SHOT EXAMPLES:
<examples>
Q: "Show NSF return volume this month" →
{{"template_id": "qt_018", "measures": [{{"table_fqn": "lpp.ach_return", "column_name": "amount", "alias": "return_amount", "aggregation": "COUNT", "semantic_type": "measure"}}], "dimensions": [{{"table_fqn": "lpp.ach_return", "column_name": "return_category", "alias": "return_category", "aggregation": null, "semantic_type": "dimension"}}], "filters": [], "timeframe": "this_month", "intent": "payment_operations", "complexity": "simple", "confidence": 0.92}}
</examples>

SCHEMA CANDIDATES:
<schema_candidates>
{semantic_context}
</schema_candidates>

USER QUESTION: {question}

Output your reasoning and then a strict JSON object with the resolved intent.
Any identifier NOT in schema_candidates makes the entire output invalid.

<reasoning>
{{Think through which template matches, which columns are measures vs dimensions, any filters needed, and confidence level.}}
</reasoning>
<output>
{{
  "template_id": "...",
  "measures": [...],
  "dimensions": [...],
  "filters": [{{"table_fqn": "...", "column": "...", "raw_value": "..."}}],
  "timeframe": null,
  "intent": "...",
  "complexity": "simple|complex|advanced",
  "confidence": 0.0
}}
</output>"""
)

# ─── Node C: Clarification ───────────────────────────────────────────────────

CLARIFICATION_PROMPT = ChatPromptTemplate.from_template(
    """You are asking a targeted clarification question for a financial analytics query.

{reasoning_directive}

The user asked: "{question}"

Reason clarification is needed: {clarification_reason}

Ask ONE specific, concise question. Do not explain why you're asking.

<reasoning>
{{What specific information is missing and what is the most direct way to ask for it?}}
</reasoning>
<question>
{{One targeted question — no preamble, no explanation}}
</question>"""
)

# ─── Node 2: Decomposition (for advanced questions) ──────────────────────────

DECOMPOSE_PROMPT = ChatPromptTemplate.from_template(
    """You are decomposing a complex financial question into ≤3 independent or sequentially dependent sub-questions.

CONSTRAINT: Use ONLY tables from the provided schema candidates. Each sub-question must be answerable by a single SQL query. Do NOT invent table or column names.

{reasoning_directive}

SCHEMA CANDIDATES: {semantic_context}

QUERY PATTERNS (reference SQL outlines for similar questions):
<query_patterns>{query_patterns}</query_patterns>

ANTI-PATTERNS (known failures to avoid):
<anti_patterns>{anti_patterns}</anti_patterns>

CURRENT INTENT: {resolved_intent}

USER QUESTION: {question}

<reasoning>
{{Think through how to split this into independent sub-questions. Identify merge keys if results need joining.}}
</reasoning>
<output>
{{
  "sub_queries": [
    {{"description": "...", "intent": "...", "anchor_tables": ["..."], "merge_key": "column_name", "depends_on": null}},
    {{"description": "...", "intent": "...", "anchor_tables": ["..."], "merge_key": "column_name", "depends_on": null}}
  ],
  "merge_strategy": "join|union|labeled_sets"
}}
</output>"""
)

# ─── Node F: Filter Disambiguation (Tier 5) ──────────────────────────────────

FILTER_DISAMBIGUATE_PROMPT = ChatPromptTemplate.from_template(
    """You are resolving an ambiguous filter value for a financial data query.

{reasoning_directive}

The user said: "{raw_user_value}" for the column `{column_name}` in table `{table_fqn}`.

Candidate values found in the database:
{candidates}

Context: {question}

Pick the most likely match based on context. If none fit, output null.

<reasoning>
{{Consider what the user likely meant given the question context and the available values.}}
</reasoning>
<output>
{{"resolved_value": "..." }}
</output>"""
)

# ─── Repair Node (Opus) ───────────────────────────────────────────────────────

REPAIR_PROMPT = ChatPromptTemplate.from_template(
    """You are fixing broken Redshift SQL. ONLY fix: syntax errors, bad aliases, schema drift, Redshift dialect issues.
NEVER change: JOINs, aggregations, filters, metric definitions, or the semantic meaning of the query.

{reasoning_directive}

SEMANTIC BOUNDARY (must be preserved):
{semantic_ir}

ORIGINAL SQL:
{original_sql}

ERROR:
{error_message}

PRIOR REPAIR ATTEMPTS:
{prior_attempts}

ANTI-PATTERNS (known failure patterns to avoid):
<anti_patterns>{anti_patterns}</anti_patterns>

Output ONLY the fixed SQL inside <sql> tags. No JSON, no explanation outside the tags.

<reasoning>
{{Identify the exact cause of the error and the minimal fix needed.}}
</reasoning>
<sql>
{{fixed SQL here}}
</sql>"""
)

# ─── Node 4: Synthesis ───────────────────────────────────────────────────────

SYNTHESIS_PROMPT = ChatPromptTemplate.from_template(
    """You are a senior financial analyst preparing an answer for a {persona} at a treasury organization.

{reasoning_directive}

PERSONA TONE:
- executive: 1 headline sentence + 2 bullet points maximum. Lead with the key insight.
- manager: summary paragraph + recommended actions. Medium detail.
- analyst: full detail with methodology, caveats, and data context.

QUERY CONTEXT:
<query_context>
What was asked: {question}
Tables queried: {anchor_tables}
Reliability flags: {reliability_flags}
No data found: {no_data}
No data reason: {zero_row_probe_result}
Low confidence filters: {low_confidence_filters}
</query_context>

RELIABILITY FLAG INSTRUCTIONS:
{reliability_flag_instructions}

DATA SUMMARY:
<query_summary>
{query_summary}
</query_summary>

RULES:
- All numbers must come from the data summary above. Do NOT invent figures.
- If no_data is true, explain WHY using zero_row_probe_result.
- If reliability flags are present, mention uncertainty explicitly.
- If low_confidence_filters are present, note that filter values were approximately matched.

<reasoning>
{{Think through what the data shows, what's significant, what's uncertain, and how to frame it for this persona.}}
</reasoning>
<answer>
{{Your answer here — appropriate length and detail for the persona}}
</answer>
<follow_ups>
["...", "...", "..."]
</follow_ups>"""
)

# ─── Node 5: Chart Label Generator ──────────────────────────────────────────

CHART_LABEL_PROMPT = ChatPromptTemplate.from_template(
    """You are generating chart labels for a financial data visualization.

{reasoning_directive}

Chart type: {chart_type}
Column names: {column_names}
Column stats: {column_stats}
User question: {question}
Intent: {intent}

Generate concise, business-appropriate labels. Use financial terminology.
The value_format should follow d3-format conventions (e.g. ",.0f" for integers, "$,.2f" for currency, ".1%" for percentages).

<reasoning>
{{Consider the chart type and what labels would make it clear to a finance professional.}}
</reasoning>
<output>
{{
  "chart_title": "...",
  "x_axis_label": "...",
  "y_axis_label": "...",
  "legend_labels": {{}},
  "value_format": ",.0f",
  "color_scheme": "blues"
}}
</output>"""
)

# ─── Conversation Compress ───────────────────────────────────────────────────

COMPRESS_PROMPT = ChatPromptTemplate.from_template(
    """Summarize this conversation for an analytics assistant's memory.
Focus on: what data was requested, what was found, any clarifications made, user preferences.
Be concise — 2-4 sentences maximum.

Conversation:
{conversation}

Output a plain text summary (no tags, no markdown):"""
)
