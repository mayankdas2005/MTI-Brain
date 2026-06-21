# LPP Treasury Analytics — Semantic Layer Context Pack

> Purpose: ground an LLM that answers natural-language treasury & working-capital
> questions over the `lpp` schema (105 tables). This pack defines the business
> domain, the entity/dimension model, global conventions, the join graph,
> canonical metric formulas mapped to exact columns, a subject-area table catalog,
> a per-prompt query playbook, and explicit capability boundaries.
> Audience persona: **Treasury Director** and **Working Capital Lead**.

---

## 0. How to use this pack (instructions to the model)

1. Treasury questions are almost always **point-in-time** ("now", "latest") or
   **period** ("TTM", "this quarter", "YTD", "last 12 months", "YoY"). Resolve the
   time grain first, then the entity scope (entity / region / group / global),
   then the currency (default reporting currency = **USD**).
2. Prefer the **authoritative consolidated source** for headline financials
   (`company_financial_metric`, `working_capital_metric`, `credit_rating`,
   `capital_allocation_actual`, `pension_valuation`) and use the **operational
   bottom-up tables** (`cash_balance`, `cash_flow`, `borrowing`, `investment_position`,
   `fx_forward`, `bank_fee`, …) for drill-down, reconciliation, and "by account /
   by day / by counterparty" detail.
3. **Join on business `*_ref` / `code` fields, not on `uuid`** unless explicitly
   joining a child to its own parent uuid (see §4).
4. Amounts are denominated in the row's stated currency. Convert to USD with
   `fx_rate` (`rate_type = 'SPOT'`) before summing across currencies, **except**
   tables already standardized to USD (noted in §6/§7).
5. If a request needs data that the schema does not contain (see §9 Capability
   Boundaries), say so and answer with the closest available proxy rather than
   inventing values.

---

## 1. Business domain

**Who the data describes.** A large, global, **membership-based warehouse-club
retailer** ("GlobalRetail Group", entity-code prefix `GR_`, corporate email domain
`globalretail.com`). It runs warehouse stores and e-commerce across multiple
regions, earns **membership fees**, processes high volumes of card and account
payments, and operates a centralized **treasury / in-house bank (IHB)**.

**What the platform is.** A Kyriba-style treasury management system: bank
connectivity (BAI/MT940 statements, ISO 20022 pain.001 payment files), cash
positioning & forecasting, debt & investment management, FX & interest-rate
hedging, counterparty risk, bank-fee analysis, card acquiring/settlement, supply-
chain finance, and a knowledge-graph / RAG layer for institutional knowledge.

**Personas & their typical questions.**
- **Treasury Director** — global liquidity, debt profile & maturities, credit
  ratings, counterparty exposure, FX/IR hedging, investment performance, capital
  structure, stress testing, peer benchmarking, board-level dashboards.
- **Working Capital Lead** — DSO/DPO/DIO/CCC trends and drivers, AR/AP aging,
  supplier financing / dynamic discounting, cash conversion, payment efficiency.

**Peer set** (`peer_company`): publicly traded retailers used for benchmarking —
e.g. **WMT** (Walmart), **AMZN** (Amazon), **HD** (Home Depot), plus Target,
Kroger, Costco-type peers (7 rows). Peer financials live in `peer_company_metric`.

---

## 2. Entity, region, group & currency model (the core dimensions)

### 2.1 Legal entities — `company` (24 rows) / `gen_company_region` (24 rows)
- Business key: `company.code` (a.k.a. `company_ref` everywhere else).
- Observed entity codes: `GR_HOLDINGS`/`GR_HOLD` (top holding & ratings/equity
  issuer), `GR_TREASURY` (treasury / IHB entity), `GR_US_INC` (US incorporated),
  `GR_CA`, `GR_GB`/`GR_GB`, `GR_DE`, `GR_FR`, `GR_AE`, `GR_AU`, `GR_APAC_PTE`,
  `GR_EU_BV`, etc.
- `gen_company_region` is the **region + functional-currency lookup**:
  `company_code → region, functional_ccy`. Regions seen: **AMER, EMEA, APAC,
  LATAM, MENA**. Always resolve "by region" through this table.

### 2.2 Consolidation groups — `company_group` (6) + `company_group_member` (71)
- Named groups for consolidation/reporting: `GROUP_EMEA`, `GROUP_APAC`,
  `GROUP_LATAM`, plus a global group. Membership is M:N via
  `company_group_member(company_group_code, company_code)`.
- "Globally / consolidated" = the global group (all entities) or simply all
  `company` rows. "EMEA region" can be resolved either via `gen_company_region.region`
  or via the `GROUP_EMEA` membership — prefer `gen_company_region` for geography,
  `company_group` for accounting consolidation.

### 2.3 Currency — `currency` (18) + `fx_rate` (730k)
- `currency.code` (ISO 4217). One row flagged as the reference/base currency
  (**USD** is the group reporting currency).
- `fx_rate(rate_date, base_currency, quote_currency, rate_type)` is the market-data
  conversion table (sources: Bloomberg, ECB, internal). Use `rate_type = 'SPOT'`
  and the relevant `rate_date` to translate any amount to USD.

### 2.4 Banks & branches — `bank` (14), `bank_branch` (30)
- `bank.code` (e.g. `BANK_JPM` = JPMorgan, `BANK_HSBC`, `BANK_CITI`, …) is the
  counterparty key referenced as `bank_ref`, `lender_bank_ref`,
  `counterparty_bank_ref`, `issuing_bank_ref`, `issuer_bank_ref`.
- `bank.risk_tier_ref` ∈ {`TIER_1`,`TIER_2`,`TIER_3`} (1 = lowest risk).
  `bank.cash_exposure_limit_amount/_pct` set counterparty limits.
- `bank.intercompany` / `internal_counterparty` flag the **in-house bank (IHB)**;
  IHB branch `BR_IHB_LDN`, IHB accounts like `IHB_AUD_CONCENTRATION`.
- `bank_branch.code` (e.g. `BR_CITI_NY`, `BR_ANZ_SYD`) with `bic`/SWIFT, cut-off
  times; `bank_branch.bank_ref → bank.code`.

### 2.5 Bank accounts — `bank_account` (116)
- Business key `bank_account.code` (referenced as `account_ref` / `bank_account_ref`),
  e.g. `GR_AE_COLLECTION_1`, `GR_CA_PAYROLL_1`, `IHB_AUD_CONCENTRATION`.
- Key attributes: `company_ref`, `branch_ref`, `currency_ref`, `account_purpose`
  (`OPERATING`, `COLLECTION`, `PAYROLL`, `CONCENTRATION`, …), `closed_account`,
  `zba_generator`/`zba_identifier` (cash-pooling header accounts), `interest_bearing`.
- This is the hub that ties **cash, statements, sweeps, payments, fees, GL recon**
  back to an entity, branch, bank, and currency.

---

## 3. Global conventions (read before computing anything)

- **Reporting currency:** USD. Convert with `fx_rate` unless the table is already
  USD-standardized (`counterparty_exposure`, `derivative_mtm`).
- **Period grain — `period_type`:** `Q` = fiscal quarter, `FY` = full fiscal year.
  `period_date` = period-END date. "Latest quarter" = max `period_date` where
  `period_type='Q'`.
- **TTM (trailing twelve months):** sum the **four most recent quarterly** rows
  (`period_type='Q'`) for flow metrics (revenue, EBITDA, FCF, interest); use the
  **latest period** snapshot for stock metrics (debt, cash, equity).
- **YoY:** compare `period_date = D` vs `period_date = D − 1 year`, same `period_type`.
- **YTD:** fiscal-year-to-date; filter `action_date`/`transaction_date`/`period_date`
  ≥ start of current fiscal year.
- **Sign conventions:** `cash_flow.signed_amount` carries direction (inflow +,
  outflow −); `cash_flow_code.sign` ∈ {`IN`,`OUT`}; `interest_accrual.direction`
  ∈ {`INCOME`,`EXPENSE`}; `fx_exposure_forecast.direction` ∈ {`LONG`,`SHORT`}.
- **"Current" / latest snapshot flags:** `credit_rating.is_current = true`;
  snapshot tables (`forecast_*`, `fx_exposure_forecast`, daily `*_position` /
  `*_balance` / `*_exposure`) require filtering to the **latest `snapshot_date` /
  `as_of_date` / `balance_date`** unless a trend is requested.
- **Daily snapshot tables** (one row per key per day): `cash_balance`,
  `bank_statement_balance`, `investment_position`, `counterparty_exposure`,
  `derivative_mtm`, `interest_accrual`, `acquirer_sla_metric`, `benchmark_rate`,
  `fx_rate`, `macro_indicator`. For a point-in-time value, take `MAX(date) ≤ asked date`.
- **`cash_balance` multi-view flags (critical):** each row is a unique combination
  of `account_ref, balance_date, date_basis, includes_actual, includes_intraday,
  includes_confirmed, includes_estimated`. For a **clean end-of-day actual cash
  position**, use the view `date_basis='VALUE_DATE'`, `includes_actual=true`,
  `includes_confirmed=true`, `includes_intraday=false`, `includes_estimated=false`
  (adjust per question; "available/projected" includes confirmed+estimated).
- **`bank_statement_balance.balance_type`** ∈ {`OPENING`,`CLOSING`,`INTRADAY`};
  EOD = `CLOSING`. `quality_status` flags ingestion quality — prefer clean rows.

---

## 4. Join graph (relationship map)

Reference keys point to the parent's **business `code`**, not its uuid.

```
company.code  ←─ company_ref  (in ~every fact table)
              ←─ gen_company_region.company_code  → region, functional_ccy
              ←─ company_group_member.company_code → company_group_code → company_group.code
              ←─ source_company_ref / target_company_ref (intercompany_transaction)
              ←─ buyer_company (wcf_document) / applicant_company_ref (letter_of_credit)

bank.code     ←─ bank_ref, lender_bank_ref, counterparty_bank_ref,
                 issuing_bank_ref, issuer_bank_ref, parent_counterparty_ref
bank_branch.code ←─ branch_ref (bank_account);  bank_branch.bank_ref → bank.code

bank_account.code ←─ account_ref / bank_account_ref
                     (cash_balance, cash_flow, bank_statement_balance, bank_fee,
                      sweep_*, investment_position, payment_file, transfer,
                      gl_reconciliation, acquirer.settlement_account_ref)

currency.code ←─ currency_code / currency_ref / *_currency (everywhere)
fx_rate(base_currency, quote_currency, rate_date, rate_type)  ← conversion

cash_flow_code.code ←─ cash_flow.flow_code_ref          (category, sign)
budget_code.code    ←─ cash_flow.budget_code_ref
third_party.code    ←─ vendor_ref (ap_invoice), customer_ref (ar_invoice),
                       supplier_ref (wcf_document), counterparty_ref (cash_flow)

credit_facility.code ←─ borrowing.facility_ref, letter_of_credit.credit_facility_ref
investment_instrument.code ←─ investment_position.instrument_ref,
                              investment_transaction.instrument_ref
fx_forward.deal_id (FXF-…) ←─ hedge_relationship.instrument_ref,
                              derivative_mtm.instrument_ref
stress_scenario.code ←─ stress_run_result.scenario_ref
pension_plan.code    ←─ pension_valuation.plan_ref
peer_company.code    ←─ peer_company_metric.peer_code
forecast_snapshot.snapshot_id ←─ forecast_cash_flow.snapshot_id, forecast_vs_actual.snapshot_id
bank_service_type.code ←─ bank_fee.service_code, fee_rate_card.service_code
card_rebate_program.code ←─ card_rebate_earning.program_ref
gl_account.code ←─ gl_balance.gl_account_ref, gl_reconciliation.gl_account_ref,
                   bank_account.gl_account_ref

payment_file.file_uuid ←─ transfer.file_uuid, payment_transaction.file_uuid,
                          payment_exception.file_uuid
transfer.uuid / payment_transaction.uuid ←─ payment_exception.transfer_uuid,
                                            ach_return.original_transfer_ref (logical)
card_settlement_batch.uuid ←─ card_settlement_line.batch_ref
card_authorization.uuid ←─ card_settlement_line.authorization_ref,
                           chargeback.authorization_ref, fraud_loss.authorization_ref
```

---

## 5. Reference enumerations (controlled vocabularies)

- **`cash_flow_code`** (40): `code`, `sign` (`IN`/`OUT`), `category`
  (`OPERATIONS`/`INVESTING`/`FINANCING`). Drives cash-flow-statement classification.
- **`budget_code`** (4): `BC_CAPEX`, operating, tax, treasury.
- **`credit_facility.facility_type`**: `REVOLVING` (RCF), `TERM_LOAN`, `OVERDRAFT`,
  `COMMERCIAL_PAPER`. Facility codes encode type/ccy/size, e.g. `CF_RCF_EUR_500M`,
  `CF_RCF_USD_…`.
- **`investment_instrument.instrument_type`**: money-market fund (`MMF`), time
  deposit, certificate of deposit (`CD`), treasury, commercial paper, repo, bond.
  Codes like `INV_MMF_0014`. → short-term vs long-term inferred from `maturity_date`.
- **`benchmark_rate.benchmark_code`**: `SOFR`, `SONIA`, `ESTR`, `TIIE`; `tenor` ∈
  {`ON` overnight, `1M`, `3M`}.
- **`macro_indicator.indicator_code`**: Fed Funds Rate, 3M SOFR, US CPI, EUR/USD
  volatility, **Geopolitical Risk Index**, **Brent crude oil price** (by `region`,
  daily, sources FRED/BLS/CME/ICE).
- **`credit_rating.agency`**: `MOODYS`, `SP`, `FITCH`; `rating_grade` (e.g. A2/A/A),
  `outlook` (Stable/Positive/Negative), `rating_action` (affirm/upgrade/downgrade).
- **`capital_allocation_actual.bucket`**: `CAPEX`, `M&A`, `DIVIDENDS`,
  `DEBT_PAYDOWN`, `BUYBACKS` (vs `framework_target_pct`).
- **`equity_action.action_type`**: `BUYBACK`, `DIVIDEND`, `SPECIAL_DIVIDEND`.
- **`hedge_relationship`**: `hedge_type` (cash-flow / net-investment / fair-value),
  `hedged_item_type`, `instrument_type` (FX_FORWARD); `status` (`ACTIVE`,
  de-designated). `fx_forward.status`, `fx_exposure_forecast.source`
  (FORECAST / BUDGET / BALANCE_SHEET / TRANSLATION), `tenor_bucket`.
- **Payment rails** (`payment_transaction.payment_rail`, `transfer.payment_rail`,
  `payment_hub_throughput.payment_rail`): `ACH`, `WIRE`, `SEPA_CT`, `RTP`, etc.
- **`stress_scenario.scenario_type`**: FX shock, rate shift, customer default,
  receivables drop, etc. `parameters` is a structured (super) object.
- **`gl_account.account_class`**: `ASSET`, `LIABILITY`, `EQUITY`, `REVENUE`,
  `EXPENSE`. `gl_balance.source_system`: SAP / NetSuite / Oracle.
- **Status fields** across AR/AP/payments: `PAID`, `OPEN`, `PARTIAL`, `DISPUTED`,
  `WRITTEN_OFF`, `OUTSTANDING`, `REPAID`, etc. (table-specific).

---

## 6. Metric dictionary — canonical definitions → exact columns

> Each metric lists: **authoritative source** (fast, consolidated) and
> **bottom-up source** (operational drill-down). Convert to USD via `fx_rate`
> when aggregating across currencies.

| Metric | Definition | Authoritative source | Bottom-up source |
|---|---|---|---|
| **Consolidated cash & ST investments** | Σ over entities of cash + short-term investments, latest period | `company_financial_metric.cash_and_equivalents + short_term_investments`, latest `period_date` | `cash_balance.amount` (latest `balance_date`, EOD-actual view) + `investment_position.market_value` where instrument `maturity_date` ≤ 1yr |
| **Total debt / ST / LT debt** | Outstanding borrowings | `company_financial_metric.total_debt / short_term_debt / long_term_debt` | Σ `borrowing.principal_amount` where `status='OUTSTANDING'`, by tenor from `repayment_date` |
| **Net debt** | Total debt − cash & equivalents (− ST investments) | `company_financial_metric.net_debt` | total_debt(bottom-up) − cash(bottom-up) |
| **EBITDA (TTM)** | Σ last 4 quarterly EBITDA | Σ `company_financial_metric.ebitda` over 4 latest `period_type='Q'` | — |
| **Debt/EBITDA (TTM)** | total_debt(latest) ÷ EBITDA(TTM) | `company_financial_metric.debt_to_ebitda` (per-period) or compute with TTM EBITDA | — |
| **Leverage ratio / interest coverage** | per definitions | `company_financial_metric.leverage_ratio`, `interest_coverage` | EBITDA ÷ interest_expense |
| **Weighted avg cost of debt (WACD)** | Σ(rate×principal) ÷ Σ principal | `company_financial_metric.weighted_avg_cost_of_debt_pct` | Σ(`borrowing.all_in_rate`×`principal_amount`) ÷ Σ`principal_amount` (outstanding); add facility `spread_bps`+`benchmark_rate` for undrawn-cost view |
| **WACC** | weighted avg cost of capital | `company_financial_metric.wacc_pct` | (cost of debt + cost of equity weighting — equity cost not in schema) |
| **Free cash flow (quarter)** | CFO − capex | `company_financial_metric.free_cash_flow` (or `cash_from_operations − capex`) | `cash_flow` Operations inflows−outflows − CAPEX (`budget_code='BC_CAPEX'`) |
| **Interest expense vs income (year)** | P&L interest | `company_financial_metric.interest_expense` vs `interest_income`, `period_type='FY'` | Σ `interest_accrual.amount` grouped by `direction` over the year |
| **Total liquidity** | cash + ST investments + **undrawn committed facilities** | cash(above) + Σ undrawn (see facilities) | per-region via `gen_company_region` |
| **Undrawn committed facility** | commitment − drawn | `credit_facility.commitment_amount` − Σ`borrowing.principal_amount`(OUTSTANDING) per `facility_ref`, for committed `REVOLVING`/`TERM_LOAN` | — |
| **FX hedge ratio (currency C)** | hedged notional ÷ forecast exposure | Σ `hedge_relationship.notional_amount` (ACTIVE, `hedged_currency=C`) **or** Σ `fx_forward` notional (`status` open, sell/buy = C) ÷ Σ `fx_exposure_forecast.gross_exposure_amount` (`exposure_currency=C`, latest `snapshot_date`, matching `tenor_bucket`/`forecast_period`) | — |
| **Counterparty exposure / concentration** | exposure per bank | `counterparty_exposure.total_exposure`, `pct_of_total` (USD), latest `as_of_date` | deposits + investments + `derivative_mtm.mtm_amount` per `counterparty_bank_ref` |
| **DSO / DPO / DIO / CCC** | working-capital days | `working_capital_metric.dso_days/dpo_days/dio_days/ccc_days` per `company_ref,period_date` | derive from `ar_invoice`/`ap_invoice` aging + COGS |
| **Share-of-wallet (bank)** | bank fees as % of total fees | Σ `bank_fee.charged_amount` by `bank_ref` ÷ total, over window | also `fee_rate_card` for negotiated rates |
| **Funded status (pension)** | plan assets − PBO | `pension_valuation.funded_status`, `funded_status_pct`, `oci_impact`, `projected_contribution_y1..y3` | — |
| **ROIC / return on capital** | NOPAT ÷ invested capital | `company_financial_metric.return_on_capital_pct` | — |
| **Shareholder return yield** | dividends + buybacks ÷ mkt cap | peers: `peer_company_metric.shareholder_return_yield_pct` (and dividend/buyback yield) | own: `equity_action` totals |
| **Forecast accuracy** | |actual−forecast| / actual | `forecast_vs_actual.variance_amount`, `variance_pct` | — |
| **STP rate / payment success** | straight-through %, success/reject | `payment_hub_throughput.stp_rate_pct`, `success_count`, `rejection_count`, `repair_count` | `payment_exception`, `ach_return`, `transfer.status` |

---

## 7. Subject-area table catalog

Format: **table** (rows) — grain — role; key columns; join keys.
USD★ = amounts already standardized to USD.

### 7.1 Cash & liquidity
- **cash_balance** (865k) — account × date × view — daily multi-view balances;
  `amount, currency_code, date_basis, includes_*` flags, `cash_flow_status`;
  join `account_ref → bank_account.code`. *Use canonical EOD-actual filter (§3).*
- **cash_flow** (1.03M) — one cash movement — confirmed & forecast flows;
  `signed_amount, flow_amount/flow_currency, account_amount/account_currency,
  fx_rate, transaction_date, value_date, status, payment_rail, counterparty_ref`;
  classify via `flow_code_ref → cash_flow_code(category, sign)`; `budget_code_ref`.
- **bank_statement_balance** (317k) — account × date × type — bank-fed
  OPENING/CLOSING/INTRADAY balances; reconciliation source vs `cash_balance`.
- **sweep_instruction** (38) / **sweep_execution** (29k) — cash pooling/ZBA;
  executed `swept_amount`, pre/post balances, `source_account_ref→target_account_ref`.
- **liquidity_policy** (10) — min-cash buffers, operating floors, concentration
  limits, tenor caps; `policy_type, threshold_amount/_pct`, scoped to
  `company_ref`/`company_group_ref`, validity dates. *Defines "minimum liquidity".*
- **gl_reconciliation** (2.9k) — monthly bank-vs-GL variance per account.

### 7.2 Debt, facilities & interest
- **credit_facility** (17) — facility agreement — `facility_type, commitment_amount,
  spread_bps, commitment_fee_bps, benchmark_code, start_date, maturity_date,
  lender_bank_ref, currency_ref, status`. *Maturity wall & undrawn capacity source.*
- **borrowing** (651) — drawdown event — `facility_ref, principal_amount,
  all_in_rate, drawdown_date, repayment_date, status` (OUTSTANDING/REPAID).
- **letter_of_credit** (15) — commercial & standby LCs — `face_amount, drawn_amount,
  lc_type, issuing_bank_ref, credit_facility_ref, fee_bps, expiration_date`.
- **interest_accrual** (31.7k) — daily accrual — `source_type` (borrowing/investment/
  facility fee), `amount, direction` (INCOME/EXPENSE), `company_ref, accrual_date`.
- **benchmark_rate** (20k) — SOFR/SONIA/ESTR/TIIE by date×tenor (floating-rate inputs).

### 7.3 Investments
- **investment_instrument** (120) — `instrument_type, issuer_name, issuer_bank_ref,
  coupon_rate, maturity_date, rating, currency_ref` (MMF/CD/TD/treasury/CP/repo/bond).
- **investment_position** (9.5k) — daily holdings — `face_amount, market_value,
  book_value, accrued_interest, yield_to_maturity, duration_days`; by
  `instrument_ref, bank_account_ref, company_ref, as_of_date`. *Portfolio perf/duration.*
- **investment_transaction** (325) — buys/coupons/maturities; `yield_at_trade, price`.

### 7.4 FX & hedging
- **fx_exposure_forecast** (1.5k) — forward exposure — `exposure_currency,
  functional_currency, gross_exposure_amount, direction, tenor_bucket,
  forecast_period, source, snapshot_date` (vintaged). *Denominator of hedge ratio.*
- **fx_forward** (1.8k) — FX forward deals — `deal_id (FXF-…), buy/sell_currency,
  buy/sell_amount, forward_rate, spot_at_trade, value_date, counterparty_bank_ref, status`.
- **hedge_relationship** (606) — hedge-accounting designations — `hedge_type,
  hedged_currency, notional_amount, instrument_ref (→fx_forward.deal_id), status,
  designation_date, dedesignation_date, effectiveness_method`.
- **hedge_dedesignation** (4) — discontinuation events.
- **derivative_mtm** (36.5k) USD★ — daily FX-forward mark-to-market `mtm_amount`
  by `instrument_ref, counterparty_bank_ref, company_ref, valuation_date`.
- **fx_rate** (730k) — market FX (conversion + volatility history).

### 7.5 Counterparty & bank-relationship risk
- **counterparty_exposure** (1.96k) USD★ — daily per-bank exposure split into
  `deposits_amount, investments_amount, derivative_mtm_amount, total_exposure,
  pct_of_total` by `counterparty_bank_ref, as_of_date`.
- **bank** / **bank_branch** — counterparty master incl. `risk_tier_ref`, limits.
- **bank_fee** (12.6k) — fee line per service/account/period — `charged_amount,
  expected_amount, overage_amount, units, service_code, bank_ref, flagged`.
  *Share-of-wallet & fee-leakage source.*
- **fee_rate_card** (650) — negotiated `negotiated_rate` per bank×service×company.
- **bank_service_type** (25) — service catalogue (`category` for aggregation).
- **card_rebate_program** (4) / **card_rebate_earning** (220) — card-program rebates.

### 7.6 Consolidated financials, capital structure & peers
- **company_financial_metric** (136) — per-entity P&L/BS/CF/ratios snapshot by
  `period_date, period_type, reporting_currency`. The headline financial source
  (revenue, ebitda, net_income, diluted_eps, fcf, total/short/long_term_debt,
  net_debt, cash_and_equivalents, short_term_investments, total_equity,
  debt_to_ebitda, leverage_ratio, interest_coverage, wacc_pct,
  weighted_avg_cost_of_debt_pct, return_on_capital_pct, diluted_shares_outstanding,
  fy_*_guidance/target).
- **capital_allocation_actual** (165) — actual `amount` by `bucket` vs
  `framework_target_pct`, per `company_ref, period_date`.
- **equity_action** (23) — buybacks/dividends — `shares, price_per_share,
  total_amount, dividend_per_share, program_name, authorization_remaining,
  action_date, settle_date`.
- **credit_rating** (13) — agency ratings — `agency, rating_grade, outlook,
  rating_action, as_of_date, is_current`.
- **peer_company** (7) / **peer_company_metric** (154) — peer benchmarking incl.
  leverage, debt_to_ebitda, wacc_pct, return_on_capital_pct, dividend/buyback/
  shareholder_return_yield_pct, payments_cost_pct_revenue, fraud_loss_bps, interchange_pct.

### 7.7 Working capital & supply-chain finance
- **working_capital_metric** (806) — per-entity DSO/DPO/DIO/CCC + `ar_balance,
  ap_balance, revenue_ttm, cogs_ttm` by `company_ref, period_date`.
- **ar_invoice** (269k) / **ap_invoice** (180k) — invoice lifecycle —
  `invoice_amount, paid_amount, open_amount, issue/due/paid_date, status,
  payment_terms, company_ref, customer_ref/vendor_ref`. *Aging & DSO/DPO drill-down.*
- **wcf_document** (2.5k) — supply-chain-finance / dynamic-discounting instruments —
  `supplier_ref, buyer_company, amount, issue/due_date, status, early_payment_terms_ref`.

### 7.8 Forecasting & stress
- **forecast_snapshot** (38) — forecast run metadata (`snapshot_id, snapshot_date,
  horizon_start/end_date, granularity, model_version`).
- **forecast_cash_flow** (2.66k) — projected flows by `company_ref, account_ref,
  forecast_date, flow_category, flow_subcategory, direction, forecast_amount,
  confidence, seasonality_factor, snapshot_id`.
- **forecast_vs_actual** (3.75k) — accuracy/variance per company×period×flow_category.
- **stress_scenario** (6) / **stress_run_result** (432) — scenario defs + outcomes
  (`min_projected_cash, breach_date, threshold_amount, breach_severity`).
- **macro_indicator** (777) — Fed Funds, 3M SOFR, CPI, EUR/USD vol, Geopolitical
  Risk Index, Brent crude by region×date.

### 7.9 Payments, cards & operations (working-capital & cost levers)
- **payment_file** (17k), **transfer** (177.9k), **payment_transaction** (177.9k) —
  ISO 20022 pain.001 outbound payments; rails, status, value_date, references.
- **payment_hub_throughput** (2.9k) — daily rail metrics (`volume_count, value_amount,
  success/rejection/repair_count, stp_rate_pct, country, originating_system`).
- **payment_exception** (3.56k), **ach_return** (417) — exceptions, returns, costs.
- **card_authorization** (66k), **card_settlement_batch** (34.7k),
  **card_settlement_line** (63k), **chargeback** (906), **fraud_loss** (32),
  **fraud_detection_event** (121), **acquirer**/**acquirer_contract**/
  **acquirer_sla_metric** — card acquiring economics, interchange, fraud, SLAs.
- **membership_fee** (30k), **pos_transaction** (80k) — membership & retail revenue.
- **intercompany_transaction** (628) — IHB funding/loans/pooling/dividends/royalties.
- **cross_border_payment_leg** (18k) — FX/fees per cross-border leg.

### 7.10 Governance, access & knowledge graph (usually not analytical)
- `app_user, user_group(_member), data_permission(_profile), user_profile_assignment`
  — RBAC/data-scope (use to honor persona/entity permissions, not for finance KPIs).
- `audit_trail, webhook_event` — change/event logs.
- `brain_evaluation, kg_relationship, tribal_knowledge_fact(/entity_link),
  sme_feedback_session, sme_transcript_chunk, v_*_indexable, mapping_table(/entry)`
  — knowledge-graph / RAG / code-mapping plumbing. Surface tribal-knowledge facts
  as qualitative context, not as numeric truth.

---

## 8. Query playbook — the 30 test prompts

> For each: **scope • tables • joins/filters • computation • caveats**. "→USD" =
> translate via `fx_rate(SPOT)` before cross-currency aggregation.

### Tier 1 — single-metric / direct lookups

1. **Consolidated cash & ST investments globally.**
   Tables: `company_financial_metric`. Filter latest `period_date` (per entity).
   Compute Σ(`cash_and_equivalents`+`short_term_investments`)→USD across all entities.
   Drill-down: `cash_balance` (EOD-actual, latest `balance_date`)→USD +
   `investment_position.market_value` for ST instruments. Caveat: pick one period
   per entity; reconcile entity reporting_currency.

2. **Debt-to-EBITDA TTM.**
   Tables: `company_financial_metric`. Numerator `total_debt` latest period;
   denominator Σ`ebitda` over 4 latest `period_type='Q'`. Report `debt_to_ebitda`
   precomputed for sanity. Scope = consolidated (`GR_HOLDINGS`) or Σ entities→USD.

3. **Weighted average cost of debt (portfolio).**
   Authoritative: `company_financial_metric.weighted_avg_cost_of_debt_pct`.
   Bottom-up: Σ(`borrowing.all_in_rate`×`principal_amount`)÷Σ`principal_amount`
   where `status='OUTSTANDING'`→USD-weight. Caveat: include only outstanding draws.

4. **Total liquidity (cash + undrawn committed) by region.**
   Tables: `company_financial_metric` (cash+ST inv) OR `cash_balance`+`investment_position`;
   `credit_facility` − `borrowing`; `gen_company_region`. Undrawn = Σ(`commitment_amount`
   − Σ outstanding `principal_amount` by `facility_ref`) for committed facilities.
   Group by `region`. →USD. Caveat: only committed (RCF/term) facilities count;
   exclude uncommitted/overdraft if flagged.

5. **Current credit rating from each agency.**
   `credit_rating` where `is_current=true`, `company_ref='GR_HOLDINGS'`; return one
   row per `agency` (grade, outlook, rating_action, as_of_date).

6. **Bank relationships with share of wallet (last 12 months).**
   `bank_fee` Σ`charged_amount` by `bank_ref` over trailing 12 months ÷ grand total
   = share of wallet %. Join `bank` for names/tier. Optionally split by
   `bank_service_type.category`. →USD.

7. **YoY change in net debt.**
   `company_financial_metric.net_debt` latest `period_date` vs −1yr (same `period_type`).
   Δ absolute and %. Consolidated or by entity→USD.

8. **Free cash flow, most recent quarter.**
   `company_financial_metric` where `period_type='Q'`, max `period_date`:
   `free_cash_flow` (= `cash_from_operations` − `capex`). Consolidated→USD.

9. **Total interest expense vs interest income for the year.**
   `company_financial_metric` `period_type='FY'` (or Σ 4 Q): `interest_expense` vs
   `interest_income`; net interest. Cross-check Σ`interest_accrual.amount` by `direction`.

10. **FX hedge ratio for forecasted EUR exposures.**
    Numerator: Σ active EUR hedge notional — `hedge_relationship.notional_amount`
    (`hedged_currency='EUR'`, `status='ACTIVE'`) or open `fx_forward` legs in EUR.
    Denominator: Σ`fx_exposure_forecast.gross_exposure_amount` (`exposure_currency='EUR'`,
    latest `snapshot_date`). Ratio = num÷den. Caveat: align tenor_bucket/forecast_period;
    net LONG/SHORT direction.

### Tier 2 — multi-table dashboards & analyses

11. **Treasury dashboard (liquidity, debt profile, investment perf, FX & IR
    hedging, counterparty exposure).** Compose: liquidity (#1,#4 + `liquidity_policy`);
    debt profile (`credit_facility` maturity wall, `borrowing`, WACD #3, `credit_rating`);
    investment performance (`investment_position` MV/YTM/duration, `investment_transaction`);
    FX/IR hedging (#10, `hedge_relationship`, `fx_forward`, `derivative_mtm`,
    `benchmark_rate`); counterparty exposure (`counterparty_exposure` top banks, `pct_of_total`).

12. **Capital structure vs peers (Walmart, Target, Home Depot, Kroger) — leverage,
    liquidity, cost of capital, shareholder returns.** Own: `company_financial_metric`
    (leverage_ratio, debt_to_ebitda, cash, wacc_pct, return_on_capital_pct). Peers:
    `peer_company`+`peer_company_metric` (leverage_ratio, debt_to_ebitda, wacc_pct,
    shareholder_return_yield_pct, dividend/buyback yields), aligned `period_date`.
    Caveat: confirm peer codes exist (WMT/HD/… ; Target/Kroger only if present in 7 rows).

13. **Working-capital efficiency over 8 quarters, decompose into AR/inventory/AP
    by segment.** `working_capital_metric` 8 latest `period_date` per `company_ref`
    (dso/dpo/dio/ccc, ar_balance, ap_balance, revenue_ttm, cogs_ttm). Decompose ΔCCC =
    ΔDSO + ΔDIO − ΔDPO. Drill `ar_invoice`/`ap_invoice` aging. "Segment" = entity or
    `region` (via `gen_company_region`). Caveat: **no inventory-balance table** — DIO
    derives from `dio_days`/`cogs_ttm` only.

14. **Trapped cash by region, applicable taxes, efficient repatriation routes.**
    Cash by entity/region (#1 + `gen_company_region`) minus operating floors
    (`liquidity_policy` OPERATING/MIN_CASH). Routes: `intercompany_transaction`
    (dividends/loans/pooling), `sweep_instruction`/`sweep_execution` pooling structures,
    IHB accounts. Caveat: **no withholding/CIT rate table** — repatriation tax must be
    flagged as external assumption.

15. **Share repurchase program YTD (shares, avg price, spend, remaining auth, EPS &
    share-count impact).** `equity_action` `action_type='BUYBACK'`, `action_date` YTD:
    Σ`shares`, avg price = Σ`total_amount`÷Σ`shares`, Σ`total_amount`,
    `authorization_remaining` (latest). Impact: `company_financial_metric.diluted_shares_outstanding`
    & `diluted_eps` trend.

16. **Rate-move impact on pension funded status, OCI, 3-yr contributions.**
    `pension_valuation` (funded_status(_pct), oci_impact, discount_rate_pct,
    projected_contribution_y1..y3) across `as_of_date`; correlate `discount_rate_pct`
    to `benchmark_rate`/`macro_indicator`. `pension_plan` for plan scope. Caveat:
    sensitivity beyond reported scenarios is modeled, not stored.

17. **Actual capital allocation vs framework (3 yrs).** `capital_allocation_actual`
    by `bucket`, `period_date` (12 quarters): actual `amount` vs `framework_target_pct`
    (× total deployed). Variance per bucket (CAPEX/M&A/dividends/buybacks/debt paydown).

18. **FX risk report (translation exposure by ccy, transaction exposure by entity,
    hedge coverage, YTD FX impact on earnings).** `fx_exposure_forecast` by
    `exposure_currency` (translation: `source` translation/balance-sheet) and by
    `company_ref` (transaction); hedge coverage = hedged notional ÷ exposure (#10) per
    currency; YTD FX impact via `derivative_mtm` Δ + realized `cash_flow.fx_rate`.
    Caveat: no dedicated "FX gain/loss" P&L line — approximate from MTM + revaluation.

19. **Supplier financing / dynamic discounting performance (spend enrolled, discounts
    captured, DPO impact).** `wcf_document` Σ`amount` by `status` (enrolled/financed),
    discounts via `early_payment_terms_ref`; DPO impact via `working_capital_metric.dpo_days`
    and `ap_invoice` paid-vs-terms. Caveat: explicit "discount captured" amount may need
    derivation from terms.

### Tier 3 — modeling / scenario / optimization (data + reasoning)

20. **Capital-structure optimization (current vs optimal under rate/growth/rating
    scenarios; 24-mo issuance/refi actions).** Inputs: `credit_facility`+`borrowing`
    (current mix, maturities, spreads), `company_financial_metric` (EBITDA, leverage,
    coverage, WACD/WACC), `credit_rating` (target grade thresholds), `benchmark_rate`/
    `macro_indicator` (rate environments). Output is a **model** (scenario logic on top
    of stored data) — state assumptions explicitly.

21. **Gulf-war impact on next-6-month projections by entity.** `macro_indicator`
    (Brent crude, Geopolitical Risk Index, FX vol) as shock drivers; `forecast_cash_flow`
    +`forecast_snapshot` (latest, 6-mo horizon) by `company_ref`; `fx_exposure_forecast`
    for FX sensitivity; oil-linked cost via `cash_flow_code`/`budget_code`. Scenario
    overlay = reasoning; flag assumptions. Caveat: no direct commodity-position table.

22. **$5B membership-warehouse expansion eval (financing: cash/debt/sale-leaseback;
    credit metrics, ROIC, shareholder returns).** Base: `company_financial_metric`
    (EBITDA, debt, leverage, return_on_capital_pct, wacc_pct), `credit_facility`/
    `borrowing` (debt capacity), `credit_rating` (rating headroom),
    `capital_allocation_actual` (framework fit). Model pro-forma. Caveat: **no
    sale-leaseback / lease / fixed-asset table** — that financing path is assumption-driven.

23. **Enterprise liquidity stress test (recession, supply-chain, oil spike, −30%
    same-store sales): min liquidity, facility utilization, contingent funding/12mo.**
    `stress_scenario`+`stress_run_result` (min_projected_cash, breach_date, threshold,
    severity by `company_ref`); `forecast_cash_flow` baseline; `credit_facility` undrawn
    headroom (#4); `liquidity_policy` minimums; `macro_indicator` shock calibration;
    `membership_fee`/`pos_transaction` for sales-decline modeling. Contingent actions =
    reasoning over undrawn facilities + sweeps.

24. **Multi-year FX hedging optimization (layered/static/dynamic across top-8
    currencies; cost, P&L volatility, budget-rate protection).** Top-8 by
    Σ`fx_exposure_forecast.gross_exposure_amount` per `exposure_currency`; `fx_forward`/
    `hedge_relationship` current coverage; `fx_rate` history → volatility; `macro_indicator`
    EUR/USD vol; budget rate via `fx_exposure_forecast.source='BUDGET'`. Strategy
    comparison = model.

25. **Integrated treasury risk dashboard — VaR across FX/IR/commodity/counterparty at
    95%/99%.** FX: `fx_exposure_forecast` + `fx_rate` history. IR: `borrowing`/
    `credit_facility` floating exposure + `benchmark_rate` history. Commodity: only
    `macro_indicator` Brent (proxy — **no commodity position table**). Counterparty:
    `counterparty_exposure`. VaR = statistical model over stored time series; state
    confidence-level method.

26. **Optimal funding for $3B maturities/18mo (bonds, term loans, CP, revolver;
    windows, demand, covenants, rating).** `credit_facility.maturity_date` +
    `borrowing.repayment_date` (the wall), `benchmark_rate`/`macro_indicator` (windows),
    `credit_rating` (rating impact), `company_financial_metric` (covenant headroom:
    leverage/coverage). Output = sequencing recommendation (model). Caveat: covenant
    terms not stored explicitly — infer from leverage/coverage ratios.

27. **Real-time payments + ISO 20022 transition impact (cost, working-capital, fraud,
    implementation /3yr).** `payment_hub_throughput` (rail mix, volume/value, stp_rate),
    `transfer`/`payment_transaction` (`payment_rail`: ACH/WIRE/SEPA_CT/RTP),
    `payment_exception`+`ach_return`+`fraud_loss` (error/fraud cost), `bank_fee`
    (per-transaction cost), DSO/DPO (`working_capital_metric`) for WC benefit. Costs/
    savings = model.

28. **Treasury KPI scorecard vs best-in-class (25 metrics).** Forecast accuracy
    (`forecast_vs_actual`), idle cash (`cash_balance` vs `liquidity_policy`), cost per
    transaction (`bank_fee`÷`payment_hub_throughput.volume_count`), hedge effectiveness
    (`hedge_relationship.effectiveness_method`/`derivative_mtm`), STP (`payment_hub_throughput`),
    fraud bps (`fraud_loss` vs throughput), DSO/DPO/CCC (`working_capital_metric`).
    Caveat: **"best-in-class" external benchmarks not in schema** — use `peer_company_metric`
    where comparable, else flag as external targets.

29. **Sustainability-linked financing analysis (green bonds, SLLs, SCF aligned to ESG
    targets; pricing benefits, reporting).** SCF: `wcf_document`. Instruments:
    `credit_facility`/`borrowing` (spreads as pricing baseline). Caveat: **no ESG /
    sustainability / green-instrument flag or KPI table exists** — green-bond/SLL
    structures and ESG targets are NOT represented; treat this prompt as largely
    out-of-data and answer with explicit assumptions + the SCF/financing proxies only.

30. *(Reserved / overflow — same patterns as 20–29 for any additional optimization,
    benchmarking, or scenario prompt: anchor on stored time-series + dimension tables,
    label modeled assumptions, and surface data gaps from §9.)*

---

## 9. Capability boundaries (prevent hallucination)

The schema **does NOT contain**, so do not fabricate — flag as external/assumption:
- **Tax rates** (withholding, corporate income, repatriation) — needed for trapped-cash
  repatriation (#14); only cash positions and intercompany routes exist.
- **Inventory balances** — only `working_capital_metric.dio_days`/`cogs_ttm`; no
  SKU/inventory table (#13).
- **Leases / sale-leaseback / fixed-asset register** — financing path in #22 is modeled.
- **Commodity positions/hedges** — only `macro_indicator` Brent as a price proxy (#25).
- **ESG / sustainability KPIs / green-instrument tags** — absent (#29).
- **Explicit debt covenant terms** — infer from leverage/coverage ratios (#26).
- **Equity cost of capital components** — `wacc_pct` is stored but not decomposed.
- **A dedicated FX P&L line** — approximate from `derivative_mtm` + revaluation (#18).

When a question depends on missing data, the model should: (a) name the gap,
(b) answer with the closest stored proxy, and (c) state any assumption used.

### Time & freshness
- Always resolve "latest" via `MAX(period_date / as_of_date / snapshot_date /
  balance_date)` within scope; never assume a hard-coded current date.
- Snapshot/vintaged tables (`forecast_*`, `fx_exposure_forecast`) can hold multiple
  vintages — pin to one `snapshot_id`/`snapshot_date` unless trending vintages.
