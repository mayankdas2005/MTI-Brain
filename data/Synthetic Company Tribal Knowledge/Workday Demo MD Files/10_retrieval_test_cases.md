---
document_id: RETRIEVAL-VALIDATION-TREASURY-LIQUIDITY-2026-05
source_type: retrieval_validation_record
source_system: SharePoint
repository_path: /Finance/Treasury/Knowledge Records/Retrieval Validation/2026/May/
classification: Internal - Treasury Knowledge Validation
access_scope: Deep Analysis only
created_date: 2026-05-17
business_domain: treasury
related_process: liquidity knowledge retrieval validation
related_prompts:
  - total_liquidity_today
  - cash_forecast_4_week_3_month
  - liquidity_threshold_seasonality_review
  - cfo_treasury_briefing
  - judgment_before_cfo_briefing
  - reasoning_path_explainability
related_entities:
  - Group Treasury
  - CFO Office
  - Audit & Risk Committee
  - Group Treasury Policy
  - 13-week cash forecast
related_commitments:
  - informal_minimum_liquidity_250m
  - formal_minimum_liquidity_200m
  - target_operating_buffer_300m
deep_analysis_relevance: high
confidence: high
sme_validated: true
---

# Treasury Liquidity Retrieval Validation — May 2026

## Purpose

Validate that treasury liquidity questions retrieve the correct internal records and preserve the distinction between formal policy, forecast facts, management commitments, and recommended action.

## Validation cases

### Case 1 — Current liquidity

**User question:** What is our total liquidity available today?

**Expected retrieval emphasis:**

- Current liquidity snapshot from CFO monthly notes or treasury forecast working notes.
- Definition of total liquidity from formal policy if required.
- Components: unrestricted cash, available committed credit, less restricted or trapped balances.

**Expected answer direction:**

Report total liquidity in USD and identify whether the current position is above the target operating buffer.

**Relevant records:**

- CFO monthly finance / treasury meeting notes
- Treasury forecast review working notes
- Group Treasury Policy

### Case 2 — Four-week and three-month forecast

**User question:** Build a 4 week and 3 month cash forecast using historical inflows and outflows.

**Expected retrieval emphasis:**

- 4-week forecast table.
- 13-week forecast table.
- Seasonality assumptions.
- Historical inflow / outflow adjustments.

**Expected answer direction:**

Summarize the near-term liquidity trajectory and identify the projected trough.

**Relevant records:**

- Treasury forecast review working notes
- Treasury Committee liquidity review notes

### Case 3 — Minimum threshold and seasonality

**User question:** Factor in seasonality from the same period last year, and highlight any week where projected liquidity falls below our USD 200M minimum threshold.

**Expected retrieval emphasis:**

- Formal USD 200M hard minimum liquidity threshold.
- Target operating buffer.
- Week-by-week forecast.
- Prior-year seasonal assumptions.

**Expected answer direction:**

State that no week falls below the USD 200M hard minimum, while also noting that several weeks fall below the target operating buffer and require Amber monitoring or action before the CFO briefing.

**Relevant records:**

- Group Treasury Policy
- Treasury forecast review working notes
- Treasury Committee liquidity review notes

### Case 4 — CFO treasury briefing

**User question:** Give me a one-page CFO briefing on treasury health: liquidity, debt, FX, interest rate exposure, and key risks.

**Expected retrieval emphasis:**

- CFO expectations from meeting notes.
- Liquidity forecast and forecast trough.
- Risk watchlist.
- Action register.
- Any relevant debt, FX, and interest-rate summary if available.

**Expected answer direction:**

Produce an executive-ready briefing that does not stop at current liquidity. Include the forecast trough, policy posture, drivers, risk rating, and recommended actions.

**Relevant records:**

- CFO monthly finance / treasury meeting notes
- Treasury risk watchlist notes
- CFO action register liquidity items
- Treasury forecast review working notes

### Case 5 — Judgment before CFO briefing

**User question:** Does this treasury position require action before the CFO briefing?

**Expected retrieval emphasis:**

- Formal policy status.
- Forecast low point.
- CFO / Audit & Risk Committee management commitment.
- SME validation note.
- Action register.
- Transcript excerpt.

**Expected answer direction:**

The answer should be more specific than ordinary threshold analysis. It should state that the position requires action before the CFO briefing because:

1. the forecast is Amber under formal policy; and
2. the forecast low point falls below the active CFO commitment to maintain at least USD 250M total liquidity through the current planning window.

The answer should explicitly avoid saying that the USD 250M level is a formal policy threshold.

**Relevant records:**

- Audit & Risk Committee prebrief liquidity commitment excerpt
- SME validation note liquidity commitment
- CFO action register liquidity items
- Teams transcript excerpt CFO briefing prep
- Treasury risk watchlist notes
- Treasury forecast review working notes

### Case 6 — Reasoning path

**User question:** Show the reasoning path behind that recommendation.

**Expected retrieval emphasis:**

- Graph relationship seed.
- Committee notes.
- SME validation note.
- Forecast working notes.
- CFO action register.

**Expected answer direction:**

The answer should show a relationship path similar to:

CFO made commitment to Audit & Risk Committee → commitment sets active USD 250M management comfort level → formal policy still has USD 200M hard floor → forecast Week 8 projects approximately USD 231M → forecast remains formally above hard floor but below active management commitment → Board-commitment risk exists → CFO should receive mitigation options and messaging before the briefing.

**Relevant records:**

- Graph relationship seed
- Audit & Risk Committee prebrief liquidity commitment excerpt
- SME validation note liquidity commitment
- Treasury forecast review working notes
- CFO action register liquidity items

## Validation rules

- Formal policy facts and institutional memory should be retrieved as separate evidence types.
- The USD 200M formal hard minimum must remain authoritative.
- The USD 250M management commitment must be treated as active and material, but not as a formal policy amendment.
- A forecast low point around USD 231M should trigger a Board-commitment risk recommendation even though it remains above the USD 200M hard minimum.
- Recommendations should include mitigation options or CFO messaging, not only monitoring language.
