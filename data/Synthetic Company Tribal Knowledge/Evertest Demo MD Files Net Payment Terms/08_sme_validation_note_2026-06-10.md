---
document_id: SME-VALIDATION-NET30-NET90-2026-06-10
title: SME Validation Note - Deep Analysis Handling for Net-30 / Net-90 Supplier Rollout
source_type: sme_validation_note
classification: Confidential - GR_TREASURY / Procurement / GR_HOLDINGS CFO Office
access_control: Deep Analysis only
validation_date: 2026-06-10
recorded_location: /GlobalRetail Holdings plc/Finance Leadership/GlobalRetail Treasury Ltd/Working Capital/Payment Terms Review/SME Validation/
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
  - Deep Analysis
  - tribal knowledge
  - Net-30 policy rollout
  - Net-90 policy rollout
  - supplier notices
  - exception-adjusted recommendation
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
evidence_status: sme_validated_context_not_formal_policy
deep_analysis_relevance: high
---

# SME Validation Note - Deep Analysis Handling for Net-30 / Net-90 Supplier Rollout

## Group and entity context

These notes relate to **GlobalRetail Holdings plc** (`GR_HOLDINGS`, LEI `LEI-GRHOLD`), the UK parent company of a 25-entity GlobalRetail group. The parent functional currency is **GBP**. Working-capital analysis, supplier-notice decisions, and CFO briefing values in this folder should be treated as group-level analysis for `GR_HOLDINGS` unless a source line identifies a specific regional or operating-company dependency.

**GlobalRetail Treasury Ltd** (`GR_TREASURY`, GBP) is the in-house treasury vehicle coordinating the Net-30 / Net-90 supplier payment-term workstream with the CFO Office, AP Operations, Procurement, Sourcing, and Store Development Finance. Regional context may involve **GlobalRetail Europe BV** (`GR_EU_BV`), **GlobalRetail APAC Pte** (`GR_APAC_PTE`), **GlobalRetail LATAM SA** (`GR_LATAM_SA`), and country operating companies such as GlobalRetail US Inc, GlobalRetail UK Ltd, GlobalRetail Deutschland GmbH, and GlobalRetail Japan KK.

## Entity scope for SME validation

SME validation applies to the `GR_HOLDINGS` group payment-term rollout coordinated by `GR_TREASURY`. The validation team confirmed that Deep Analysis should preserve the distinction between parent-level GBP working-capital benefit and supplier execution risks sitting with APAC, US, and Europe operating contexts.


**Validation date:** 2026-06-10  
**Validated by:** Group Treasurer, GlobalRetail Treasury Ltd (`GR_TREASURY`); VP Procurement; AP Director  
**Prepared by:** Treasury PMO, GlobalRetail Treasury Ltd (`GR_TREASURY`)  
**Validation scope:** Supplier payment-term demo prompts and Deep Analysis retrieval behavior

## Validation summary

The structured AP data can answer the first four questions in the payment-term prompt sequence. It can identify Net-30 suppliers, estimate working-capital benefit from extending open invoices to Net-90, rank cash-flow impact, and create a summary of the proposed rollout.

The structured answer should not be treated as the final supplier notice list. Before notices are sent, Deep Analysis should retrieve institutional context from recent Finance, Treasury, Procurement, and CFO Office documents.

## Validated interpretation of the prompt sequence

### Prompt 1

**Question:** Which suppliers are included in the Net-30 payment-term?

**Expected standard behavior:** Retrieve suppliers with Net-30 activity from structured AP data.

**Expected Deep Analysis context if activated:** Recognize that the Net-30 supplier population is the starting cohort for the Net-90 policy rollout and that supplier-notice exceptions are being tracked in recent meeting notes.

### Prompt 2

**Question:** What working-capital benefit do we get from extending the open invoices of Net-30 suppliers to Net-90?

**Expected standard behavior:** Calculate gross working-capital benefit from structured open invoice data.

**Expected Deep Analysis context if activated:** Flag that gross benefit should not be interpreted as executable benefit until the exception list is applied.

### Prompt 3

**Question:** Which Net-30 suppliers create the largest cash-flow impact, if the payment terms are renegotiated to Net-90?

**Expected standard behavior:** Rank suppliers by cash-flow impact using structured AP data.

**Expected Deep Analysis context if activated:** Identify that at least one high-impact supplier, CAPEX Vendor 1, requires manual CFO / Store Development Finance routing before notices. Also identify that Jakarta Apparel Mfg 14 and XPO Logistics Region 7 may not be the largest by invoice count, but they carry disproportionate seasonal execution risk.

### Prompt 4

**Question:** Summarize the payment-term policy rollout changing it from Net-30 to Net-90, including cash benefit, impacted supplier count, DPO impact, exceptions, and recommendation.

**Expected standard behavior:** Produce a CFO-ready GlobalRetail Holdings plc summary from formal AP analysis.

**Expected Deep Analysis context if activated:** Separate the recommendation into gross rollout and exception-adjusted rollout. The summary should include the three named suppliers with recommended handling.

### Prompt 5

**Question:** Before we send supplier notices, which suppliers should we exclude from the Net-30 policy rollout?

**Expected standard behavior:** Identify exclusions available from formal contract or AP data only.

**Expected Deep Analysis behavior:** Retrieve current institutional context from meeting notes and exception logs. Recommend excluding, holding, or routing suppliers before notices are sent.

## SME-validated suppliers requiring special handling

| Supplier code | Vendor name | Required handling | Validation rationale |
|---|---|---|---|
| TP_APPAREL_0014 | Jakarta Apparel Mfg 14 | Exclude from first Net-90 notice cycle for GR_APAC_PTE / GlobalRetail Japan KK seasonal allocation; keep Net-30 / priority-payment exception active during seasonal window | Verbal sourcing commitment and CFO-office operational exception are current and not reflected in formal payment-term documentation. |
| TP_LOGI_0007 | XPO Logistics Region 7 | Defer notice until Logistics Procurement confirms no capacity-allocation risk for GlobalRetail US Inc lanes | Carrier capacity commitment is unresolved and could affect seasonal outbound coverage. |
| TP_CAPEX_0001 | CAPEX Vendor 1 | Route to CFO / Store Development Finance for GR_EU_BV / UK / Germany store-readiness before notice; do not send automated notice | Store-readiness and lien-waiver dependencies require manual review before term-change notice. |

## Validation note for output wording

The correct Deep Analysis answer is not simply “exclude all three suppliers.” The better answer is:

- **Exclude / suppress notice:** Jakarta Apparel Mfg 14.
- **Hold / defer notice:** XPO Logistics Region 7.
- **Manual review / route before notice:** CAPEX Vendor 1.

If the UI needs a simplified executive answer, these can be grouped as suppliers that should be removed from the automated supplier notice batch before the Net-90 policy rollout proceeds.

## Evidence expectations

A Deep Analysis answer should show evidence from at least two of the following source types:

- CFO Finance / Treasury monthly discussion notes.
- Procurement seasonal allocation review notes.
- CFO Office priority payment exception log.
- Teams transcript excerpt.
- Supplier exception register snapshot.

The answer should expose a graph path linking supplier, payment-term initiative, institutional commitment, operating exception, business risk, and recommended notice handling.
