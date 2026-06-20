"""Dashboard generation prompt and input formatter.

The system prompt instructs the LLM to produce only the inner body HTML
using the pre-defined CSS class system baked into dashboard_template.html.
All raw technical content (SPARQL, SQL, column names) is excluded from the
visible output.  The target audience is always C-suite / VP executives.
"""

from __future__ import annotations

from typing import Any

from app.services.agents.helpers import _spread_sample

_TABLE_CHAR_BUDGET = 5_000   # ~1,250 tokens — keeps total prompt lean
_ID_SUFFIXES = ("_id", "_key", "_ref", "_uuid", "_hash", "_code")

# Column name translation map — applied at input time so LLM never sees raw DB names
_LABEL_MAP: dict[str, str] = {
    "bank_account_id": "Account", "account_id": "Account",
    "balance": "Closing Balance", "closing_balance": "Closing Balance",
    "total_cash_balance": "Total Cash Position", "total_balance": "Total Balance",
    "totalbalance": "Total Balance",
    "currency": "Currency",
    "wire_transfer": "Wire Transfer", "wire": "Wire Transfer",
    "ach_amount": "ACH Volume", "ach": "ACH Volume",
    "settlement_amount": "Net Settlement",
    "gross_amount": "Gross Volume", "gross": "Gross Volume",
    "net_amount": "Net Amount", "net": "Net Amount",
    "fee_amount": "Processing Cost", "fee": "Processing Cost",
    "approval_rate": "Authorization Rate",
    "decline_rate": "Conversion Leakage",
    "chargeback_rate": "Chargeback Ratio",
    "match_rate": "Reconciliation Match Rate",
    "avg_settlement_delay_days": "Avg. Days-to-Settlement",
    "unmatched_count": "Unreconciled Items",
    "processor_id": "Payment Processor", "processor": "Payment Processor",
    "account_type": "Account Type",
    "institution": "Bank / Institution",
    "fx_rate": "Exchange Rate",
    "naccounts": "No. of Accounts", "num_accounts": "No. of Accounts",
    "row_count": "Records", "count": "Records",
    "statement_date": "Statement Date", "report_date": "Report Date",
    "transaction_date": "Transaction Date", "value_date": "Value Date",
}


def _translate_column(col: str) -> str:
    """Return executive-friendly label for a raw DB column name."""
    key = col.lower().strip()
    if key in _LABEL_MAP:
        return _LABEL_MAP[key]
    # Title-case with spaces for anything else (last resort)
    return col.replace("_", " ").title()


def _format_numeric(val: Any) -> str:
    """Format a numeric value to K/M/B/T shorthand."""
    if val is None:
        return "—"
    try:
        n = float(val)
    except (TypeError, ValueError):
        return str(val)
    if n != n:  # NaN
        return "—"
    neg = n < 0
    a = abs(n)
    if a >= 1e12:
        s = f"{a / 1e12:.2f}T"
    elif a >= 1e9:
        s = f"{a / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"{a / 1e6:.2f}M"
    elif a >= 1e3:
        s = f"{a / 1e3:.2f}K"
    else:
        if isinstance(val, float) or (isinstance(val, str) and "." in str(val)):
            s = f"{a:.2f}"
        else:
            s = f"{a:g}"
    return f"-{s}" if neg else s


def _select_columns(columns: list[str], rows: list[list[Any]]) -> list[int]:
    """Return column indices ordered by dashboard value, trimmed to char budget.

    Priority:
      0 — numeric / financial (always keep)
      1 — short categorical / label / date / status
      2 — long free-text
      3 — ID / hash / reference columns (drop first)

    If all columns fit within the budget estimate, all are returned.
    """
    def _is_numeric(ci: int) -> bool:
        sample = [r[ci] for r in rows[:20] if r[ci] is not None]
        if not sample:
            return False
        try:
            [float(v) for v in sample]
            return True
        except (TypeError, ValueError):
            return False

    def _avg_val_len(ci: int) -> float:
        vals = [str(r[ci]) for r in rows[:20] if r[ci] is not None]
        return sum(len(v) for v in vals) / max(len(vals), 1)

    def _priority(i: int, col: str) -> int:
        if _is_numeric(i):
            return 0
        if any(col.lower().endswith(s) for s in _ID_SUFFIXES):
            return 3
        if _avg_val_len(i) > 40:
            return 2
        return 1

    ranked = sorted(range(len(columns)), key=lambda i: _priority(i, columns[i]))

    # Estimate chars-per-row for ALL columns
    full_row_chars = sum(_avg_val_len(i) + 3 for i in range(len(columns)))
    header_chars   = sum(len(columns[i]) + 3 for i in range(len(columns)))
    sample_n       = min(len(rows), 30)
    if (header_chars + full_row_chars) * sample_n <= _TABLE_CHAR_BUDGET:
        return list(range(len(columns)))   # all columns fit

    # Greedily add highest-priority columns until we'd exceed budget
    selected: list[int] = []
    chars_so_far = 0
    for i in ranked:
        col_chars = (len(columns[i]) + 3 + _avg_val_len(i) + 3) * sample_n
        if chars_so_far + col_chars > _TABLE_CHAR_BUDGET and selected:
            break
        selected.append(i)
        chars_so_far += col_chars

    return sorted(selected)


# ─── System Prompt ────────────────────────────────────────────────────────────

DASHBOARD_SYSTEM_PROMPT = """You are a senior strategy consultant at McKinsey or Bain producing an executive-ready HTML dashboard for a CFO, COO, or Head of Treasury / Payments. The output will be shown on screen and in board meetings. It must reflect Pyramid Principle thinking: conclusion first, then evidence.

══════════════════════════════════════
PYRAMID PRINCIPLE — MANDATORY
══════════════════════════════════════
Every dashboard follows the Minto Pyramid:
1. TITLE IS THE CONCLUSION — the h1 states the answer, not the question.
   BAD:  "Cash Flow Forecast — 4-Week & 3-Month Horizon"
   GOOD: "Cash Position Growing +3.5% to $33.86B — Thin Margins Require Active Monitoring"
2. EXECUTIVE SUMMARY IS THE GOVERNING THOUGHT — 2 sentences that tell the full story.
3. SECTIONS ARE SUPPORTING ARGUMENTS — each section exists to prove or qualify the title.
4. BOTTOM LINE RESTATES WITH ACTION — who must do what by when.

Tone: Confident, forward-looking, action-oriented. Never apologetic. Never hedge without data.
Frame observations as business context and implications, not warnings or problems.

══════════════════════════════════════
OUTPUT RULES
══════════════════════════════════════
• Output ONLY the HTML that goes inside <body> — no <html>, <head>, <style>, or <script> tags.
• MUST start with <main class="wrap"> and end with </main>.
• Use ONLY the class names listed in the DESIGN SYSTEM below. Do not invent class names.
• Never display the user question. Never write "User Query", "Input", "Markdown", "SPARQL", "SQL", or any raw technical content.
• Never expose raw field/column names — translate to executive language.
• No markdown fences, no explanations, no follow-up questions.
• NO icons anywhere. No <i> tags. No data-lucide attributes. No emoji.
• NEVER use inline style attributes (style="color:..." or style="width:...") — use CSS classes only.
• NO alert banners, warning callouts, or danger callouts. Frame everything through implications.

══════════════════════════════════════
DESIGN SYSTEM — CLASS REFERENCE
══════════════════════════════════════

LAYOUT
  .wrap          — main content container (<main class="wrap">)
  .divider       — <hr class="divider"> visual separator between major sections

HEADER (always first)
  .hdr                      — full-width dark navy gradient header
  .hdr-eyebrow              — small uppercase: "EXECUTIVE BRIEFING · [DOMAIN] · [DATE/PERIOD]"
  .hdr-title                — h1: THE CONCLUSION (answer, not question — include key number + implication)
  .hdr-subtitle             — methodology/scope note (one line, neutral)
  .hdr-meta                 — frosted glass strip: 3–4 hero numbers
  .hdr-meta-item            — stat: uppercase label + <strong>VALUE</strong>

  Example:
    <div class="hdr-meta-item">CURRENT POSITION<strong>$32.33B</strong></div>

INFOBAR (always after header — neutral scope facts only)
  .infobar                  — light strip with scope metadata (NO .warn or .bad variants)
  .infobar-item             — label + <span>value</span>
  .idot                     — neutral blue dot (NO .warn or .bad variants — just .idot)
  Limit to 4 items. Content: data-through date, forecast horizon, granularity, key constraint.

EXECUTIVE SUMMARY (ALWAYS — immediately after infobar)
  .exec-summary             — dark navy gradient paragraph block
  Structure:
    <div class="exec-summary">
      <strong>[Confident 3–5 word status phrase]:</strong> [Sentence 1: key number + situation.
      Sentence 2: the critical implication or action owner + timeline.]
    </div>
  Rules: 2 SENTENCES MAXIMUM. Active voice. Present tense. Positive framing.
  Lead with what IS happening (not what could go wrong).

KPI CARDS (when ≥ 3 metrics computable)
  .kpi-grid                 — auto-fit grid; always wrap in .section with h2
  .kcard.kb                 — THE ONLY CARD VARIANT. Blue accent, light gradient background.
                              Do NOT use .kg, .ka, .kr, or .kn — they do not exist.
  .kcard-header             — flex row: .kcap (left) + .kbadge (right)
  .kcap                     — card label (10px uppercase)
  .knum                     — large metric value (34px)
  .ktrend                   — context line below number. Use <span class="up">↑ +X%</span> for positive trends.
  .kbadge                   — tag pill ("BASELINE", "AVG", "AUG 1", "YTD")

  Max 4 KPI cards. Select the 4 most executive-relevant metrics.

  Example:
    <div class="kcard kb">
      <div class="kcard-header">
        <div class="kcap">CURRENT CASH POSITION</div>
        <span class="kbadge">BASELINE</span>
      </div>
      <div class="knum">$32.33B</div>
      <div class="ktrend">Opening position as of May 11, 2026</div>
    </div>

SECTIONS
  .section                  — spacing wrapper
  .section > h2             — auto-styled heading with left accent bar
    Format: "NN · SECTION TITLE" — number sequentially starting from 01
    e.g. "01 · POSITION &amp; TRAJECTORY" / "02 · CASH POSITION TRAJECTORY"

TABLES (single integrated exhibit when possible)
  .table-wrap               — scrollable container; wrap every <table>
  table                     — full-width, dark header, clean body
  td.r                      — right-align numeric cells
  tfoot tr                  — bold totals row
  .data-source              — small italic source citation below table
  Combine related data into ONE table with a "Horizon" or "Period" column rather than
  splitting into separate weekly/monthly/quarterly tables.
  Drop constant-value columns (same value in every row).
  All table cells use default text color — no colored text, no colored backgrounds on cells.

INSIGHT BOX (place ABOVE each major data table — before .table-wrap, not after)
  .insight-box              — blue-accented box for the key implication
  Structure:
    <div class="insight-box">
      <div><strong>Key Implication:</strong> [one sentence — what this data means for the business]</div>
    </div>
  NO icons. NO <i> tags. Use "Key Implication:" label.

STRATEGIC IMPLICATIONS (section 04 — replaces "Risk Flags")
  .flags-list               — <ul> of implications
  .flags-list li            — single item (NO .sev-hi or .sev-lo — never add severity classes)
  .fl-num                   — numbered circle (1, 2, 3)
  .fl-body                  — wrapper for title + desc + impact
  .fl-title                 — bold headline (frame as business observation, not warning)
  .fl-desc                  — description (neutral, contextual — what the data shows)
  .fl-impact                — uppercase label line. Use one of these prefixes:
                              BUSINESS CONTEXT: / IMPLICATION: / SCOPE:
                              (NEVER use "OPERATIONAL IMPACT", "REPORTING IMPACT", or "PENALTY EXPOSURE")

  Max 3 implications. Frame as what the data MEANS, not what's WRONG.
  Example:
    <li>
      <div class="fl-num">1</div>
      <div class="fl-body">
        <div class="fl-title">Margin Concentration Implies Limited Shock Absorption</div>
        <div class="fl-desc">Outflows consume 99.6% of weekly inflows, leaving a thin buffer relative to flow volume.</div>
        <div class="fl-impact">BUSINESS CONTEXT: A 1% outflow increase exceeds weekly net cash generation by 2.5×</div>
      </div>
    </li>

DECISION FRAMEWORK (section 05 — replaces "Recommended Actions")
  .actions-list             — <ul> of decisions/options
  .actions-list li          — single item
  .ac-num                   — numbered circle (1, 2, 3)
  .ac-body                  — wrapper for title + detail + meta
  .ac-title                 — action headline (Validate / Evaluate / Reconcile / Assess / Confirm)
  .ac-detail                — rationale: why this matters and what it enables
  .ac-meta                  — flex row of pills:
    <span class="pill owner">Treasury Operations</span>
    <span class="pill brand">Jun 24, 2026</span>

  Max 3 items. Frame as what to VALIDATE or EVALUATE, not what to FIX.

PERFORMANCE CONTEXT (section 06 — replaces "Benchmarks")
  .bench-grid               — auto-fit grid
  .bench-item               — single metric tile
  .bench-metric             — metric label (uppercase)
  .bench-val                — current value (NO .ok, .warn, .bad — just plain .bench-val)
  .bench-target             — target/context line using · separator
  Max 4 items. Present as context, not as pass/fail judgments.

FORWARD CALENDAR (section 07 — when ≥ 3 future dates derivable)
  .chips                    — flex container
  .chip                     — date chip (NO .co or .cw variants — just plain .chip)
  .chip-dot                 — blue dot inside chip
  Forward-looking only: future deadlines, milestones, review dates.
  Structure:
    <div class="chip">
      <div class="chip-dot"></div>
      <strong>Jun 24</strong> — Description (Owner)
    </div>

BOTTOM LINE (ALWAYS — final element before </main>)
  .callout.hero-bottom      — dark navy gradient callout with white text
  Structure:
    <div class="callout hero-bottom">
      <strong>Bottom Line:</strong> [Confident one-sentence conclusion.
      Restate the trajectory + who should act + by when.]
    </div>
  Tone: Positive, confident. State what IS on track and the one action needed.
  Never use: "however", "despite", "unfortunately", "must not be used until", "provisional".

══════════════════════════════════════
BODY STRUCTURE — EXACT ORDER
══════════════════════════════════════

 1. <main class="wrap">
 2. HEADER (.hdr)             — title = conclusion with key number
 3. INFOBAR (.infobar)        — 4 neutral scope facts with .idot dots
 4. EXECUTIVE SUMMARY         — .exec-summary, 2 sentences max, positive framing
 5. 01 · [DOMAIN KPIs]        — .section > h2 + .kpi-grid (max 4 cards, ALL .kcard.kb)
 6. <hr class="divider" />
 7. 02 · [PRIMARY EXHIBIT]    — .section > h2 + .insight-box + .table-wrap + .data-source
 8. <hr class="divider" />
 9. 03 · SENSITIVITY ANALYSIS — if stress scenarios derivable; table with "vs Base Case" column
10. <hr class="divider" />
11. 04 · STRATEGIC IMPLICATIONS — .flags-list (max 3, no severity classes)
12. <hr class="divider" />
13. 05 · DECISION FRAMEWORK   — .actions-list (max 3, with .pill.owner + .pill.brand)
14. <hr class="divider" />
15. 06 · PERFORMANCE CONTEXT  — .bench-grid (max 4, no color-state classes)
16. <hr class="divider" />
17. 07 · FORWARD CALENDAR     — .chips (neutral, future dates only)
18. BOTTOM LINE               — .callout.hero-bottom
19. </main>

Omit sections 03, 06, 07 if insufficient data. Sections 01–05 and Bottom Line are MANDATORY.

══════════════════════════════════════
EXECUTIVE LANGUAGE
══════════════════════════════════════
Target: CFO, COO, VP Treasury, Head of Payments. No technical terms.

LABEL TRANSLATIONS (never use raw column names):
  bank_account_id / account_id   → "Account"
  balance / closing_balance      → "Closing Balance"
  total_cash_balance             → "Total Cash Position"
  currency → "Currency"    wire_transfer → "Wire Transfer"
  ach_amount → "ACH Volume"    settlement_amount → "Net Settlement"
  gross_amount → "Gross Volume"    net_amount → "Net Amount"
  fee_amount → "Processing Cost"    approval_rate → "Authorization Rate"
  decline_rate → "Conversion Leakage"    chargeback_rate → "Chargeback Ratio"
  match_rate → "Reconciliation Match Rate"
  avg_settlement_delay_days → "Avg. Days-to-Settlement"

NUMERIC SHORTHAND: K=thousands, M=millions, B=billions, T=trillions. Preserve as given.
FORMATTING: Currency ≥$1T→$1.24T, ≥$1B→$1.24B, ≥$1M→$12.4M, ≥$1K→$12.4K. Pct→44.3%. Dates→Jan 30, 2026.

Action verbs: Validate / Evaluate / Reconcile / Assess / Confirm
Owners: Treasury Operations / Finance Ops / Risk & Compliance
NEVER use: "N/A" (use "—"), "SQL", "SPARQL", "query", "schema", "column", "table", "field".
ALWAYS capitalize: Visa, Mastercard, Stripe, Adyen, PayPal, American Express, JPMorgan, Wells Fargo.

══════════════════════════════════════
FRAMING RULES
══════════════════════════════════════
• Lead with what IS working, then note what needs attention
• Use "implies" and "requires" instead of "warns" or "alerts"
• Use "Business Context" / "Implication" / "Scope" labels in .fl-impact
• If data is stale, note it factually in one .infobar-item — do NOT create an alert banner
• If a threshold is breached, frame as an implication in section 04, not as a red-coded KPI
• The dashboard should make an executive feel INFORMED and EMPOWERED, not alarmed
• Every section should answer "so what?" — raw data without interpretation has no place
• If TRIBAL KNOWLEDGE is provided: use it to contextualize metrics against known policies, thresholds, and decisions. Surface relevant limits or commitments in the Strategic Implications (section 04) and Decision Framework (section 05). Cite the policy name or decision in plain English — never as a technical reference.

Generate the body HTML now. Start with <main class="wrap">. Only what goes inside <body>. No markdown. No prose."""


# ─── Input Formatter ──────────────────────────────────────────────────────────

def build_input_markdown(
    question: str,
    answer: str,
    columns: list[str] | None,
    rows: list[list[Any]] | None,
    row_count: int | None,
    intent: str | None,
    follow_ups: list[str] | None,
    col_stats: str | None = None,
    was_truncated: bool = False,
    true_total_rows: int | None = None,
    query_intent: list[str] | None = None,
    sensitivity_table: list[dict] | None = None,
    denominator_context: dict | None = None,
    temporal_projection: dict | None = None,
    tribal_facts: list[dict] | None = None,
) -> str:
    """Format conversation context into INPUT MARKDOWN for the LLM.

    Rows are capped at 50. The question is passed as context only and must
    not appear as visible text in the LLM output.
    """
    parts: list[str] = []

    # ── Question context (invisible to dashboard output) ──
    parts.append(f"## QUESTION CONTEXT (for reference only — do not display)\n{question.strip()}")

    # ── Truncation note (when result exceeded row cap) ──
    if was_truncated:
        count_note = f"{true_total_rows:,} total rows" if true_total_rows else "more rows than the display cap"
        parts.append(
            f"## DATA TRUNCATION NOTE\n"
            f"The query returned {count_note}. The DATA SAMPLE below shows a "
            f"stratified selection of the capped rows. "
            f"Column statistics (distinct counts, min, max, mean) in COLUMN STATISTICS are from "
            f"the full Redshift result where available. "
            f"Do NOT extrapolate totals from the sample rows."
        )

    # ── Intent / domain ──
    if intent:
        parts.append(f"## DOMAIN / INTENT\n{intent.strip()}")

    # ── Query intent (structured analytical contract from intake_classifier) ──
    if query_intent:
        lines = "\n".join(f"- {line}" for line in query_intent)
        parts.append(f"## QUERY INTENT (analytical contract)\n{lines}")

    # ── Column statistics (from _build_data_summary) ──
    if col_stats:
        capped_stats = col_stats[:2000] + (" …" if len(col_stats) > 2000 else "")
        parts.append(f"## COLUMN STATISTICS (full dataset)\n{capped_stats}")

    # ── Sample table — spread-sampled rows · budget-filtered columns ──
    if columns and rows:
        total = row_count or len(rows)

        # Rows: percentage-based, null-filtered, start/Q1/Q3/end bands
        sampled_rows  = _spread_sample(rows)

        # Columns: keep highest-value columns that fit the char budget
        col_indices   = _select_columns(columns, rows)
        # Translate raw DB column names to executive labels at input time
        shown_cols    = [_translate_column(columns[i]) for i in col_indices]
        dropped_count = len(columns) - len(shown_cols)

        header_line = " | ".join(shown_cols)
        sep_line    = " | ".join("---" for _ in shown_cols)

        note = f"{len(sampled_rows)} representative rows of {total} total (start · Q1 · Q3 · end)"
        if dropped_count:
            note += f" · {dropped_count} low-priority columns omitted — see COLUMN STATISTICS"

        table_md = f"## DATA SAMPLE ({note})\n\n| {header_line} |\n| {sep_line} |\n"
        chars = len(table_md)
        rows_written = 0
        for row in sampled_rows:
            line = "| " + " | ".join(
                _format_numeric(row[i]) if row[i] is not None else "—" for i in col_indices
            ) + " |\n"
            if chars + len(line) > _TABLE_CHAR_BUDGET:
                break
            table_md += line
            chars += len(line)
            rows_written += 1

        if rows_written < total:
            table_md += "\n_Full aggregates and distributions are in COLUMN STATISTICS above._"

        parts.append(table_md)

    elif row_count is not None:
        parts.append(f"## DATA\nThe query returned {row_count} record(s). Use the ANALYSIS section to derive metrics.")

    # ── Deep Analysis Enrichment (only present for deep_analysis=True queries) ──
    if denominator_context and denominator_context.get("share") is not None:
        concept = denominator_context.get("concept", "total")
        denom_val = denominator_context.get("value")
        share_pct = round(denominator_context["share"] * 100, 1)
        denom_fmt = _format_numeric(denom_val)
        parts.append(
            f"## DENOMINATOR CONTEXT\n"
            f"The primary metric represents **{share_pct}% of {concept}** "
            f"(denominator: {denom_fmt}). "
            f"Surface this in KPI cards and the executive summary as context for scale."
        )

    if temporal_projection:
        pct = temporal_projection.get("completeness_pct", 0)
        proj = temporal_projection.get("projected_total")
        period_end = (temporal_projection.get("period_end") or "")[:7]
        current = temporal_projection.get("current_total")
        prior_same = temporal_projection.get("prior_period_at_same_point")
        prior_final = temporal_projection.get("prior_period_final")
        prior_start = (temporal_projection.get("prior_period_start") or "")[:7]

        proj_fmt = _format_numeric(proj)
        curr_fmt = _format_numeric(current)
        prior_same_fmt = _format_numeric(prior_same) if prior_same is not None else None
        prior_final_fmt = _format_numeric(prior_final) if prior_final is not None else None

        lines = [
            f"Period is {pct}% complete as of today.",
            f"Current value: {curr_fmt}. Projected {period_end} total: {proj_fmt}.",
        ]
        if prior_same_fmt:
            lines.append(f"At this same point in {prior_start}, the value was {prior_same_fmt}.")
        if prior_final_fmt:
            lines.append(f"That prior period finished at {prior_final_fmt}.")
        parts.append(
            "## TEMPORAL PROJECTION (in-flight period)\n"
            + " ".join(lines)
            + "\nUse this for the projected KPI card, performance context section, and forward calendar."
        )

    if sensitivity_table:
        header = "| Threshold | Count |"
        sep = "|-----------|-------|"
        rows_md = "\n".join(
            f"| {r.get('threshold_label', r.get('threshold_value', ''))} "
            f"{'← current' if r.get('is_current') else ''} | {r.get('count', ''):,} |"
            for r in sensitivity_table
        )
        parts.append(
            "## SENSITIVITY ANALYSIS\n"
            "Use this data for the **03 · SENSITIVITY ANALYSIS** section. "
            "Frame each row as a scenario vs. the current threshold (marked ← current).\n\n"
            f"{header}\n{sep}\n{rows_md}"
        )

    # ── AI Analysis ──
    if answer:
        # Cap analysis at ~4000 chars to prevent total context from ballooning
        capped = answer.strip()[:4000]
        if len(answer.strip()) > 4000:
            capped += "\n… [truncated for context budget]"
        parts.append(f"## ANALYSIS\n{capped}")

    # ── Follow-ups (potential action items) ──
    if follow_ups:
        items = "\n".join(f"- {q}" for q in follow_ups[:6])
        parts.append(f"## SUGGESTED NEXT STEPS\n{items}")

    # ── Tribal Knowledge (policies, limits, decisions from the knowledge graph) ──
    if tribal_facts:
        lines = []
        for fact in tribal_facts[:10]:
            label = (fact.get("label") or "").strip()
            value = (fact.get("value") or "").strip()
            if label and value:
                lines.append(f"**{label}**\n{value}")
        if lines:
            parts.append(
                "## TRIBAL KNOWLEDGE (organizational policies and thresholds — use to contextualize metrics)\n"
                + "\n\n".join(lines)
            )

    return "\n\n---\n\n".join(parts)
