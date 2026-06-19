---
document_id: RETRIEVAL-TEST-CASES-NET30-NET90-2026-06-10
title: Retrieval Test Cases - Net-30 / Net-90 Supplier Payment-Term Demo
source_type: retrieval_test_cases
classification: Internal - Demo QA / Data AI / GR_TREASURY
access_control: Development and Deep Analysis validation
created_date: 2026-06-10
business_domain: treasury_working_capital
process: supplier_payment_terms_rollout
company_context:
  parent_company:
    name: GlobalRetail Holdings plc
    code: GR_HOLDINGS
    lei: LEI-GRHOLD
    country: 'GB (UK)'
    functional_currency: GBP
  group_structure:
    description: GlobalRetail group of 25 legal entities
    total_entities: 25
    parent_entity_code: GR_HOLDINGS
  treasury_vehicle:
    name: GlobalRetail Treasury Ltd
    code: GR_TREASURY
    functional_currency: GBP
    role: in-house treasury vehicle
  regional_subholdings:
    - name: GlobalRetail Europe BV
      code: GR_EU_BV
    - name: GlobalRetail APAC Pte
      code: GR_APAC_PTE
    - name: GlobalRetail LATAM SA
      code: GR_LATAM_SA
  example_operating_companies:
    - GlobalRetail US Inc
    - GlobalRetail UK Ltd
    - GlobalRetail Deutschland GmbH
    - GlobalRetail Japan KK
related_terms:
  - retrieval QA
  - Deep Analysis
  - Net-30 payment-term
  - Net-30 policy rollout
  - Net-90 policy rollout
  - supplier notices
related_suppliers:
  - supplier_code: TP_APPAREL_0014
    vendor_name: Jakarta Apparel Mfg 14
    internal_exception_alias: Vendor X
  - supplier_code: TP_LOGI_0007
    vendor_name: XPO Logistics Region 7
    internal_exception_alias: Vendor Y
  - supplier_code: TP_CAPEX_0001
    vendor_name: CAPEX Vendor 1
    internal_exception_alias: Vendor Z
supplier_entity_context:
  TP_APPAREL_0014:
    vendor_name: Jakarta Apparel Mfg 14
    exception_alias: Vendor X
    relevant_regional_entity: GR_APAC_PTE
    relevant_operating_company: GlobalRetail Japan KK
  TP_LOGI_0007:
    vendor_name: XPO Logistics Region 7
    exception_alias: Vendor Y
    relevant_operating_company: GlobalRetail US Inc
  TP_CAPEX_0001:
    vendor_name: CAPEX Vendor 1
    exception_alias: Vendor Z
    relevant_regional_entity: GR_EU_BV
    relevant_operating_companies:
      - GlobalRetail UK Ltd
      - GlobalRetail Deutschland GmbH
deep_analysis_relevance: high
---

# Retrieval Test Cases - Net-30 / Net-90 Supplier Payment-Term Demo

## Group and entity context

These notes relate to **GlobalRetail Holdings plc** (`GR_HOLDINGS`, LEI `LEI-GRHOLD`), the UK parent company of a 25-entity GlobalRetail group. The parent functional currency is **GBP**. Working-capital analysis, supplier-notice decisions, and CFO briefing values in this folder should be treated as group-level analysis for `GR_HOLDINGS` unless a source line identifies a specific regional or operating-company dependency.

**GlobalRetail Treasury Ltd** (`GR_TREASURY`, GBP) is the in-house treasury vehicle coordinating the Net-30 / Net-90 supplier payment-term workstream with the CFO Office, AP Operations, Procurement, Sourcing, and Store Development Finance. Regional context may involve **GlobalRetail Europe BV** (`GR_EU_BV`), **GlobalRetail APAC Pte** (`GR_APAC_PTE`), **GlobalRetail LATAM SA** (`GR_LATAM_SA`), and country operating companies such as GlobalRetail US Inc, GlobalRetail UK Ltd, GlobalRetail Deutschland GmbH, and GlobalRetail Japan KK.

## Entity scope for retrieval testing

Retrieval testing should verify that Deep Analysis grounds the payment-term workflow in the GlobalRetail group structure before applying supplier exception logic. The expected final answer should treat the payment-term rollout as a `GR_HOLDINGS` / `GR_TREASURY` initiative, with cash benefit and DPO impact stated in GBP and exceptions tied to the relevant regional or operating-company context.


## GlobalRetail-specific retrieval expectations

Deep Analysis should identify the group context before applying the supplier exception logic:

- Parent company: GlobalRetail Holdings plc (`GR_HOLDINGS`), LEI `LEI-GRHOLD`, country `GB (UK)`, functional currency GBP.
- Treasury owner: GlobalRetail Treasury Ltd (`GR_TREASURY`), the in-house treasury vehicle coordinating the Net-30 and Net-90 policy rollout.
- Regional context: GlobalRetail APAC Pte (`GR_APAC_PTE`) / GlobalRetail Japan KK for Vendor X, GlobalRetail US Inc for Vendor Y, and GlobalRetail Europe BV (`GR_EU_BV`) / GlobalRetail UK Ltd / GlobalRetail Deutschland GmbH for Vendor Z.
- CFO-ready Cash benefit, DPO impact, and exception-adjusted recommendation should be stated as a GlobalRetail Holdings plc group view.

## Purpose

Validate that the Deep Analysis pathway retrieves realistic institutional context from markdown source artifacts and changes the recommendation only when the prompt requires supplier-notice judgment or exception-aware executive packaging.

The first four prompts can be answered primarily from structured AP / invoice data. The markdown corpus should add continuity and caveats when Deep Analysis is active. The fifth prompt should visibly change the recommendation because the markdown corpus contains meeting-note and exception-log context that is not formal policy.

## Test case summary

| Test ID | User prompt | Expected primary data path | Expected Deep Analysis retrieval | Expected answer behavior |
|---|---|---|---|---|
| RT-001 | Which suppliers are included in the Net-30 payment-term? | Structured AP vendor master and open invoice data | Folder index and AP/Treasury working session if Deep Analysis is active | Return Net-30 supplier population. If Deep Analysis is active, note that Net-30 is the starting cohort for a Net-90 rollout and that exception handling exists for supplier notices. |
| RT-002 | What working-capital benefit do we get from extending the open invoices of Net-30 suppliers to Net-90? | Structured AP invoices, terms, and payment timing | CFO monthly discussion and AP/Treasury working session | Return gross GBP working-capital benefit for GlobalRetail Holdings plc. If Deep Analysis is active, explain that gross benefit should be separated from exception-adjusted benefit. |
| RT-003 | Which Net-30 suppliers create the largest cash-flow impact, if the payment terms are renegotiated to Net-90? | Structured open invoices ranked by cash-flow impact | Supplier exception register, CFO meeting, seasonal readiness notes | Return largest cash-flow impact suppliers from structured data. If Deep Analysis is active, flag that CAPEX Vendor 1 requires review and that Jakarta Apparel Mfg 14 and XPO Logistics Region 7 carry execution risk even if not top-ranked by value. |
| RT-004 | Summarize the payment-term policy rollout changing it from Net-30 to Net-90, including cash benefit, impacted supplier count, DPO impact, exceptions, and recommendation. | Structured AP / invoice data plus synthesis agent | CFO monthly discussion, exception log, supplier exception register, SME validation note | Produce a CFO-ready GlobalRetail Holdings plc summary with gross benefit, impacted count, DPO impact, exceptions, and recommendation. Deep Analysis should add exception-adjusted recommendation. |
| RT-005 | Before we send supplier notices, which suppliers should we exclude from the Net-30 policy rollout? | Structured AP data can identify formal exclusions only | CFO monthly discussion, Procurement seasonal allocation review, CFO exception log, Teams transcript, exception register, graph seed | Change recommendation: suppress Jakarta Apparel Mfg 14 for GR_APAC_PTE / Japan seasonal allocation, hold XPO Logistics Region 7 for GlobalRetail US Inc logistics, and route CAPEX Vendor 1 for GR_EU_BV / UK / Germany store-development review. |

## Detailed expected behavior

### RT-001 - Net-30 supplier population

**Prompt:** Which suppliers are included in the Net-30 payment-term?

**Expected standard answer:**

- Retrieve suppliers with Net-30 activity from AP vendor master / invoice data.
- Present supplier code, vendor name, current term activity, invoice count or open invoice exposure if available.
- Do not infer exclusions from tribal knowledge unless Deep Analysis is active.

**Expected Deep Analysis addition:**

- Retrieve `00_sharepoint_folder_index.md` and `03_ap_treasury_supplier_notice_working_session_2026-06-03.md`.
- Recognize that Net-30 policy rollout and Net-90 policy rollout are used interchangeably by the business teams.
- Add a caveat: inclusion in the Net-30 population does not equal clearance for supplier notices.

**Do not do:**

- Do not remove suppliers from the Net-30 population just because they have exception context.
- Do not present meeting notes as formal policy.

### RT-002 - Working-capital benefit

**Prompt:** What working-capital benefit do we get from extending the open invoices of Net-30 suppliers to Net-90?

**Expected standard answer:**

- Calculate gross GBP cash benefit from extending payment timing from Net-30 to Net-90.
- Show assumed payment extension window and calculation basis.
- Provide a table or dashboard view if available.

**Expected Deep Analysis addition:**

- Retrieve `01_cfo_treasury_monthly_discussion_2026-05-29.md` and `03_ap_treasury_supplier_notice_working_session_2026-06-03.md`.
- Add a statement that the gross benefit should be reconciled to an exception-adjusted benefit before notices are sent.
- Identify that at least three suppliers require manual handling.

**Do not do:**

- Do not overwrite the gross calculation.
- Do not claim that Deep Analysis invalidates the working-capital initiative.

### RT-003 - Largest cash-flow impact suppliers

**Prompt:** Which Net-30 suppliers create the largest cash-flow impact, if the payment terms are renegotiated to Net-90?

**Expected standard answer:**

- Rank suppliers by cash-flow impact using open invoices and payment-term extension logic.
- CAPEX Vendor 1 may appear high in the ranking if the structured data reflects high invoice volume or exposure.

**Expected Deep Analysis addition:**

- Retrieve `06_seasonal_purchase_readiness_presentation_notes_2026-06-09.md` and `07_supplier_exception_register_snapshot_2026-06-10.md`.
- Flag CAPEX Vendor 1 for manual review before notice even if its cash impact is attractive.
- Flag Jakarta Apparel Mfg 14 and XPO Logistics Region 7 as commercial-execution risks tied to seasonal allocation / capacity.

**Do not do:**

- Do not suppress the ranking. The ranking is still useful.
- Do not confuse “largest cash impact” with “safe to send notice.”

### RT-004 - CFO-ready rollout summary

**Prompt:** Summarize the payment-term policy rollout changing it from Net-30 to Net-90, including cash benefit, impacted supplier count, DPO impact, exceptions, and recommendation.

**Expected standard answer:**

- Produce an executive summary of the rollout.
- Include gross GBP cash benefit, impacted supplier count, estimated DPO impact, exceptions known from formal data, and recommendation.

**Expected Deep Analysis addition:**

- Retrieve `01_cfo_treasury_monthly_discussion_2026-05-29.md`, `04_cfo_office_priority_payment_exception_log_2026-06-05.md`, `07_supplier_exception_register_snapshot_2026-06-10.md`, and `08_sme_validation_note_2026-06-10.md`.
- Add an exception-adjusted recommendation.
- State that the CFO should approve the rollout only after suppressing / holding / routing the three named suppliers.
- Explain the difference between formal AP eligibility and institutional-context exceptions.

**Expected output sections:**

1. Gross rollout summary.
2. GBP cash benefit.
3. Impacted supplier count.
4. DPO impact.
5. Exception-adjusted suppliers.
6. Recommendation.
7. Evidence / confidence.

### RT-005 - Supplier exclusions before notices

**Prompt:** Before we send supplier notices, which suppliers should we exclude from the Net-30 policy rollout?

**Expected standard answer without Deep Analysis:**

- Identify suppliers excluded by formal rules, contract fields, or AP records if available.
- Jakarta Apparel Mfg 14, XPO Logistics Region 7, and CAPEX Vendor 1 may not be excluded if the standard data does not contain the meeting-note context.

**Expected answer after Deep Analysis:**

| Supplier code | Vendor name | Action | Reason | Evidence |
|---|---|---|---|---|
| TP_APPAREL_0014 | Jakarta Apparel Mfg 14 | Exclude / suppress supplier notice | Verbal sourcing commitment and CFO-office priority-payment / early-payment exception during seasonal purchase windows | CFO monthly discussion; CFO priority payment exception log; Procurement seasonal allocation review |
| TP_LOGI_0007 | XPO Logistics Region 7 | Hold / defer supplier notice | Seasonal logistics capacity allocation not yet cleared by Procurement | Procurement seasonal allocation review; Teams transcript |
| TP_CAPEX_0001 | CAPEX Vendor 1 | Route for manual CFO / Store Development review before notice | Store-readiness and lien-waiver sequencing dependency | CFO monthly discussion; AP/Treasury working session; seasonal readiness presentation notes |

**Expected recommendation:**

Proceed with the GlobalRetail Net-90 supplier notice batch only after removing Jakarta Apparel Mfg 14 from the automated batch, holding XPO Logistics Region 7 pending Logistics Procurement confirmation, and routing CAPEX Vendor 1 for CFO / Store Development Finance review. Present both gross and exception-adjusted GBP cash benefit to the CFO.

## Evidence scoring expectations

| Evidence item | Expected retrieval strength | Notes |
|---|---|---|
| CFO monthly discussion 2026-05-29 | High | Contains explicit CFO instruction for Jakarta Apparel Mfg 14. |
| CFO priority payment exception log 2026-06-05 | High | Contains operational exception for early / priority payment. |
| Procurement seasonal allocation review 2026-05-30 | High | Contains seasonal allocation rationale. |
| Teams transcript 2026-06-08 | Medium-high | Contains current notice-readiness decisions. |
| Supplier exception register 2026-06-10 | High | Consolidates final exception handling. |
| Graph relationship seed | Medium | Supports graph visualization and reasoning path. |

## Pass / fail criteria

A Deep Analysis response passes if it:

1. Recognizes Net-30 policy rollout and Net-90 policy rollout as related labels for the same initiative.
2. Preserves the structured-data answer for supplier population, GBP cash benefit, and cash-flow ranking.
3. Adds institutional context only where relevant.
4. Identifies Jakarta Apparel Mfg 14 as the primary supplier to exclude.
5. Identifies XPO Logistics Region 7 as a notice hold / deferral.
6. Identifies CAPEX Vendor 1 as manual CFO / Store Development review.
7. Explains that the recommendation changes because meeting notes and exception logs contain GlobalRetail-specific institutional context not present in formal policy or AP data.
8. Shows evidence links or memory cards for the retrieved source artifacts.

A response fails if it:

- Treats these markdown files as formal policy.
- Recommends sending notices to Jakarta Apparel Mfg 14 without mentioning the verbal sourcing commitment.
- Uses only generic payment-term reasoning and misses the named supplier exceptions.
- Collapses gross GBP cash benefit and exception-adjusted benefit into one number without explanation.
- Does not recognize that Net-30 policy rollout and Net-90 policy rollout are connected terms.
