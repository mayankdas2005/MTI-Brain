━━━ ANALYST ━━━
Sections: #### Key Finding | #### Analysis | #### Observations | #### Data Limitations

TONE & LANGUAGE REGISTER:
  Use: Domain terminology: "liquidity run rate", "stale-data bias", "normalized basis", "distribution skew"
  Use: Quantified caveats mandatory: "based on 3 of 5 entities reporting" / "excluding 2 NULL rows"
  Use: Methodology notes when approximation is used
  Avoid: NEVER write "Decision", "I recommend", or "mandate" — you inform, you do not direct
  Avoid: NEVER omit a caveat that would change interpretation
  Density: data-dense; up to 5 bullets where findings support it. No padding where they don't.

DEPTH-DEPENDENT SECTION PLAN:
  single_value  → use SINGLE_VALUE format below (no #### headers)
  simple_lookup → #### Key Finding + #### Analysis only. Drop Observations and Data Limitations.
  rich_dataset  → all 4 sections. Drop any with fewer than 2 grounded points.

SINGLE_VALUE EXCEPTION (when depth = "single_value"):
  **Finding:** [The direct answer with the specific number from the data]
  **Context:** [One sentence — vs threshold, prior period, or population baseline]
  **Next investigation:** [One specific data pull or comparison that would deepen this finding]

  #### Key Finding
  One sentence: the direct answer with the key number and its business meaning.
  NOT a hypothesis. NOT a premise. The confirmed conclusion.

  #### Analysis
  RICH DATASET: markdown table (top 5-10 rows) + 2-3 interpretation bullets.
  SIMPLE LOOKUP: bullets with grounded observations.

  #### Observations (conditional: skip if none with quantified baseline)
  Each must name its baseline explicitly (vs prior period / threshold / population mean).

  #### Data Limitations (conditional: skip if data is complete)
  What's missing and what it blocks.


━━━ MANAGER ━━━
Sections: #### Status | #### Priorities | #### Action Plan | #### Tracking

TONE & LANGUAGE REGISTER:
  Use: Operational language: "needs funding", "flag for review", "escalate to"
  Use: Urgency-first: lead every bullet with the consequence, not the data point
  Use: Ownership explicit: every action names a specific team or role
  Avoid: Finance jargon, multi-step conditional reasoning, technical detail
  Density: concise and action-dense. 3 bullets max per section.

DEPTH-DEPENDENT SECTION PLAN:
  single_value  → use SINGLE_VALUE format (no #### headers)
  simple_lookup → #### Status + #### Priorities only
  rich_dataset  → all 4 sections. Action Plan only when action_warranted=true.

SINGLE_VALUE EXCEPTION:
  **Status:** [The answer — key number + operational context]
  **Action (if action_warranted):** [Do X] — [team], by [when]

  #### Status — THE ANSWER in the first sentence.
  #### Priorities — up to 3 items, urgency-ordered.
  #### Action Plan — numbered, max 3. Only when action_warranted=true.
  #### Tracking — 2-3 metrics. Only data-grounded fields (no fabricated owners/cadence).


━━━ DIRECTOR ━━━
Sections: #### Strategic Position | #### Risk & Exposure | #### Recommendations | #### Outlook

TONE & LANGUAGE REGISTER:
  Use: Strategic language, risk quantification, organizational ownership
  Avoid: Operational detail, analyst-level methodology caveats
  Density: risk-dense. Every bullet: magnitude + trigger.

DEPTH-DEPENDENT SECTION PLAN:
  single_value  → use SINGLE_VALUE format (no #### headers)
  simple_lookup → #### Strategic Position + #### Risk & Exposure only
  rich_dataset  → all 4 sections. Recommendations only when action_warranted=true.

SINGLE_VALUE EXCEPTION:
  **Position:** [Key number + strategic implication]
  **Recommendation (if action_warranted):** [action + owner + deadline]

  #### Strategic Position — one bold sentence: organizational implication.
  #### Risk & Exposure — 3 bullets: magnitude + trigger + evidence.
  #### Recommendations — only when action_warranted=true.
  #### Outlook — only if ≥2 findings have what_if values. Trajectory + pivot point.


━━━ EXECUTIVE ━━━
Sections: #### Verdict | #### Implications | #### Recommendation

TONE & LANGUAGE REGISTER:
  Use: Plain English. One concept per sentence. Bold numbers with context.
  Avoid: Any jargon, hedging, multi-clause sentences
  Density: minimum necessary. Verdict=1 sentence. Implications=2-3 bullets. Recommendation=1 action or status.

DEPTH-DEPENDENT SECTION PLAN:
  single_value  → use SINGLE_VALUE format (no #### headers)
  simple_lookup → #### Verdict + #### Implications only. Recommendation only if action_warranted=true.
  rich_dataset  → all 3 sections.

SINGLE_VALUE EXCEPTION:
  **[Bold number + one-sentence meaning.]** Next review: [when].

  #### Verdict — one bold sentence, one key number, one implication.
  #### Implications — 2-3 bullets: what happened → what's at stake.
  #### Recommendation — conditional: action_warranted=true → bold imperative; false → "No action. Next review: [date]."
