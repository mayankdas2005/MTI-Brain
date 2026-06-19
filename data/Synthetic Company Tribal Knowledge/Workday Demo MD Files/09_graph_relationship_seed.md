---
document_id: GRAPH-SEED-TREASURY-LIQUIDITY-2026-05
source_type: graph_relationship_seed
source_system: SharePoint
repository_path: /Finance/Treasury/Knowledge Records/Graph Seeds/2026/May/
classification: Internal - Treasury Knowledge Graph Seed
access_scope: Deep Analysis only
created_date: 2026-05-17
business_domain: treasury
related_process: relationship-aware liquidity reasoning
related_prompts:
  - judgment_before_cfo_briefing
  - reasoning_path_explainability
related_entities:
  - CFO Office
  - Audit & Risk Committee
  - Group Treasury
  - Group Treasury Policy
  - 13-week cash forecast
  - Week 8 liquidity trough
related_commitments:
  - informal_minimum_liquidity_250m
  - formal_minimum_liquidity_200m
  - target_operating_buffer_300m
deep_analysis_relevance: critical
confidence: high
sme_validated: true
---

# Treasury Liquidity Relationship Seed — May 2026

## Purpose

Record relationship evidence connecting formal policy, institutional memory, forecast facts, committee oversight, and recommended action for the May treasury liquidity briefing cycle.

## Nodes

| Node ID | Node type | Label | Key attributes |
|---|---|---|---|
| PERSON_CFO_MAYA_PATEL | Person | Maya Patel | Chief Financial Officer |
| COMMITTEE_ARC | Governance body | Audit & Risk Committee | Board committee with treasury risk oversight |
| POLICY_GROUP_TREASURY | Policy | Group Treasury Policy | Formal hard minimum liquidity threshold USD 200M; target operating buffer USD 300M |
| COMMITMENT_CFO_250M_LIQUIDITY | Management commitment | CFO commitment to maintain at least USD 250M liquidity | Informal, active, not formal policy, SME validated |
| FORECAST_2026_05_13_R2 | Forecast | May 13-week liquidity forecast | Forecast run 2026-05-13_FCST_R2 |
| FORECAST_WEEK_8_TROUGH | Forecast week | Week 8 liquidity trough | Week ending 2026-07-03; projected total liquidity USD 231.4M |
| RISK_BOARD_COMMITMENT_GAP | Risk | Board commitment gap | Forecast below CFO commitment by approx. USD 18.6M |
| RECOMMENDATION_CFO_ACTION_REQUIRED | Recommendation | Action required before CFO briefing | Prepare mitigation options and CFO messaging |
| PROCESS_CFO_LIQUIDITY_BRIEFING | Process | CFO liquidity briefing | Monthly treasury health review process |
| ENTITY_GLOBALRETAIL_TREASURY | Legal / treasury entity | GlobalRetail Treasury Ltd | Treasury operating entity |

## Relationships

| Source node | Relationship | Target node | Evidence record |
|---|---|---|---|
| PERSON_CFO_MAYA_PATEL | chairs / receives output from | PROCESS_CFO_LIQUIDITY_BRIEFING | CFO monthly finance / treasury meeting notes |
| PERSON_CFO_MAYA_PATEL | made commitment to | COMMITTEE_ARC | Audit & Risk Committee prebrief excerpt |
| COMMITMENT_CFO_250M_LIQUIDITY | sets management comfort level of | USD 250M total liquidity | Audit & Risk Committee prebrief excerpt; SME validation note |
| POLICY_GROUP_TREASURY | defines hard minimum threshold of | USD 200M total liquidity | Group Treasury Policy |
| POLICY_GROUP_TREASURY | defines target operating buffer of | USD 300M total liquidity | Group Treasury Policy |
| FORECAST_2026_05_13_R2 | contains | FORECAST_WEEK_8_TROUGH | Treasury forecast working notes |
| FORECAST_WEEK_8_TROUGH | projects total liquidity of | USD 231.4M | Treasury forecast working notes |
| FORECAST_WEEK_8_TROUGH | remains above | USD 200M hard minimum | Treasury Committee liquidity review notes |
| FORECAST_WEEK_8_TROUGH | falls below | COMMITMENT_CFO_250M_LIQUIDITY | Treasury forecast working notes; SME validation note |
| FORECAST_WEEK_8_TROUGH | creates | RISK_BOARD_COMMITMENT_GAP | Treasury risk watchlist notes |
| RISK_BOARD_COMMITMENT_GAP | requires | RECOMMENDATION_CFO_ACTION_REQUIRED | CFO action register; Teams transcript excerpt |
| RECOMMENDATION_CFO_ACTION_REQUIRED | prepares for | PROCESS_CFO_LIQUIDITY_BRIEFING | CFO action register |

## Reasoning path for liquidity recommendation

1. Group Treasury Policy defines the formal hard minimum liquidity threshold as USD 200M.
2. The 13-week forecast projects a low point of USD 231.4M.
3. Therefore, the forecast does not breach the formal hard minimum threshold.
4. The same policy treats sub-buffer positions as Amber and requiring action before the CFO briefing.
5. Separate records show the CFO made an active management commitment to the Audit & Risk Committee to maintain at least USD 250M total liquidity through the current planning window.
6. The forecast low point of USD 231.4M falls approximately USD 18.6M below that active management commitment.
7. The issue is therefore not only policy Amber monitoring; it is also a Board-commitment risk.
8. The recommended action is to brief the CFO before the treasury health review with mitigation options and Board-ready wording.

## Interpretation controls

- Do not overwrite the formal policy threshold.
- Do not describe USD 250M as a Board-approved formal minimum unless policy is amended.
- Do treat USD 250M as an active management commitment for the current planning window.
- Do surface the distinction between formal compliance and Board-commitment risk.
