---
document_id: SP-FOLDER-INDEX-NET30-NET90-2026-06
title: Treasury / Procurement Working Folder Index - Net-30 and Net-90 Supplier Term Review
source_type: sharepoint_folder_index
classification: Confidential - GR_HOLDINGS CFO Office / GR_TREASURY / Procurement
access_control: Deep Analysis only
created_date: 2026-06-10
last_modified: 2026-06-11
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
  - Net-30 policy rollout
  - Net-90 policy rollout
  - supplier notices
  - open invoices
  - working-capital benefit
  - seasonal purchase window
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
net30_net90_relevance: high
deep_analysis_relevance: high
---

# Treasury / Procurement Working Folder Index - Net-30 and Net-90 Supplier Term Review

## Group and entity context

These notes relate to **GlobalRetail Holdings plc** (`GR_HOLDINGS`, LEI `LEI-GRHOLD`), the UK parent company of a 25-entity GlobalRetail group. The parent functional currency is **GBP**. Working-capital analysis, supplier-notice decisions, and CFO briefing values in this folder should be treated as group-level analysis for `GR_HOLDINGS` unless a source line identifies a specific regional or operating-company dependency.

**GlobalRetail Treasury Ltd** (`GR_TREASURY`, GBP) is the in-house treasury vehicle coordinating the Net-30 / Net-90 supplier payment-term workstream with the CFO Office, AP Operations, Procurement, Sourcing, and Store Development Finance. Regional context may involve **GlobalRetail Europe BV** (`GR_EU_BV`), **GlobalRetail APAC Pte** (`GR_APAC_PTE`), **GlobalRetail LATAM SA** (`GR_LATAM_SA`), and country operating companies such as GlobalRetail US Inc, GlobalRetail UK Ltd, GlobalRetail Deutschland GmbH, and GlobalRetail Japan KK.

## Entity scope note

This SharePoint folder is maintained under the GlobalRetail Holdings plc finance leadership workspace. `GR_TREASURY` owns the consolidated working-capital view and coordinates inputs from regional sub-holdings and country operating companies. Supplier exceptions in this folder should be interpreted as GlobalRetail group institutional context, not as standalone vendor-master or policy changes.


**Folder location:** `/GlobalRetail Holdings plc/Finance Leadership/GlobalRetail Treasury Ltd/Working Capital/Payment Terms Review/Net30-Net90 Rollout/`

**Folder owner:** Group Treasurer, GlobalRetail Treasury Ltd (`GR_TREASURY`)  
**Contributors:** GR_HOLDINGS CFO Office, GlobalRetail Treasury Ltd (`GR_TREASURY`), AP Operations, Procurement, Sourcing, Store Development Finance, regional finance leads  
**Access:** GR_HOLDINGS CFO Office / GR_TREASURY Leadership / Procurement Leadership only

## Folder purpose

This folder is used to collect working notes, meeting minutes, draft presentation notes, and recorded discussion extracts related to the supplier payment-term initiative. The folder is not the policy repository. It captures the conversations and current operating context that may not yet be reflected in the AP vendor master, formal payment-term documentation, supplier contract records, or approved policy files.

The teams use the phrases **Net-30 policy rollout** and **Net-90 policy rollout** interchangeably in this folder. In meeting shorthand:

- **Net-30 policy rollout** means the rollout population starts with suppliers currently carrying Net-30 open invoice activity.
- **Net-90 policy rollout** means the proposed target state for suppliers selected for payment-term renegotiation.
- The intended business action is to evaluate whether open invoices for selected Net-30 suppliers should be extended or renegotiated to Net-90.

## Key documents in this folder

| File | Source type | Why it matters |
|---|---|---|
| `01_cfo_treasury_monthly_discussion_2026-05-29.md` | CFO/Treasury monthly meeting notes | Captures the verbal sourcing commitment and CFO-office payment exception for Jakarta Apparel Mfg 14. |
| `02_procurement_seasonal_allocation_review_2026-05-30.md` | Procurement and sourcing meeting notes | Captures seasonal allocation risk and supplier capacity commitments for apparel and logistics suppliers. |
| `03_ap_treasury_supplier_notice_working_session_2026-06-03.md` | AP/Treasury working session notes | Captures the operational decision to pause notices for suppliers with unresolved relationship commitments. |
| `04_cfo_office_priority_payment_exception_log_2026-06-05.md` | CFO Office exception log excerpt | Captures the early-payment / priority-payment operating exception for specific seasonal suppliers. |
| `05_teams_transcript_supplier_notice_readiness_2026-06-08.md` | Teams transcript excerpt | Captures up-to-date discussion immediately before supplier notices were prepared. |
| `06_seasonal_purchase_readiness_presentation_notes_2026-06-09.md` | Presentation notes | Captures the seasonal purchasing dependency behind supplier exceptions. |
| `07_supplier_exception_register_snapshot_2026-06-10.md` | Exception register snapshot | Consolidates the three suppliers that should be excluded, deferred, or routed for CFO approval before notices are sent. |
| `08_sme_validation_note_2026-06-10.md` | SME validation note | Confirms how Treasury and Procurement expect Deep Analysis to interpret the exception facts. |
| `09_graph_relationship_seed.md` | Relationship seed notes | Human-readable node and edge guidance for the ontology / knowledge graph layer. |
| `10_retrieval_test_cases.md` | Retrieval QA notes | Expected retrieval and reasoning behavior for the five demo prompts. |

## Current working conclusion

The initial structured AP answer can identify suppliers with Net-30 open invoice activity and can estimate the working-capital benefit of moving eligible suppliers to Net-90. That answer is correct as far as the formal data goes.

The Deep Analysis path should add the following GlobalRetail-specific institutional context from this folder:

1. **Jakarta Apparel Mfg 14** must remain on Net-30 and must not be extended to Net-90 during the upcoming seasonal purchase windows because the CFO monthly discussion captured a verbal sourcing commitment and an operational early-payment / priority-payment exception.
2. **XPO Logistics Region 7** should not receive the initial supplier notice until Procurement confirms that the logistics capacity allocation will not be impaired.
3. **CAPEX Vendor 1** should be routed to CFO / Store Development Finance review before any Net-90 supplier notice because the vendor is tied to store-readiness milestones and lien-waiver sequencing.

These notes do not override formal policy. They identify relationship and execution risk that is not yet reflected in the formal policy or supplier master records.
