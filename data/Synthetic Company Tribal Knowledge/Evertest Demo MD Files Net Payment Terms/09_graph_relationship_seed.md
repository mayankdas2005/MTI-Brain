---
document_id: GRAPH-SEED-NET30-NET90-2026-06-10
title: Relationship Seed Notes - Supplier Payment-Term Rollout Deep Analysis
source_type: graph_relationship_seed
classification: Confidential - Data / AI / GR_TREASURY
access_control: Deep Analysis only
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
  - ontology
  - knowledge graph
  - Net-30 policy rollout
  - Net-90 policy rollout
  - supplier notices
  - tribal knowledge
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

# Relationship Seed Notes - Supplier Payment-Term Rollout Deep Analysis

## Group and entity context

These notes relate to **GlobalRetail Holdings plc** (`GR_HOLDINGS`, LEI `LEI-GRHOLD`), the UK parent company of a 25-entity GlobalRetail group. The parent functional currency is **GBP**. Working-capital analysis, supplier-notice decisions, and CFO briefing values in this folder should be treated as group-level analysis for `GR_HOLDINGS` unless a source line identifies a specific regional or operating-company dependency.

**GlobalRetail Treasury Ltd** (`GR_TREASURY`, GBP) is the in-house treasury vehicle coordinating the Net-30 / Net-90 supplier payment-term workstream with the CFO Office, AP Operations, Procurement, Sourcing, and Store Development Finance. Regional context may involve **GlobalRetail Europe BV** (`GR_EU_BV`), **GlobalRetail APAC Pte** (`GR_APAC_PTE`), **GlobalRetail LATAM SA** (`GR_LATAM_SA`), and country operating companies such as GlobalRetail US Inc, GlobalRetail UK Ltd, GlobalRetail Deutschland GmbH, and GlobalRetail Japan KK.

## Entity scope for graph seed

The graph seed should connect the supplier payment-term rollout to the GlobalRetail corporate structure. `GR_HOLDINGS` is the parent company, `GR_TREASURY` coordinates the payment-term initiative, and regional / operating-company nodes provide the context that explains why some formally eligible suppliers should not receive automated notices.


**Purpose:** Human-readable relationship notes for building or validating graph paths used by Deep Analysis.  
**Scope:** Net-30 supplier population, proposed Net-90 rollout, supplier notices, institutional-memory exceptions, and recommendation changes.

## GlobalRetail corporate entity nodes

| Entity ID | Entity type | Label | Notes |
|---|---|---|---|
| `GR_HOLDINGS` | ParentCompany | GlobalRetail Holdings plc | UK parent company; LEI `LEI-GRHOLD`; functional currency GBP |
| `GR_TREASURY` | TreasuryVehicle | GlobalRetail Treasury Ltd | In-house treasury vehicle; functional currency GBP |
| `GR_EU_BV` | RegionalSubHolding | GlobalRetail Europe BV | Europe regional sub-holding |
| `GR_APAC_PTE` | RegionalSubHolding | GlobalRetail APAC Pte | APAC regional sub-holding |
| `GR_LATAM_SA` | RegionalSubHolding | GlobalRetail LATAM SA | LATAM regional sub-holding |
| `GR_US_INC` | CountryOperatingCompany | GlobalRetail US Inc | Country operating company referenced for logistics capacity |
| `GR_UK_LTD` | CountryOperatingCompany | GlobalRetail UK Ltd | Country operating company referenced for store development |
| `GR_DE_GMBH` | CountryOperatingCompany | GlobalRetail Deutschland GmbH | Country operating company referenced for store development |
| `GR_JP_KK` | CountryOperatingCompany | GlobalRetail Japan KK | Country operating company referenced for seasonal apparel demand |

## Core entities

| Entity ID | Entity type | Label |
|---|---|---|
| `TERM_NET_30` | PaymentTerm | Net-30 |
| `TERM_NET_90` | PaymentTerm | Net-90 |
| `ROLLOUT_NET30_POLICY` | PaymentTermRollout | Net-30 policy rollout |
| `ROLLOUT_NET90_POLICY` | PaymentTermRollout | Net-90 policy rollout |
| `NOTICE_BATCH_2026_06` | SupplierNoticeBatch | June 2026 supplier notice batch |
| `SEASONAL_WINDOW_2026_Q3_Q4` | SeasonalPurchaseWindow | Q3 / Q4 2026 seasonal purchase window |
| `CFO_TREASURY_MEETING_2026_05_29` | MeetingNotes | CFO Finance / Treasury Monthly Discussion |
| `PROCUREMENT_ALLOCATION_REVIEW_2026_05_30` | MeetingNotes | Procurement Seasonal Allocation Review |
| `CFO_PAY_EXCEPTION_2026_061` | CFOOfficeException | Priority payment exception for Jakarta Apparel Mfg 14 |
| `STORE_READINESS_DEPENDENCY_2026_06` | ExecutionDependency | Store-readiness / lien-waiver sequencing dependency |

## Supplier entities

| Supplier ID | Vendor name | Internal alias | Current AP activity | Proposed rollout treatment |
|---|---|---|---|---|
| `TP_APPAREL_0014` | Jakarta Apparel Mfg 14 | Vendor X | Net-30 activity | Suppress from notice; exclude from first Net-90 cycle |
| `TP_LOGI_0007` | XPO Logistics Region 7 | Vendor Y | Net-30 activity | Hold / defer notice pending Logistics confirmation |
| `TP_CAPEX_0001` | CAPEX Vendor 1 | Vendor Z | Net-30 activity | Manual CFO / Store Development review before notice |

## Human-readable relationship triples

### GlobalRetail group structure

- `GR_HOLDINGS` -> `has_legal_entity_identifier` -> `LEI-GRHOLD`
- `GR_HOLDINGS` -> `has_country` -> `GB (UK)`
- `GR_HOLDINGS` -> `has_functional_currency` -> `GBP`
- `GR_HOLDINGS` -> `has_group_entity_count` -> `25`
- `GR_HOLDINGS` -> `has_in_house_treasury_vehicle` -> `GR_TREASURY`
- `GR_TREASURY` -> `coordinates` -> `ROLLOUT_NET30_POLICY`
- `GR_TREASURY` -> `coordinates` -> `ROLLOUT_NET90_POLICY`
- `GR_TREASURY` -> `prepares_CFO_recommendation_for` -> `GR_HOLDINGS`
- `GR_HOLDINGS` -> `has_regional_subholding` -> `GR_EU_BV`
- `GR_HOLDINGS` -> `has_regional_subholding` -> `GR_APAC_PTE`
- `GR_HOLDINGS` -> `has_regional_subholding` -> `GR_LATAM_SA`
- `GR_APAC_PTE` -> `supports_operating_company` -> `GR_JP_KK`
- `GR_EU_BV` -> `supports_operating_company` -> `GR_UK_LTD`
- `GR_EU_BV` -> `supports_operating_company` -> `GR_DE_GMBH`

### Rollout terminology

- `ROLLOUT_NET30_POLICY` -> `same_business_initiative_as` -> `ROLLOUT_NET90_POLICY`
- `ROLLOUT_NET30_POLICY` -> `starts_from_supplier_payment_term` -> `TERM_NET_30`
- `ROLLOUT_NET90_POLICY` -> `targets_supplier_payment_term` -> `TERM_NET_90`
- `ROLLOUT_NET90_POLICY` -> `requires_supplier_notices` -> `NOTICE_BATCH_2026_06`
- `NOTICE_BATCH_2026_06` -> `prepared_from` -> `Net-30 suppliers with open invoice activity`

### Vendor X - Jakarta Apparel Mfg 14

- `TP_APPAREL_0014` -> `supports_regional_subholding` -> `GR_APAC_PTE`
- `TP_APPAREL_0014` -> `supports_operating_company` -> `GR_JP_KK`
- `TP_APPAREL_0014` -> `has_current_payment_term_activity` -> `TERM_NET_30`
- `TP_APPAREL_0014` -> `appears_eligible_in_formal_ap_data_for` -> `ROLLOUT_NET90_POLICY`
- `TP_APPAREL_0014` -> `has_verbal_sourcing_commitment` -> `SEASONAL_WINDOW_2026_Q3_Q4`
- `TP_APPAREL_0014` -> `commitment_documented_in` -> `CFO_TREASURY_MEETING_2026_05_29`
- `TP_APPAREL_0014` -> `allocation_dependency_documented_in` -> `PROCUREMENT_ALLOCATION_REVIEW_2026_05_30`
- `TP_APPAREL_0014` -> `has_cfo_office_exception` -> `CFO_PAY_EXCEPTION_2026_061`
- `CFO_PAY_EXCEPTION_2026_061` -> `allows` -> `priority_payment_during_seasonal_purchase_window`
- `CFO_PAY_EXCEPTION_2026_061` -> `requires` -> `maintain_Net_30_or_favorable_payment_timing`
- `TP_APPAREL_0014` -> `should_be_excluded_from` -> `NOTICE_BATCH_2026_06`
- `TP_APPAREL_0014` -> `should_be_removed_from_exception_adjusted_cash_benefit_until` -> `seasonal_allocation_dependency_expires`

### Vendor Y - XPO Logistics Region 7

- `TP_LOGI_0007` -> `supports_operating_company` -> `GR_US_INC`
- `TP_LOGI_0007` -> `has_current_payment_term_activity` -> `TERM_NET_30`
- `TP_LOGI_0007` -> `appears_eligible_in_formal_ap_data_for` -> `ROLLOUT_NET90_POLICY`
- `TP_LOGI_0007` -> `supports` -> `seasonal_outbound_transportation_capacity`
- `TP_LOGI_0007` -> `capacity_dependency_documented_in` -> `PROCUREMENT_ALLOCATION_REVIEW_2026_05_30`
- `TP_LOGI_0007` -> `notice_status` -> `hold_pending_Logistics_Procurement_confirmation`
- `TP_LOGI_0007` -> `should_be_deferred_from` -> `NOTICE_BATCH_2026_06`

### Vendor Z - CAPEX Vendor 1

- `TP_CAPEX_0001` -> `supports_regional_subholding` -> `GR_EU_BV`
- `TP_CAPEX_0001` -> `supports_operating_company` -> `GR_UK_LTD`
- `TP_CAPEX_0001` -> `supports_operating_company` -> `GR_DE_GMBH`
- `TP_CAPEX_0001` -> `has_current_payment_term_activity` -> `TERM_NET_30`
- `TP_CAPEX_0001` -> `appears_eligible_in_formal_ap_data_for` -> `ROLLOUT_NET90_POLICY`
- `TP_CAPEX_0001` -> `has_execution_dependency` -> `STORE_READINESS_DEPENDENCY_2026_06`
- `STORE_READINESS_DEPENDENCY_2026_06` -> `involves` -> `store_opening_milestones`
- `STORE_READINESS_DEPENDENCY_2026_06` -> `involves` -> `lien_waiver_sequencing`
- `TP_CAPEX_0001` -> `should_be_routed_to` -> `CFO_and_Store_Development_Finance_review`
- `TP_CAPEX_0001` -> `should_not_be_included_in` -> `automated_supplier_notice_batch`

## Expected graph paths for Deep Analysis

### Path 1 - Vendor X exclusion

`GR_TREASURY` -> `ROLLOUT_NET30_POLICY` -> `Net-30 supplier population` -> `TP_APPAREL_0014` -> `appears eligible for Net-90 rollout` -> `CFO monthly discussion` -> `verbal sourcing commitment` -> `seasonal purchase window` -> `CFO priority-payment exception` -> `exclude from first supplier notice cycle`

### Path 2 - Vendor Y deferral

`GR_TREASURY` -> `ROLLOUT_NET30_POLICY` -> `Net-30 supplier population` -> `TP_LOGI_0007` -> `appears eligible for Net-90 rollout` -> `Procurement seasonal allocation review` -> `seasonal outbound capacity dependency` -> `defer supplier notice`

### Path 3 - Vendor Z manual review

`GR_TREASURY` -> `ROLLOUT_NET30_POLICY` -> `Net-30 supplier population` -> `TP_CAPEX_0001` -> `large cash-flow impact` -> `Store Development Finance dependency` -> `lien-waiver / store-readiness risk` -> `route before notice`

## Brain memory card guidance

A Brain memory card for the final prompt should include:

- **Memory title:** Supplier notice exceptions from CFO / Procurement working notes.
- **Primary evidence:** CFO monthly discussion on 2026-05-29 and CFO priority payment exception log on 2026-06-05.
- **Primary supplier:** TP_APPAREL_0014 - Jakarta Apparel Mfg 14.
- **Decision change:** Formal AP eligibility becomes exception-adjusted exclusion.
- **Confidence:** High for Jakarta Apparel Mfg 14; medium-high for XPO Logistics Region 7; medium-high for CAPEX Vendor 1 pending Store Development Finance review.
- **Recommendation:** Send supplier notices only after suppressing Vendor X, holding Vendor Y, and routing Vendor Z for manual review.
