---
document_id: SUPPLIER-EXCEPTION-REGISTER-SNAPSHOT-2026-06-10
title: Supplier Exception Register Snapshot - Net-30 / Net-90 Notice Hold List
source_type: exception_register_snapshot
classification: Confidential - GR_TREASURY / Procurement / GR_HOLDINGS CFO Office
access_control: Deep Analysis only
snapshot_date: 2026-06-10
recorded_location: /GlobalRetail Holdings plc/Finance Leadership/GlobalRetail Treasury Ltd/Working Capital/Payment Terms Review/Exception Register/
business_domain: treasury_working_capital
process: supplier_notice_exception_handling
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
  - Net-30 policy rollout
  - Net-90 policy rollout
  - supplier notices
  - exception-adjusted recommendation
  - gross GBP cash benefit
  - DPO impact
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
evidence_status: snapshot_not_formal_policy
deep_analysis_relevance: high
---

# Supplier Exception Register Snapshot - Net-30 / Net-90 Notice Hold List

## Group and entity context

These notes relate to **GlobalRetail Holdings plc** (`GR_HOLDINGS`, LEI `LEI-GRHOLD`), the UK parent company of a 25-entity GlobalRetail group. The parent functional currency is **GBP**. Working-capital analysis, supplier-notice decisions, and CFO briefing values in this folder should be treated as group-level analysis for `GR_HOLDINGS` unless a source line identifies a specific regional or operating-company dependency.

**GlobalRetail Treasury Ltd** (`GR_TREASURY`, GBP) is the in-house treasury vehicle coordinating the Net-30 / Net-90 supplier payment-term workstream with the CFO Office, AP Operations, Procurement, Sourcing, and Store Development Finance. Regional context may involve **GlobalRetail Europe BV** (`GR_EU_BV`), **GlobalRetail APAC Pte** (`GR_APAC_PTE`), **GlobalRetail LATAM SA** (`GR_LATAM_SA`), and country operating companies such as GlobalRetail US Inc, GlobalRetail UK Ltd, GlobalRetail Deutschland GmbH, and GlobalRetail Japan KK.

## Entity scope for register

This register is maintained for the GlobalRetail Holdings plc group-level payment-term initiative and owned operationally by GlobalRetail Treasury Ltd. It identifies supplier-notice handling exceptions where a group-level GBP cash benefit intersects with regional commercial commitments or operating-company execution risk.


**Snapshot date:** 2026-06-10  
**Prepared by:** Treasury Working Capital Lead, GlobalRetail Treasury Ltd (`GR_TREASURY`)  
**Reviewed by:** AP Director, VP Procurement, CFO Office Coordinator  
**Status:** Working register snapshot for supplier-notice review

## Register notes

This register is compiled from meeting minutes, Teams transcript excerpts, CFO Office notes, and Procurement updates. It is not a formal policy document. It is intended to prevent supplier notices from being sent before current institutional context is considered.

The terms **Net-30 policy rollout** and **Net-90 policy rollout** refer to the same payment-term initiative in this register.

## Exception table

| Exception alias | Supplier code | Vendor name | Relevant GlobalRetail entity / region | Structured AP treatment | Deep Analysis finding | Recommended notice handling | Business risk if ignored | Evidence source |
|---|---|---|---|---|---|---|---|---|
| Vendor X | TP_APPAREL_0014 | Jakarta Apparel Mfg 14 | GR_APAC_PTE / GlobalRetail Japan KK seasonal apparel | Eligible Net-30 supplier; include in gross Net-90 benefit | Verbal sourcing commitment plus CFO-office priority-payment / early-payment exception during seasonal purchase windows | Exclude from first Net-90 notice cycle; keep Net-30 / priority-payment handling active | Loss of peak-season allocation priority, shipment delay, reduced seasonal purchase coverage | CFO monthly discussion; Procurement seasonal allocation review; CFO exception log |
| Vendor Y | TP_LOGI_0007 | XPO Logistics Region 7 | GlobalRetail US Inc seasonal logistics | Eligible Net-30 supplier; include in gross Net-90 benefit | Seasonal logistics capacity allocation not yet cleared by Procurement | Defer notice until Logistics Procurement confirms no capacity risk | Reduced preferred carrier capacity or slower exception handling during holiday surge | Procurement seasonal allocation review; Teams readiness transcript |
| Vendor Z | TP_CAPEX_0001 | CAPEX Vendor 1 | GR_EU_BV / GlobalRetail UK Ltd / GlobalRetail Deutschland GmbH store development | High-impact Net-30 supplier; include in gross cash-flow ranking | Store-readiness and lien-waiver sequencing dependency | Route to CFO / Store Development Finance before notice; exclude from automated batch | Store-opening delay, lien-waiver release delay, escalation from Store Development Finance | CFO monthly discussion; AP/Treasury working session; seasonal readiness notes |

## Register interpretation

For the CFO-ready GlobalRetail Holdings plc summary, the three suppliers above should be separated from the standard notice population. The dashboard should show:

1. Gross GBP cash benefit from the structured Net-30 to Net-90 analysis.
2. Gross impacted supplier count from the structured AP population.
3. Suppliers removed, held, or routed for approval after Deep Analysis.
4. Exception-adjusted GBP cash benefit and DPO impact after supplier holds.
5. Recommendation to send notices only after exception handling is applied.

## Current decision status

| Supplier | Decision status | Final notice status |
|---|---|---|
| TP_APPAREL_0014 - Jakarta Apparel Mfg 14 | Approved exception | Suppress notice |
| TP_LOGI_0007 - XPO Logistics Region 7 | Pending Procurement confirmation | Hold notice |
| TP_CAPEX_0001 - CAPEX Vendor 1 | CFO / Store Development review required | Manual review only |

## Register owner note

The Deep Analysis answer should not say that these suppliers are outside formal policy. It should say that formal AP eligibility is incomplete because recent institutional context changes the execution recommendation.
