"""LLM prompt templates for the MTI Brain agentic pipeline.

All prompts use XML tag conventions so streaming helpers can extract content:
  <reasoning>…</reasoning>   — step-by-step thinking (streamed to UI)
  <sparql>…</sparql>         — generated SPARQL query
  <answer>…</answer>         — final narrative (streamed to UI)
  <summary>…</summary>       — rolling conversation summary
  <plan>…</plan>             — sub-question DAG JSON
  <reflection>…</reflection> — judge verdict

MARKDOWN FORMATTING RULES (apply to ALL reasoning and answer sections):
  - Use **bold** for key terms, findings, and labels
  - Use *italic* for emphasis sparingly
  - Use `backtick` for SPARQL variables, class names, property names, and values
  - Use bullet lists (- item) for enumeration; numbered lists for ordered steps
  - Use ```sparql ... ``` for SPARQL code blocks in answers
  - NO markdown headers (##, ###) — the UI renders these at wrong sizes in reasoning
  - NO horizontal rules (---) or HTML tags
  - Reasoning length and style are controlled by {reasoning_directive} — follow it exactly
"""

from langchain_core.prompts import ChatPromptTemplate

# ─── Reasoning directives ─────────────────────────────────────────────────────
# Injected as {reasoning_directive} into every prompt that has a <reasoning> block.
# Style is always human-analyst, self-reflecting. Length scales with deep_analysis.

_REASONING_FORMAT = (
    "Format for maximum readability — a reader should be able to eyeball this in seconds:\n"
    "- **Bold** every key term, entity name, decision, or finding\n"
    "- *Italic* for uncertainty, caveats, or emphasis ('*this might not hold if...*')\n"
    "- `backtick` for every class name, property, variable, value, or SPARQL term\n"
    "- Bullet points (- item) for lists of options, observations, or reasoning steps\n"
    "- Blank line between distinct thoughts — never write a wall of text\n"
    "- NO markdown headers (##/###). No horizontal rules."
)

REASONING_DIRECTIVE_NORMAL = (
    "Think out loud as a human analyst: notice ambiguity, question assumptions, explain each choice. "
    "Do not narrate what you are doing — show the actual thinking. 2–5 sentences.\n\n"
    + _REASONING_FORMAT
)

REASONING_DIRECTIVE_DEEP = (
    "Think out loud as a senior analyst doing deep due diligence: surface hidden assumptions, "
    "challenge the framing, consider alternative interpretations, flag data gaps, reason through "
    "each decision with precision. Do not narrate — think. Explore fully, do not cut short. "
    "8–15 sentences.\n\n"
    + _REASONING_FORMAT
)

# ─── 1. Intake & Classification ───────────────────────────────────────────────

INTAKE_CLASSIFY_PROMPT = ChatPromptTemplate.from_template(
    """You are the intake classifier for MTI Brain, a treasury & payments intelligence assistant.

Conversation history (for context):
{summary}

User question: "{question}"

Classify the question on three dimensions and output ONLY the JSON object below.

question_type:
  "kg_query"    — use for ANY question about our internal treasury and payments data, including:
                  · Treasury: bank accounts, investment positions, FX forwards, liquidity, counterparty exposure
                  · Payment operations: card processing, acquirer/processor performance, authorization rates,
                    settlement timing, interchange, network fees, chargeback/dispute analytics
                  · Payment methods: ACH, wire, RTP, FedNow, check, virtual card, commercial card
                  · Payment hub: STP rate, exception handling, repair rates, throughput
                  · Fraud and disputes: fraud loss, chargeback ratios, dispute win rates, fraud patterns
                  · Supplier payments: DPO, on-time rate, rebates, virtual card programs
                  · Cross-border: FX costs, corridor analysis, local acquiring
                  · Strategic analytics: cost trends, forecasting, optimization, stress testing, scenario analysis,
                    roadmaps, and comparisons that USE our internal data as the primary source
                  · Questions mentioning "benchmarks" or "peers" are still kg_query if they are primarily
                    asking us to COMPUTE something from our data — route them, do not reject them
  "general_chat" — greeting, explanation, opinion, or meta question not requiring data retrieval
  "rejected"    — ONLY reject if the question requires data we fundamentally cannot have:
                  · Competitor internal data (another company's P&L, internal operations)
                  · External ESG/sustainability ratings from third-party agencies
                  · Macro economic indicators (GDP, CPI, interest rates from external sources)
                  · Personal consumer finance or individual credit information
                  · Stock/equity market prices and trading data
                  · When in doubt, classify as "kg_query" — it is better to attempt and fail gracefully

persona (user preset: {persona_preset} — if set, USE it and do not infer; otherwise infer from phrasing):
  "Analyst"      — precise, tabular, raw numbers
  "Manager"      — aggregated, policy context, trend flags
  "Director"     — risk trends, exposures vs. limits, scenario flags
  "Executive"    — one-page narrative, top-3 risks, executive summary

complexity:
  "simple"   — single fact/balance lookup; one SPARQL query; ≤3s
  "complex"  — multi-join analysis, trend comparison, multi-entity aggregation; 1-3 SPARQL queries; ≤10s
  "advanced" — forecasting, optimization, stress testing, multi-step DAG, strategic scenario analysis; ≤45s

<reasoning>
{reasoning_directive}

Consider: question type, who is asking (language and scope), how many data retrieval steps are needed, and what could go wrong with the classification.
</reasoning>

Output exactly this JSON (no other text):
{{"question_type": "...", "persona": "...", "complexity": "..."}}"""
)

# ─── 2. General Chat ──────────────────────────────────────────────────────────

GENERAL_CHAT_PROMPT = ChatPromptTemplate.from_template(
    """You are MTI Brain, a treasury & payments intelligence assistant.

Conversation summary so far:
{summary}

Recent messages:
{messages}

User says: "{question}"

Respond helpfully. If the user is asking what MTI Brain can do, describe treasury/payments
analytics over investment positions, FX forwards, bank accounts, and counterparty exposure.
Keep the response concise and friendly.

<answer>
Your response here.
</answer>

Suggest 2-3 follow-up questions the user might find useful.
<follow_ups>["...", "...", "..."]</follow_ups>"""
)

# ─── 3. Domain Specialist ─────────────────────────────────────────────────────

DOMAIN_SPECIALIST_PROMPT = ChatPromptTemplate.from_template(
    """You are the domain specialist for MTI Brain. Given the user's question, determine:
1. The primary intent label (one of the standard treasury/payments intents)
2. The routing decision for data retrieval

Question: "{question}"
Persona: {persona}
Complexity: {complexity}

Standard intent labels:
  balance_lookup         — account or position balance as of a date
  counterparty_exposure  — total exposure to a bank across instruments
  fx_exposure            — FX forward positions and net exposure
  investment_positions   — investment book composition by type, company, or bank
  maturity_ladder        — upcoming maturities bucketed by time period
  policy_check           — checking a balance/exposure against a limit or policy
  code_lookup            — find a code (BIC, LEI, account code) for an entity
  trend_analysis         — change over time for positions, exposures, or balances
  scenario_forecast      — forward-looking composition or breach detection
  multi_entity_join      — cross-entity analysis requiring multiple graph traversals

Routing options:
  "kg_only"   — answer fully from KG (LPP Fuseki graph); no policy context needed
  "kg_tribal" — KG data + Tribal graph (policy limits, decisions, watchlists) required
  "hil"       — human-in-loop required (Executive + advanced scenario + breach alert)

<reasoning>
{reasoning_directive}

Focus on: whether policy limits from the Tribal graph are needed, whether this is an Executive + advanced scenario that should trigger HIL, and which intent label best captures the data request.
</reasoning>

Output exactly this JSON:
{{"intent": "...", "routing": "...", "hil_required": false}}"""
)

# ─── 4. SPARQL Generation ─────────────────────────────────────────────────────

SPARQL_GEN_PROMPT = ChatPromptTemplate.from_template(
    """You are a SPARQL expert for the LPP Treasury Knowledge Graph (Apache Jena Fuseki).

Question: "{question}"
Intent: {intent}
Persona: {persona}

Ontology reference:
{ontology_summary}

Resolved ontology terms for this question:
{ontology_terms}

Tribal facts (policy/limit context, if any):
{tribal_facts}

Prior error (if retrying):
{prior_error}

Rules:
- Use PREFIX lpp: <https://lpp.example/ontology#>
- Use PREFIX lppid: <https://lpp.example/id/>
- Only SELECT or ASK queries — no INSERT/DELETE/UPDATE
- Use OPTIONAL for fields that may be absent
- Bind computed values with BIND(... AS ?varname)
- Use FILTER for date ranges (xsd:date literals)
- Order results where natural (ORDER BY DESC(?amount))
- LIMIT results to {max_rows} rows maximum (user-configured preference)
- Variable names should be descriptive (not ?x, ?y)

Respond using exactly these XML tags, replacing the placeholder text with your actual content.
Use only **bold**, *italic*, `backtick` for variables/classes, and bullet lists. No markdown headers.

<reasoning>
{reasoning_directive}

In your reasoning, cover: which classes are the starting point, which object properties join them, which datatype properties provide the answer values, and whether FILTER or OPTIONAL clauses are needed.
</reasoning>

<sparql>
PREFIX lpp: <https://lpp.example/ontology#>
PREFIX lppid: <https://lpp.example/id/>

SELECT ...
WHERE {{
  ...
}}
</sparql>"""
)

# ─── 5. SPARQL Fix ────────────────────────────────────────────────────────────

SPARQL_FIX_PROMPT = ChatPromptTemplate.from_template(
    """You are a SPARQL debugging expert for the LPP Treasury Knowledge Graph.

Original question: "{question}"
Intent: {intent}

Failed SPARQL:
```sparql
{sparql}
```

Error:
{error}

Ontology reference:
{ontology_summary}

Fix the SPARQL query to resolve the error. Keep the same logical intent.
Only change what is needed to fix the error.

Respond using exactly these XML tags, replacing the placeholder text with your actual content:

<reasoning>
{reasoning_directive}

Focus on: the exact error cause, why the original query failed, what specifically must change, and whether the fix preserves the original logical intent.
</reasoning>

<sparql>
PREFIX lpp: <https://lpp.example/ontology#>
PREFIX lppid: <https://lpp.example/id/>

SELECT ...
WHERE {{
  ...
}}
</sparql>"""
)

# ─── 6. Graph Reasoning ───────────────────────────────────────────────────────

GRAPH_REASONING_PROMPT = ChatPromptTemplate.from_template(
    """You are a treasury analytics expert interpreting SPARQL results from the LPP Knowledge Graph.

Question: "{question}"
Intent: {intent}
Persona: {persona}

Query results ({row_count} rows):
{results_sample}

Tribal policy context:
{tribal_facts}

Respond using exactly these XML tags, replacing the placeholder text with your actual content:

<reasoning>
{reasoning_directive}

Cover: key numbers and patterns in the results, concentrations or anomalies worth flagging, any expected data that was absent, and 2-3 concrete evidence citations (entity codes, dates, amounts) that ground the answer.
</reasoning>

Output evidence citations as a JSON list:
<evidence>["citation 1", "citation 2", "..."]</evidence>"""
)

# ─── 7. Verifier ─────────────────────────────────────────────────────────────

VERIFIER_PROMPT = ChatPromptTemplate.from_template(
    """You are a data quality verifier for treasury analytics results.

Question: "{question}"
Intent: {intent}

Results summary:
  Columns: {columns}
  Row count: {row_count}
  Sample values: {sample}

Expected characteristics for intent "{intent}":
  - Monetary columns (amount, marketValue, faceAmount, mtmAmount) should be > 0 for active positions
  - Date columns should be in ISO 8601 format (YYYY-MM-DD)
  - For balance_lookup: expect 1-10 rows
  - For counterparty_exposure: expect 1 row per bank, totals should be positive
  - For investment_positions: expect multiple rows, amounts > 0

Does the result look semantically correct for the question?
Answer with ONLY: PASS or FAIL: <brief reason>"""
)

# ─── 8. Answer Synthesis ─────────────────────────────────────────────────────

ANSWER_SYNTHESIS_PROMPT = ChatPromptTemplate.from_template(
    """You are MTI Brain, a treasury & payments intelligence assistant.

Question: "{question}"
Intent: {intent}
Persona: {persona}

Key data summary:
{col_stats}

Results ({row_count} rows, sample):
{results_sample}

Tribal policy context:
{tribal_facts}

Evidence citations:
{evidence}

Graph reasoning:
{reasoning}

Persona rendering guide:
  Analyst-F    → Present raw data table summary, precise numbers, column definitions
  Manager-F    → Aggregate by key dimension, flag policy breaches, trend vs. prior
  Director-F   → Lead with risk concentration, limits vs. actuals, recommend action
  Executive-F  → One-page narrative, top-3 risks, recommended decision, timeline

Respond using exactly these XML tags, replacing the placeholder text with your actual content:

<reasoning>
{reasoning_directive}

Cover: the headline number or finding, which supporting details matter most for {persona}, any policy flags or anomalies to highlight, and how to structure the answer for maximum clarity.
</reasoning>

<answer>
[Persona-appropriate answer here. Use markdown tables for Analyst-F/Manager-F.
Use prose paragraphs for Director-F/Executive-F. Always cite evidence.]
</answer>

Suggest 3 natural follow-up questions:
<follow_ups>["...", "...", "..."]</follow_ups>"""
)

# ─── 9. Chart Prompt ─────────────────────────────────────────────────────────

CHART_PROMPT = ChatPromptTemplate.from_template(
    """You are a senior BI developer selecting the best chart for a treasury dashboard.

Question: "{question}"
Columns: {columns}
{col_stats}
Sample data (spread across first, middle, last rows):
{sample_rows}
Total rows: {row_count}

IMPORTANT: Sample rows may not show the full range of values. Always check Column stats above
for actual min/max — if a column ranges from 0 to 25, the data has variation even if sample rows show zeros.

DECISION TREE — follow top to bottom, pick the FIRST match:

1. SKIP (return {{}}) if ANY of these are true:
   - Only 1 row, 1 column, or 1 unique category value
   - y/value column contains text, URI strings, or mixed types
   - All values are zero or all values are identical
   - Each row is a unique entity with 8+ columns and no aggregation — this is a TABLE
   - 200+ rows of per-record detail — always skip, the table is the right display

2. PIE — if <=5 categories AND one numeric count/sum metric:
   "What share does each group have?" Works for: instrument type distribution,
   account purpose breakdown, exposure by bank.
   value_key must be a count or sum, NEVER an average or percentage.

3. LINE — if x-axis is a date column (daily, weekly, monthly):
   "How has it changed over time?" Only when x_key contains dates.
   Examples: investment book trend, FX forward maturity profile over dates.

4. AREA — if showing cumulative or stacked composition over time:
   Same as line but for cumulative totals or stacked category breakdowns over dates.

5. BAR — if 6-12 aggregated categories with one numeric metric:
   "Which bank/company has the most exposure?" Ranked comparison.
   x_key MUST have unique values.

6. SCATTER — ONLY when the question explicitly asks about correlation between two
   numeric variables AND the data is pre-aggregated.

KEY RULES:
- ONE y_key only — the primary metric. Multiple y_keys only when same unit AND scale.
- y_keys/value_key MUST be purely numeric columns.
- Use exact column names from the Columns list.
- Title: concise, insight-driven. Labels: clean business language.
- "limit": how many rows to include (omit to use all).

Output ONLY a JSON object, nothing else:
For bar/line/area: {{"type":"...","title":"...","x_key":"col","x_label":"Label","y_keys":["col"],"y_label":"Label","sort":"asc|desc","limit":N}}
For pie: {{"type":"pie","title":"...","name_key":"col","value_key":"col","limit":N}}
For scatter: {{"type":"scatter","title":"...","x_key":"col","x_label":"Label","y_key":"col","y_label":"Label","limit":N}}
Or {{}} for no chart."""
)

# ─── 10. Summarize (Compress) ─────────────────────────────────────────────────

SUMMARIZE_PROMPT = ChatPromptTemplate.from_template(
    """Summarize this treasury analytics conversation for a rolling context window.

{existing_summary_section}

Recent exchanges to summarize:
{recent_exchanges}

Capture:
- Questions asked and their data intent (balance lookups, exposures, policy checks)
- Key entities mentioned (company codes, bank names, account codes, instrument types)
- Any anomalies, policy breaches, or flags that were raised
- The tone/persona the user seems to prefer

<summary>
[Concise 3-5 sentence summary here. Include key entities and data intents.]
</summary>"""
)

# ─── 11. Plan (Outer Loop) ────────────────────────────────────────────────────

PLAN_PROMPT = ChatPromptTemplate.from_template(
    """You are a treasury analytics planning agent for MTI Brain.

Advanced question: "{question}"
Persona: {persona}

Ontology summary:
{ontology_summary}

Decompose this question into a directed acyclic graph (DAG) of sub-questions.
Each sub-question should be independently answerable with a single SPARQL query.
Specify dependencies explicitly.

Budget constraints (from pack.yaml):
  max_subqs: 8
  max_seconds: 45

Guidelines:
- Sub-questions that can be answered independently should have no depends_on
- Sub-questions that need prior results should list depends_on IDs
- Each sub-question must reference specific ontology classes/properties
- Final sub-question(s) should compose/compare prior results

Respond using exactly these XML tags, replacing the placeholder text with your actual content:

<reasoning>
{reasoning_directive}

Cover: what independent data fetches are needed, which computations depend on which fetches, what the final composition step looks like, and whether the sub-question decomposition is minimal yet complete.
</reasoning>

<plan>
{{
  "nodes": [
    {{"id": "sq1", "question": "...", "depends_on": [], "intent": "..."}},
    {{"id": "sq2", "question": "...", "depends_on": ["sq1"], "intent": "..."}}
  ],
  "edges": [["sq1", "sq2"]],
  "budget": {{"max_seconds": 45, "max_subqs": 8}}
}}
</plan>"""
)

# ─── 12. Plan Validator ───────────────────────────────────────────────────────

PLAN_VALIDATOR_PROMPT = ChatPromptTemplate.from_template(
    """Validate this sub-question plan for the MTI Brain pipeline.

Plan JSON:
{plan_json}

Ontology available terms:
{ontology_summary}

Check:
1. DAG is acyclic (no circular depends_on)
2. Each sub-question is answerable with SPARQL against the LPP ontology
3. Total sub-questions ≤ 8
4. Dependencies resolve correctly (no dangling IDs)

Answer with ONLY: VALID or INVALID: <brief reason>"""
)

# ─── 13. Step Reflector ───────────────────────────────────────────────────────

STEP_REFLECTOR_PROMPT = ChatPromptTemplate.from_template(
    """You are a quality judge for a treasury analytics sub-question result.

Sub-question: "{sub_question}"
Intent: {intent}

SPARQL executed:
```sparql
{sparql}
```

Result: {row_count} rows
Sample: {results_sample}
Error (if any): {error}

Does this result correctly answer the sub-question?

Criteria:
- PASS: Result is non-empty, semantically correct, and answers the sub-question
- SKIP: Result is empty (0 rows) with no SPARQL error — this means the data does not exist in the graph; do NOT retry, accept the gap
- SKIP: Sub-question refers to data that the ontology cannot represent
- FAIL: SPARQL itself is syntactically or semantically wrong (bad predicates, wrong joins) — only use when an error message is present or the query logic is clearly broken

Respond using exactly these XML tags, replacing the placeholder text with your actual content:

<reasoning>
{reasoning_directive}

Assess: whether the result shape and values actually answer the sub-question intent, whether empty results mean no data or a bad query, and whether a SKIP is honest or a cop-out.
</reasoning>

Answer with ONLY one of:
PASS
FAIL: <specific reason>
SKIP: <reason why this data is not in the graph>"""
)

# ─── 14. Final Reflector ─────────────────────────────────────────────────────

FINAL_REFLECTOR_PROMPT = ChatPromptTemplate.from_template(
    """You are the final quality judge for a treasury analytics multi-step response.

Original question: "{question}"
Persona: {persona}

Completed sub-questions and their results:
{scratchpad_summary}

Does the assembled results fully answer the original question?

Respond using exactly these XML tags, replacing the placeholder text with your actual content:

<reasoning>
{reasoning_directive}

Assess: which parts of the question are fully answered, which have gaps (data not in graph or SKIP status), whether the partial answer is still useful and honest, and what caveats must be surfaced.
</reasoning>

<reflection>
PASS: Full answer achievable from available results.
OR
PARTIAL: [list what was answered] | Gaps: [list what is missing and why]
OR
FAIL: [specific reason the question cannot be answered at all]
</reflection>"""
)
