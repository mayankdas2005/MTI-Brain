-- =============================================================================
-- 99_validate.sql (Redshift) — QA validation queries.
-- =============================================================================
SET search_path TO lpp;

-- V1. Cash flow ↔ closing balance reconciliation
WITH cf_sum AS (
    SELECT account_ref, value_date, SUM(signed_amount) AS net
    FROM cash_flow WHERE value_date IS NOT NULL
    GROUP BY account_ref, value_date
),
bsb_lag AS (
    SELECT account_ref, statement_date, amount,
           LAG(amount,1) OVER (PARTITION BY account_ref ORDER BY statement_date) AS prev_amt
    FROM bank_statement_balance WHERE balance_type='CLOSING'
),
bsb_delta AS (
    SELECT account_ref, statement_date, amount - NVL(prev_amt,0) AS bal_delta FROM bsb_lag
)
SELECT 'V1_SIGN_DELTA_MISMATCH' AS check_name, COUNT(*) AS violations
FROM cf_sum c JOIN bsb_delta b ON b.account_ref=c.account_ref AND b.statement_date=c.value_date
WHERE ABS(c.net - b.bal_delta) > 0.01;

-- V2. Sweep legs present
SELECT 'V2_SWEEP_LEGS_MISSING' AS check_name, COUNT(*) AS violations
FROM sweep_execution
WHERE status='EXECUTED' AND swept_amount > 0
  AND (cash_flow_uuid IS NULL OR counter_cash_flow_uuid IS NULL);

-- V3. Sweep legs net to zero
WITH sw AS (
    SELECT sx.uuid AS sx_id, SUM(cf.signed_amount) AS net_legs
    FROM sweep_execution sx
    JOIN cash_flow cf ON cf.uuid IN (sx.cash_flow_uuid, sx.counter_cash_flow_uuid)
    WHERE sx.status='EXECUTED' AND sx.swept_amount > 0
    GROUP BY sx.uuid
)
SELECT 'V3_SWEEP_LEGS_NOT_NET_ZERO' AS check_name, COUNT(*) AS violations
FROM sw WHERE ABS(net_legs) > 0.01;

-- V4. IC SETTLED rows have both legs
SELECT 'V4_IC_SETTLED_LEGS_MISSING' AS check_name, COUNT(*) AS violations
FROM intercompany_transaction
WHERE status='SETTLED' AND (source_cash_flow_ref IS NULL OR target_cash_flow_ref IS NULL);

-- V5. AR_INVOICE arithmetic
SELECT 'V5_AR_INVOICE_AMOUNT_MISMATCH' AS check_name, COUNT(*) AS violations
FROM ar_invoice WHERE ABS(COALESCE(paid_amount,0) + COALESCE(open_amount,0) - invoice_amount) > 0.01;

-- V6. AP_INVOICE arithmetic
SELECT 'V6_AP_INVOICE_AMOUNT_MISMATCH' AS check_name, COUNT(*) AS violations
FROM ap_invoice WHERE ABS(COALESCE(paid_amount,0) + COALESCE(open_amount,0) - invoice_amount) > 0.01;

-- V7. Bank fee overage flagging
SELECT 'V7_BANK_FEE_OVERAGE_NOT_FLAGGED' AS check_name, COUNT(*) AS violations
FROM bank_fee
WHERE expected_amount IS NOT NULL AND charged_amount > expected_amount * 1.10 AND NOT flagged;

-- V8. No INVESTMENT_POSITION beyond maturity
SELECT 'V8_INV_POSITION_AFTER_MATURITY' AS check_name, COUNT(*) AS violations
FROM investment_position p JOIN investment_instrument i ON i.code = p.instrument_ref
WHERE i.maturity_date IS NOT NULL AND p.as_of_date > i.maturity_date;

-- V9. Cross-currency CONFIRMED cash flows have FX_RATE coverage on value_date.
-- FORECAST flows past WINDOW_END are excluded by design (no SPOT past window).
-- The scripted BRL gap (V14) is also excluded.
SELECT 'V9_CASHFLOW_FX_RATE_GAP' AS check_name, COUNT(*) AS violations
FROM cash_flow cf
WHERE cf.flow_currency <> cf.account_currency
  AND cf.status = 'CONFIRMED'
  AND NOT (cf.flow_currency='USD' AND cf.account_currency='BRL'
           AND cf.value_date IN (DATE '2024-11-12', DATE '2024-11-13', DATE '2024-11-14'))
  AND NOT EXISTS (SELECT 1 FROM fx_rate fx
                  WHERE fx.rate_date = cf.value_date
                    AND fx.base_currency = cf.flow_currency
                    AND fx.quote_currency = cf.account_currency
                    AND fx.rate_type='SPOT');

-- V10. No future-dated CONFIRMED cash flows beyond 2026-05-04
SELECT 'V10_FUTURE_CONFIRMED_CASHFLOW' AS check_name, COUNT(*) AS violations
FROM cash_flow WHERE status='CONFIRMED' AND value_date > DATE '2026-05-04';

-- V11. Closed-account integrity
SELECT 'V11_FLOWS_ON_CLOSED_ACCOUNT' AS check_name, COUNT(*) AS violations
FROM cash_flow cf JOIN bank_account ba ON ba.code = cf.account_ref
WHERE ba.closed_account AND cf.value_date > ba.closing_date;

-- V12. Hedge notional matches FX_FORWARD buy_amount within 1%
SELECT 'V12_HEDGE_NOTIONAL_MISMATCH' AS check_name, COUNT(*) AS violations
FROM hedge_relationship hr JOIN fx_forward f ON f.deal_id = hr.instrument_ref
WHERE hr.instrument_type='FX_FORWARD'
  AND ABS(hr.notional_amount - f.buy_amount) / NULLIF(f.buy_amount,0) > 0.01;

-- V13. CDOR cessation enforced
SELECT 'V13_CDOR_AFTER_CESSATION' AS check_name, COUNT(*) AS violations
FROM benchmark_rate WHERE benchmark_code='CDOR' AND rate_date > DATE '2024-06-28';

-- V14. BRL stale FX gap
SELECT 'V14_BRL_STALE_FX_GAP' AS check_name, 3 - COUNT(*) AS gap_violations
FROM ( SELECT calendar_date FROM gen_calendar
       WHERE calendar_date IN (DATE '2024-11-12', DATE '2024-11-13', DATE '2024-11-14') ) c
WHERE NOT EXISTS (SELECT 1 FROM fx_rate fx WHERE fx.rate_date=c.calendar_date
                                            AND fx.base_currency='USD' AND fx.quote_currency='BRL'
                                            AND fx.rate_type='SPOT');

-- V15. Iberica writeoffs (expected ≥35)
SELECT 'V15_IBERICA_WRITEOFFS' AS check_name, COUNT(*) AS expected_ge_35
FROM ar_invoice WHERE customer_ref='TP_CUST_0042' AND status='WRITTEN_OFF';

-- V16. BEC fraud incident present
SELECT 'V16_BEC_FRAUD_PRESENT' AS check_name, COUNT(*) AS expected_2
FROM cash_flow WHERE reference IN ('BEC-2024-09-14','BEC-2024-09-14-REC');

-- V17. Counterparty exposure has rows in BANK_DB breach window
SELECT 'V17_DB_BREACH_DATA' AS check_name, COUNT(*) AS rows_in_window
FROM counterparty_exposure
WHERE counterparty_bank_ref='BANK_DB' AND as_of_date BETWEEN DATE '2024-04-12' AND DATE '2024-05-08';

-- V18. Scripted sweep failure clusters
SELECT 'V18_SCRIPTED_SWEEP_FAILURES' AS check_name, COUNT(*) AS scripted_failures
FROM sweep_execution
WHERE status='FAILED'
  AND ( (execution_date BETWEEN DATE '2024-02-12' AND DATE '2024-02-15')
     OR (execution_date BETWEEN DATE '2024-04-29' AND DATE '2024-05-06')
     OR  execution_date= DATE '2023-11-24');

-- V19. Cross-currency AP coverage
SELECT 'V19_AP_CROSS_CCY_PCT' AS check_name,
       ROUND((100.0*COUNT(CASE WHEN ap.currency_code <> cr.functional_ccy THEN 1 END)/COUNT(*))::NUMERIC, 2) AS pct
FROM ap_invoice ap JOIN gen_company_region cr ON cr.company_code = ap.company_ref;

-- V20. AR invoices linked to AR cash flows
SELECT 'V20_AR_INVOICE_TO_CF_LINK' AS check_name,
       COUNT(*) AS unlinked_paid_b2b_invoices
FROM ar_invoice ai
WHERE ai.status='PAID' AND ai.customer_ref<>'RETAIL_CONSUMER'
  AND ai.paid_date IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM cash_flow cf WHERE cf.reference = ai.invoice_number);

-- V21. AP invoices linked
SELECT 'V21_AP_INVOICE_TO_CF_LINK' AS check_name,
       COUNT(*) AS unlinked_paid_invoices
FROM ap_invoice ap
WHERE ap.status IN ('PAID','PARTIAL') AND ap.paid_date IS NOT NULL AND ap.paid_amount > 0
  AND NOT EXISTS (SELECT 1 FROM cash_flow cf WHERE cf.reference = ap.invoice_number);

-- V22. Opening balances seeded
SELECT 'V22_MISSING_OPENING_BAL' AS check_name, COUNT(*) AS missing
FROM bank_account WHERE initial_accounting_balance IS NULL AND NOT closed_account;

-- V23. Third-party hash references resolve
SELECT 'V23_AP_VENDOR_REF_INVALID' AS check_name, COUNT(*) AS violations
FROM ap_invoice ap
WHERE ap.vendor_ref NOT IN (SELECT code FROM third_party);

-- V24. POS interchange present
SELECT 'V24_POS_INTERCHANGE_PRESENT' AS check_name, COUNT(*) AS interchange_rows
FROM cash_flow WHERE flow_code_ref='CARD_INTERCHANGE';

-- V25. CAPEX flows present
SELECT 'V25_CAPEX_FLOWS_PRESENT' AS check_name, COUNT(*) AS capex_rows
FROM cash_flow WHERE flow_code_ref='DISB_CAPEX';

-- V26. APAC distribution-center ramp
SELECT 'V26_APAC_CAPEX_OVERRUN' AS check_name, ROUND(SUM(ABS(flow_amount))) AS h2_2024_capex_usd
FROM cash_flow
WHERE flow_code_ref='DISB_CAPEX'
  AND transaction_date BETWEEN DATE '2024-07-01' AND DATE '2024-12-31'
  AND account_ref LIKE 'APC_RGNL%';

-- V27. RTP/FedNow ramp
SELECT 'V27_RTP_BEFORE_2024' AS check_name, COUNT(*) AS rows_before_jan_2024
FROM cash_flow WHERE flow_code_ref='DISB_AP_RTP' AND value_date < DATE '2024-01-01';

SELECT 'V27_RTP_AFTER_2024' AS check_name, COUNT(*) AS rows_after_jan_2024
FROM cash_flow WHERE flow_code_ref='DISB_AP_RTP' AND value_date >= DATE '2024-01-01';

-- V28. PAYMENT_FILE / TRANSFER populated
SELECT 'V28_PAYMENT_FILE_POPULATED' AS check_name, COUNT(*) AS rows FROM payment_file;
SELECT 'V28_TRANSFER_POPULATED' AS check_name, COUNT(*) AS rows FROM transfer;

-- V29. INTRADAY balances for top 30 accounts
SELECT 'V29_INTRADAY_BALANCES' AS check_name,
       COUNT(DISTINCT account_ref) AS accounts_with_intraday
FROM bank_statement_balance WHERE balance_type='INTRADAY';

-- V30. Quality FAILED outage cluster
SELECT 'V30_FAILED_OUTAGE_CLUSTER' AS check_name, COUNT(*) AS rows_expected_4
FROM bank_statement_balance
WHERE account_ref='USA_RGNL_OPERATING' AND quality_status='FAILED'
  AND statement_date BETWEEN DATE '2025-09-15' AND DATE '2025-09-18'
  AND balance_type='CLOSING';

-- V31. All 7 CASH_BALANCE combos present
SELECT 'V31_CASH_BALANCE_COMBOS' AS check_name,
       COUNT(DISTINCT date_basis||'|'||CASE WHEN includes_actual THEN 'T' ELSE 'F' END||'|'||CASE WHEN includes_intraday THEN 'T' ELSE 'F' END
                       ||'|'||CASE WHEN includes_confirmed THEN 'T' ELSE 'F' END||'|'||CASE WHEN includes_estimated THEN 'T' ELSE 'F' END) AS combos
FROM cash_balance;

-- V32. APP_USER + permission profile cover all opcos
SELECT 'V32_PERMISSIONS_COVER_OPCOS' AS check_name,
       COUNT(DISTINCT entity_code) AS distinct_companies
FROM data_permission WHERE entity_type='COMPANY';

-- =============================================================================
-- Extensions #2 validation checks (V33-V45) — card acquiring, corporate
-- finance, peer benchmarks, signatories.
-- =============================================================================

-- V33. APPROVED card_authorizations should have a settlement line, except for
-- auths within the last 5 days (still pending settlement).
SELECT 'V33_APPROVED_AUTHS_WITHOUT_SETTLE' AS check_name, COUNT(*) AS violations
FROM card_authorization ca
WHERE ca.decision='APPROVED'
  AND ca.auth_response_ts::DATE < DATEADD(day, -5, DATE '2026-05-04')
  AND NOT EXISTS (SELECT 1 FROM card_settlement_line sl WHERE sl.authorization_ref = ca.uuid);

-- V34. Monthly chargeback total ≤ 2% of approved auth amount (system-wide).
WITH m_auth AS (
    SELECT DATE_TRUNC('month', auth_response_ts) AS mo,
           SUM(amount) AS approved_amt
    FROM card_authorization WHERE decision='APPROVED'
    GROUP BY DATE_TRUNC('month', auth_response_ts)
),
m_cb AS (
    SELECT DATE_TRUNC('month', initiated_date::TIMESTAMP) AS mo,
           SUM(amount) AS cb_amt
    FROM chargeback
    GROUP BY DATE_TRUNC('month', initiated_date::TIMESTAMP)
)
SELECT 'V34_MONTHLY_CB_OVER_2PCT' AS check_name,
       COUNT(*) AS months_over_2pct
FROM m_auth a JOIN m_cb c ON c.mo = a.mo
WHERE c.cb_amt > a.approved_amt * 0.02;

-- V35. Settlement batch arithmetic: gross - refunds - chargebacks - fees ≈ net
SELECT 'V35_SETTLE_BATCH_OUT_OF_BAL' AS check_name, COUNT(*) AS violations
FROM card_settlement_batch
WHERE ABS(COALESCE(gross_sales_amount,0) - COALESCE(refund_amount,0) - COALESCE(chargeback_amount,0)
         - COALESCE(interchange_amount,0) - COALESCE(network_assessment_amount,0)
         - COALESCE(processor_margin_amount,0) - COALESCE(other_fees_amount,0)
         - COALESCE(net_settlement_amount,0))
       > GREATEST(0.01, COALESCE(gross_sales_amount,0) * 0.0001);

-- V36. At least one acquirer contract due to renew within 12 months of WINDOW_END.
SELECT 'V36_CONTRACT_RENEW_WITHIN_12M' AS check_name, COUNT(*) AS contracts
FROM acquirer_contract
WHERE effective_to BETWEEN DATE '2026-05-04' AND DATEADD(month, 12, DATE '2026-05-04')
  AND contract_status='ACTIVE';

-- V37. ACH return reason mix — R01 should be ≥ 30%.
SELECT 'V37_ACH_R01_PCT' AS check_name,
       ROUND(100.0 * COUNT(CASE WHEN return_reason_code='R01' THEN 1 END) / NULLIF(COUNT(*),0), 2) AS pct_r01
FROM ach_return;

-- V38. At least one rebate program scripted to miss threshold.
WITH prog_year AS (
    SELECT program_ref, EXTRACT(YEAR FROM period_date) AS yr, SUM(eligible_spend) AS spend
    FROM card_rebate_earning GROUP BY program_ref, EXTRACT(YEAR FROM period_date)
)
SELECT 'V38_REBATE_MISS' AS check_name, COUNT(*) AS programs_missing
FROM prog_year py
JOIN card_rebate_program p ON p.code = py.program_ref
WHERE py.yr = 2024 AND py.spend < p.rebate_tier_threshold;

-- V39. payment_exception with status=RESOLVED must have resolution_time_minutes.
SELECT 'V39_PE_RESOLUTION_TIME_MISSING' AS check_name, COUNT(*) AS violations
FROM payment_exception
WHERE status='RESOLVED' AND resolution_time_minutes IS NULL;

-- V40. ≥1 LC expiring within 90 days of WINDOW_END.
SELECT 'V40_LC_EXPIRING_WITHIN_90D' AS check_name, COUNT(*) AS lcs
FROM letter_of_credit
WHERE status='OPEN'
  AND expiration_date BETWEEN DATE '2026-05-04' AND DATEADD(day, 90, DATE '2026-05-04');

-- V41. Pension funded_status sign change for US plan between 2024 and 2025.
SELECT 'V41_PENSION_FUNDED_FLIP' AS check_name,
       COUNT(*) AS expected_at_least_1
FROM (
    SELECT plan_ref,
           MAX(CASE WHEN as_of_date=DATE '2024-12-31' THEN SIGN(funded_status) END) AS sign_2024,
           MAX(CASE WHEN as_of_date=DATE '2025-12-31' THEN SIGN(funded_status) END) AS sign_2025
    FROM pension_valuation GROUP BY plan_ref
) x
WHERE sign_2024 <> sign_2025;

-- V42. Each top-revenue company should have 12 quarters of company_financial_metric.
SELECT 'V42_FIN_METRIC_QUARTERS' AS check_name, MIN(q_count) AS min_quarters_per_top_entity
FROM (
    SELECT company_ref, COUNT(*) AS q_count
    FROM company_financial_metric
    WHERE period_type='Q'
      AND company_ref IN ('GR_HOLDINGS','GR_US_INC','GR_EU_BV','GR_APAC_PTE','GR_LATAM_SA','GR_GB','GR_DE','GR_TREASURY')
    GROUP BY company_ref
) x;

-- V43. peer_company_metric: ≥20 quarters per peer.
SELECT 'V43_PEER_QUARTERS' AS check_name, MIN(q_count) AS min_q_per_peer
FROM (
    SELECT peer_code, COUNT(*) AS q_count
    FROM peer_company_metric WHERE period_type='Q'
    GROUP BY peer_code
) x;

-- V44. bank_account_signatory: ≥45 rows due within 90 days of WINDOW_END.
SELECT 'V44_SIG_RECERTIFY_BACKLOG' AS check_name, COUNT(*) AS sigs_due_within_90d
FROM bank_account_signatory
WHERE next_recertify_due_date BETWEEN DATE '2026-05-04' AND DATEADD(day, 90, DATE '2026-05-04');

-- V45. payment_hub_throughput: ≥90% of business days covered per rail
-- (denominator = business days between rail's first and last metric_date, inclusive)
WITH rail_range AS (
    SELECT payment_rail,
           MIN(metric_date) AS first_day,
           MAX(metric_date) AS last_day,
           COUNT(DISTINCT metric_date) AS days_covered
    FROM payment_hub_throughput
    GROUP BY payment_rail
),
rail_bd AS (
    SELECT r.payment_rail,
           r.days_covered,
           COUNT(bd.calendar_date) AS active_business_days
    FROM rail_range r
    JOIN v_business_days bd
      ON bd.calendar_date BETWEEN r.first_day AND r.last_day
    GROUP BY r.payment_rail, r.days_covered
)
SELECT 'V45_PHUB_RAIL_COVERAGE' AS check_name,
       MIN(ROUND(100.0 * days_covered / NULLIF(active_business_days, 0), 2)) AS min_pct_coverage
FROM rail_bd;

-- Summary
SELECT '==== Validation summary ====' AS summary,
       'V1-V13: 0 violations expected. V14 gap=0. V15 >=35. V16=2. V17>0. V18>0. '||
       'V19>=10. V20=0. V21 small (CNY suppressed). V22=0. V23=0. '||
       'V24>0. V25>0. V26 large. V27 before=0/after>0. V28>0. V29=30. V30=4. V31=7. V32=24. '||
       'V33=0 (excluding tail). V34=0 (or small). V35=0. V36>=1. V37>=30. V38>=1. V39=0. '||
       'V40>=2. V41>=1. V42=12. V43>=20. V44>=45. V45>=90.'
       AS expectations;
