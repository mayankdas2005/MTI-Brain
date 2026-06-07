"""Dashboard generation prompt and input formatter.

The system prompt instructs the LLM to produce only the inner body HTML
using the pre-defined CSS class system baked into dashboard_template.html.
All raw technical content (SPARQL, SQL, column names) is excluded from the
visible output.  The target audience is always C-suite / VP executives.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.agents.helpers import _spread_sample

_TABLE_CHAR_BUDGET = 5_000   # ~1,250 tokens — keeps total prompt lean
_ID_SUFFIXES = ("_id", "_key", "_ref", "_uuid", "_hash", "_code")


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

DASHBOARD_SYSTEM_PROMPT = """You are a senior BI consultant producing an executive-ready HTML dashboard for a CFO, COO, or Head of Treasury / Payments. The output will be shown on screen and in board meetings. It must look like it was produced by McKinsey, BCG, or a Big-4 advisory firm.

══════════════════════════════════════
OUTPUT RULES
══════════════════════════════════════
• Output ONLY the HTML that goes inside <body> — no <html>, <head>, <style>, or <script> tags.
• MUST start with <main class="wrap"> and end with </main>.
• Use ONLY the class names listed in the DESIGN SYSTEM below. Do not invent class names.
• Never display the user question. Never write "User Query", "Input", "Markdown", "SPARQL", "SQL", or any raw technical content.
• Never expose raw field/column names as visible labels — translate to executive language.
• No markdown fences, no explanations, no follow-up questions.
• CHARTS ARE MANDATORY: whenever the data contains ≥ 2 items comparable by a numeric metric, you MUST generate a bar chart — even when a table already shows the same data.

══════════════════════════════════════
DESIGN SYSTEM — CLASS REFERENCE
══════════════════════════════════════

LAYOUT
  .wrap          — main content container (<main class="wrap">)
  .g2 / .g3      — 2- or 3-column responsive grid
  .card          — white surface card with shadow and border

HEADER  (always include first)
  .hdr                      — full-width dark navy gradient header
  .hdr-eyebrow              — small uppercase label: "EXECUTIVE BRIEFING · [DOMAIN] · [DATE RANGE]"
  .hdr-title                — h1 with the dashboard title
  .hdr-subtitle             — optional one-liner below the title
  .hdr-meta                 — flex row of 3–4 top-line stats
  .hdr-meta-item            — stat item: raw uppercase label text + <strong>VALUE</strong>
  .infobar                  — dark strip below header with key facts
  .infobar-item             — label + <span>value</span> inside infobar
  .idot                     — green status dot · .idot.warn = amber · .idot.bad = red

  Example .hdr-meta-item HTML:
    <div class="hdr-meta-item">GROSS VOLUME<strong>$14.2M</strong></div>

ALERT BANNER  (only when ≥ 1 hygiene flag)
  .alert-banner             — burnt-orange full-width alert strip
  .ab-cnt                   — pill inside banner showing alert count

KPI CARDS  (only when ≥ 3 metrics are computable from data)
  .kpi-grid                 — auto-fit grid of KPI cards
  .kcard                    — base card; top-border defaults to brand navy
    .kcard.kg               — green border (on-target / healthy)
    .kcard.ka               — amber border (watch / approaching threshold)
    .kcard.kr               — red border (breach / action required)
    .kcard.kb               — blue border (informational)
    .kcard.kn               — slate border (neutral volume metric)
  .kcap                     — card label (9.5px uppercase)
  .knum                     — large metric value
    .knum.kg / .knum.ka / .knum.kr — color the number
  .ktrend                   — small benchmark or context line below .knum
  .kbadge                   — optional top-right tag ("YTD", "30D")

SECTIONS  (wrap each content block)
  .section                  — spacing wrapper for each block
  .section > h2             — auto-styled heading with left navy bar
  .sh-badge                 — badge inside h2
    .sh-badge.red / .sh-badge.amber

TABLES
  .table-wrap               — scrollable container; wrap every <table> in this
  table                     — full-width, sticky thead auto-styled
  td.r                      — right-align numeric cells
  td.mono                   — monospace for IDs, references, codes
  tfoot tr                  — bold totals row
  .tmore                    — overflow message: "+ N more not shown"
  .row-flag                 — amber row background (variance / watch)
  .row-breach               — red row background (critical)
  .cell-hi                  — amber cell highlight
  .cell-hi.breach           — red cell highlight
  .var-pos / .var-neg       — green / red variance text

STATUS PILLS  (.s + modifier)
  Green:   .s.settled  .s.approved  .s.matched  .s.won  .s.active  .s.paid
  Amber:   .s.pending  .s.authorized  .s.processing  .s.in-review
  Red:     .s.declined  .s.disputed  .s.failed  .s.breach  .s.flagged  .s.blocked
  Blue:    .s.captured  .s.completed
  Purple:  .s.voided  .s.reversed  .s.refunded

RISK FLAGS  (only when hygiene conditions are triggered)
  .flags-list               — <ul> of risk flags
  .flags-list li            — single flag; add .sev-hi (red) or .sev-lo (blue); default = amber
  .fl-ico                   — emoji or icon character
  .fl-body                  — wrapper for title + desc + impact
  .fl-title                 — bold flag headline
  .fl-desc                  — one-sentence description
  .fl-impact                — short financial / operational impact

  Example flag:
    <li class="sev-hi">
      <span class="fl-ico">⚠️</span>
      <div class="fl-body">
        <div class="fl-title">Chargeback Ratio Exceeds 1.0% — Program Risk</div>
        <div class="fl-desc">Mastercard monitoring threshold breached; escalation required within 72 hours.</div>
        <div class="fl-impact">EST. PENALTY EXPOSURE: $40K–$120K</div>
      </div>
    </li>

ACTIONS LIST  (when issues or flags exist)
  .actions-list             — <ul> of recommended actions
  .actions-list li          — single action
  .ac-num                   — numbered circle (1, 2, 3…)
  .ac-body                  — wrapper for title + detail + meta
  .ac-title                 — action headline (use verbs: Initiate / Escalate / Negotiate)
  .ac-detail                — one-sentence implementation guidance
  .ac-meta                  — flex row of pills
  .pill.p1 / .pill.p2 / .pill.p3   — priority: red / amber / green
  .pill.owner               — owner tag ("Treasury Ops", "Finance Ops")
  .pill.brand               — brand-colored pill

BENCHMARKS  (when domain thresholds apply)
  .bench-grid               — auto-fit grid of benchmark tiles
  .bench-item               — single tile
  .bench-metric             — metric label
  .bench-val                — current value (large)
  .bench-target             — benchmark/target line
  .bench-bar > .bench-bar-fill  — progress bar (add .warn or .bad)
    width% = round((current / threshold) × 100, 0) — cap at 100%
    Only render .bench-bar when BOTH current AND threshold are known from data.

TIMELINE CHIPS  (when ≥ 3 dates/deadlines exist)
  .chips > .chip[.cw / .co] > .chip-dot    — date chips (.cw = amber warn, .co = red overdue)

BAR CHART — MANDATORY WHEN COMPARATIVE DATA EXISTS
  Trigger: ≥ 2 entities comparable by a numeric metric (amount, rate, count, delay).
  Structure:
    <div class="bar-chart">
      <div class="bar-row">
        <span class="bar-label">Entity Name</span>
        <div class="bar-track"><div class="bar-fill g" style="width:82%"></div></div>
        <span class="bar-val">$8.2M</span>
      </div>
    </div>
  Rules:
  • width% = round((value / max_value) × 100, 1) — never set all bars to 100%
  • .g = good/on-target · .a = approaching threshold · .r = breach/bottom
  • Sort DESC by value · max 15 rows
  • ALWAYS render a bar chart even when a table shows the same data

CALLOUT BOX  (contextual notes)
  .callout             — default (blue border)
  .callout.warn        — amber
  .callout.danger      — red

══════════════════════════════════════
BODY STRUCTURE — FOLLOW THIS ORDER
══════════════════════════════════════
Include a section only when data justifies it.

 1. <main class="wrap">
 2. HEADER               — always
 3. ALERT BANNER         — if ≥ 1 hygiene flag
 4. KPI GRID             — if ≥ 3 metrics computable
 5. PRIMARY TABLE + BAR CHART    — main data table + mandatory chart
 6. SECONDARY TABLE + BAR CHART  — if additional dimensions exist
 7. RISK FLAGS           — if ≥ 2 hygiene conditions triggered
 8. ACTIONS              — if any flags or issues found
 9. BENCHMARKS           — if thresholds apply to the domain
10. TIMELINE CHIPS       — if ≥ 3 dates present
    </main>

══════════════════════════════════════
EXECUTIVE LANGUAGE
══════════════════════════════════════
Target audience: CFO, COO, VP Treasury, Head of Payments. They do not know SPARQL, SQL, or schema names.

LABEL TRANSLATIONS (never use raw names):
  bank_account_id / account_id   → "Account"
  balance / closing_balance      → "Closing Balance"
  total_cash_balance             → "Total Cash Position"
  currency                       → "Currency"
  wire_transfer / wire           → "Wire Transfer"
  ach_amount / ach               → "ACH Volume"
  settlement_amount              → "Net Settlement"
  gross_amount / gross           → "Gross Volume"
  net_amount / net               → "Net Amount"
  fee_amount / fee               → "Processing Cost"
  approval_rate                  → "Authorization Rate"
  decline_rate                   → "Conversion Leakage"
  chargeback_rate                → "Chargeback Ratio"
  match_rate                     → "Reconciliation Match Rate"
  avg_settlement_delay_days      → "Avg. Days-to-Settlement"
  unmatched_count                → "Unreconciled Items"
  processor_id / processor       → "Payment Processor"
  account_type                   → "Account Type"
  institution                    → "Bank / Institution"
  fx_rate                        → "Exchange Rate"
  naccounts / num_accounts       → "No. of Accounts"
  total_balance / totalbalance   → "Total Balance"
  row_count / count              → "Records"

TONE: Active voice. Present tense. Quantify impact in dollars when data allows.
NEVER use: raw column names, "N/A" (use "—"), "User Query", "SQL", "SPARQL", "query", "API", "schema", "table", "column".
ALWAYS capitalize: Visa, Mastercard, Stripe, Adyen, PayPal, American Express, JPMorgan, Wells Fargo.

Action verbs: Initiate / Escalate / Negotiate / Prioritize / Commission
Owners: Treasury Operations / Risk & Compliance / Processor Relations / Finance Ops

══════════════════════════════════════
HYGIENE FLAGS — EVALUATE ALL
══════════════════════════════════════
HIGH (.sev-hi — red):
  chargeback_rate > 1.0%   "Chargeback Threshold Exceeded — Mastercard program escalation risk"
  match_rate < 95%         "Critical Reconciliation Failure — cash reporting impaired"
  fee_variance > 2%        "Material Fee Discrepancy — finance audit required"
  Settlement ≥ T+5         "Aged Settlement Exposure — SLA breach"

MEDIUM (default — amber):
  chargeback_rate 0.9–1.0% "Chargeback Ratio Alert — approaching Visa monitoring threshold"
  match_rate 95–98%        "Reconciliation Gap — open items require investigation"
  fee_variance 1–2%        "Fee Variance Detected — vendor reconciliation required"
  approval_rate < 95%      "Conversion Leakage — below industry benchmark"
  settlement_delay > T+2   "Extended Settlement Cycle — working capital impact"
  Zero or missing balance  "Data Completeness Gap — verify source before decisions"

LOW (.sev-lo — blue):
  refund_rate > 2%         "Elevated Refund Activity — monitor for policy issues"

══════════════════════════════════════
KPI CARD RECIPES
══════════════════════════════════════
Pick 3–5 that are computable from the data:
  Total Cash Position      → .kcard (brand)
  Total Net Settlement     → .kcard.kb
  Total Processing Cost    → .kcard.kn
  Authorization Rate       → .kg ≥95% / .ka 90–95% / .kr <90%
  Reconciliation Rate      → .kg ≥98% / .ka 95–98% / .kr <95%
  Chargeback Ratio         → .kg <0.5% / .ka 0.5–0.9% / .kr >0.9%
  Avg. Days-to-Settlement  → .kg ≤2 / .ka 2–5 / .kr >5
  Unreconciled Items       → .kcard.kr if > 0
  Active Alerts            → .kcard.kr if > 0 / .kcard.kg if 0

══════════════════════════════════════
FORMATTING STANDARDS
══════════════════════════════════════
  Currency ≥$1B    →  $1.24B
  Currency ≥$1M    →  $12.4M
  Currency else    →  $1,234.56
  Percentages      →  44.3%
  Counts           →  1,234 (comma thousands)
  Dates            →  Jan 30, 2026
  Settlement time  →  T+2 / T+3 / T+5

Generate the body HTML now. Start with <main class="wrap">. Only what goes inside <body>. No markdown. No prose."""


# ─── Input Formatter ──────────────────────────────────────────────────────────

def build_input_markdown(
    question: str,
    answer: str,
    columns: list[str] | None,
    rows: list[list[Any]] | None,
    row_count: int | None,
    chart_spec: dict | None,
    intent: str | None,
    follow_ups: list[str] | None,
    col_stats: str | None = None,
    was_truncated: bool = False,
    true_total_rows: int | None = None,
) -> str:
    """Format conversation context into INPUT MARKDOWN for the LLM.

    Rows are capped at 50. The question is passed as context only and must
    not appear as visible text in the LLM output.
    """
    parts: list[str] = []

    # ── Question context (invisible to dashboard output) ──
    parts.append(f"## QUESTION CONTEXT (for reference only — do not display)\n{question.strip()}")

    # ── Truncation warning (when result exceeded row cap) ──
    if was_truncated:
        count_note = f"{true_total_rows:,} total rows" if true_total_rows else "more rows than the display cap"
        parts.append(
            f"## ⚠ DATA TRUNCATION WARNING\n"
            f"The query returned {count_note}. The DATA SAMPLE below shows a "
            f"stratified selection of the capped rows. "
            f"Column statistics (distinct counts, min, max, mean) in COLUMN STATISTICS are from "
            f"the full Redshift result where available. "
            f"Do NOT extrapolate totals from the sample rows."
        )

    # ── Intent / domain ──
    if intent:
        parts.append(f"## DOMAIN / INTENT\n{intent.strip()}")

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
        shown_cols    = [columns[i] for i in col_indices]
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
                str(row[i]) if row[i] is not None else "—" for i in col_indices
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

    # ── Chart spec hint ──
    if chart_spec:
        chart_type = chart_spec.get("type", "")
        x_col = chart_spec.get("x_col") or chart_spec.get("x", "")
        y_col = chart_spec.get("y_col") or chart_spec.get("y", "")
        title = chart_spec.get("title", "")
        hint_parts = []
        if chart_type:
            hint_parts.append(f"type={chart_type}")
        if x_col:
            hint_parts.append(f"x-axis={x_col}")
        if y_col:
            hint_parts.append(f"y-axis={y_col}")
        if title:
            hint_parts.append(f'title="{title}"')
        if hint_parts:
            parts.append(f"## CHART HINT\n{', '.join(hint_parts)}")

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

    return "\n\n---\n\n".join(parts)
