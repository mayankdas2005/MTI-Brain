-- =============================================================================
-- 07_investments_debt.sql (Redshift) — INVESTMENT_POSITION, INVESTMENT_TRANSACTION,
-- BORROWING, INTEREST_ACCRUAL
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 7.1 Update INVESTMENT_INSTRUMENT.coupon_rate from BENCHMARK_RATE on issue date
-- (Redshift UPDATE...FROM syntax)
-- -----------------------------------------------------------------------------
UPDATE investment_instrument
SET coupon_rate = bm.rate + (CASE investment_instrument.instrument_type
                                WHEN 'MMF'              THEN -0.05
                                WHEN 'TIME_DEPOSIT'     THEN  0.10
                                WHEN 'CD'               THEN  0.20
                                WHEN 'TREASURY'         THEN -0.10
                                WHEN 'COMMERCIAL_PAPER' THEN  0.30
                                WHEN 'REPO'             THEN  0.05
                                WHEN 'BOND'             THEN  0.40
                                ELSE 0 END)
FROM (SELECT * FROM benchmark_rate WHERE benchmark_code IN ('SOFR','ESTR','SONIA','TONA','SORA') AND tenor='ON') bm
WHERE bm.rate_date = COALESCE(investment_instrument.issue_date, DATE '2023-05-01')
  AND ((bm.benchmark_code='SOFR' AND investment_instrument.currency_ref='USD')
    OR (bm.benchmark_code='ESTR' AND investment_instrument.currency_ref='EUR')
    OR (bm.benchmark_code='SONIA' AND investment_instrument.currency_ref='GBP')
    OR (bm.benchmark_code='TONA'  AND investment_instrument.currency_ref='JPY')
    OR (bm.benchmark_code='SORA'  AND investment_instrument.currency_ref='SGD'));

-- -----------------------------------------------------------------------------
-- 7.2 INVESTMENT_TRANSACTION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE investment_transaction;

INSERT INTO investment_transaction (
    uuid, instrument_ref, company_ref, bank_account_ref,
    transaction_type, trade_date, settle_date,
    amount, currency_code, price, yield_at_trade
)
SELECT gen_uuid('IT_BUY_'||i.code), i.code,'GR_TREASURY','IHB_'||i.currency_ref||'_INVESTMENT',
       'PURCHASE', i.issue_date, add_business_days(i.issue_date,1),
       -ROUND((EXP(15 + 0.4 * gen_normal('FACE_'||i.code))
               * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
       i.currency_ref,
       CASE WHEN i.instrument_type='TREASURY' THEN 0.99 + 0.005*gen_rand(i.code) ELSE 1.0 END,
       i.coupon_rate
FROM investment_instrument i WHERE i.issue_date IS NOT NULL;

INSERT INTO investment_transaction (
    uuid, instrument_ref, company_ref, bank_account_ref,
    transaction_type, trade_date, settle_date,
    amount, currency_code, yield_at_trade
)
SELECT gen_uuid('IT_MAT_'||i.code), i.code,
       'GR_TREASURY','IHB_'||i.currency_ref||'_INVESTMENT',
       'MATURITY', i.maturity_date, i.maturity_date,
       ROUND((ABS(b.amount) * (1 + COALESCE(i.coupon_rate,0)/100.0
              * DATEDIFF(day,i.issue_date,i.maturity_date)/365.0))::NUMERIC,2),
       i.currency_ref, i.coupon_rate
FROM investment_instrument i
JOIN investment_transaction b ON b.instrument_ref = i.code AND b.transaction_type='PURCHASE'
WHERE i.maturity_date IS NOT NULL AND i.maturity_date <= DATE '2026-05-04';

INSERT INTO investment_transaction (
    uuid, instrument_ref, company_ref, bank_account_ref,
    transaction_type, trade_date, settle_date,
    amount, currency_code
)
SELECT gen_uuid('IT_CPN_'||i.code||'_'||CAST(c.calendar_date AS VARCHAR)),
       i.code, 'GR_TREASURY', 'IHB_'||i.currency_ref||'_INVESTMENT',
       'COUPON', c.calendar_date, c.calendar_date,
       ROUND((ABS(b.amount) * i.coupon_rate / 100.0 / 2.0)::NUMERIC, 2),
       i.currency_ref
FROM investment_instrument i
JOIN investment_transaction b ON b.instrument_ref = i.code AND b.transaction_type='PURCHASE'
JOIN gen_calendar c ON EXTRACT(DAY FROM c.calendar_date)=EXTRACT(DAY FROM i.issue_date)
                   AND EXTRACT(MONTH FROM c.calendar_date) IN (EXTRACT(MONTH FROM i.issue_date), MOD(EXTRACT(MONTH FROM i.issue_date)::INT+5,12)+1)
                   AND c.calendar_date > i.issue_date
                   AND c.calendar_date < COALESCE(i.maturity_date, DATE '2026-05-04')
WHERE i.instrument_type IN ('BOND','TREASURY','TIME_DEPOSIT') AND i.coupon_rate IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 7.3 INVESTMENT_POSITION (weekly snapshots, Friday DOW=5)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE investment_position;
INSERT INTO investment_position (
    uuid, instrument_ref, company_ref, bank_account_ref,
    as_of_date, face_amount, market_value, book_value,
    accrued_interest, currency_code, yield_to_maturity, duration_days
)
SELECT gen_uuid('IP_'||i.code||'_'||CAST(c.calendar_date AS VARCHAR)),
       i.code, 'GR_TREASURY', 'IHB_'||i.currency_ref||'_INVESTMENT',
       c.calendar_date, ABS(b.amount),
       ROUND((ABS(b.amount) * (1 + 0.005 * gen_normal(i.code||'_'||CAST(c.calendar_date AS VARCHAR))))::NUMERIC,2),
       ABS(b.amount),
       ROUND((ABS(b.amount) * COALESCE(i.coupon_rate,0)/100.0
              * DATEDIFF(day, i.issue_date, c.calendar_date)/365.0)::NUMERIC, 2),
       i.currency_ref, i.coupon_rate,
       DATEDIFF(day, c.calendar_date, COALESCE(i.maturity_date, DATEADD(year,1,c.calendar_date)))
FROM investment_instrument i
JOIN investment_transaction b ON b.instrument_ref = i.code AND b.transaction_type='PURCHASE'
JOIN gen_calendar c ON EXTRACT(DOW FROM c.calendar_date)=5
                   AND c.calendar_date >= i.issue_date
                   AND c.calendar_date < COALESCE(i.maturity_date, DATE '2026-05-05');

-- -----------------------------------------------------------------------------
-- 7.4 BORROWING
-- -----------------------------------------------------------------------------
TRUNCATE TABLE borrowing;

DROP TABLE IF EXISTS t_bmk_on_date;
CREATE TEMP TABLE t_bmk_on_date AS
SELECT rate_date, benchmark_code, currency_code, rate
FROM benchmark_rate WHERE tenor='ON';

-- RCF USD 2B
INSERT INTO borrowing (uuid, facility_ref, company_ref, drawdown_date, repayment_date,
                       principal_amount, currency_code, all_in_rate, status)
SELECT gen_uuid('BRW_RCF_USD_'||CAST(y AS VARCHAR)),
       'CF_RCF_USD_2B','GR_HOLDINGS',
       TO_DATE(CAST(y AS VARCHAR)||'-08-15','YYYY-MM-DD'),
       TO_DATE(CAST(y+1 AS VARCHAR)||'-02-15','YYYY-MM-DD'),
       300000000 + MOD(ABS(FNV_HASH(CAST(y AS VARCHAR))),50000001),
       'USD',
       (SELECT rate FROM t_bmk_on_date WHERE benchmark_code='SOFR' AND rate_date = TO_DATE(CAST(y AS VARCHAR)||'-08-15','YYYY-MM-DD')) + 1.25,
       CASE WHEN y < 2026 THEN 'REPAID' ELSE 'OUTSTANDING' END
FROM (SELECT 2023 AS y UNION ALL SELECT 2024 UNION ALL SELECT 2025) yrs;

-- RCF EUR
INSERT INTO borrowing (uuid, facility_ref, company_ref, drawdown_date, repayment_date,
                       principal_amount, currency_code, all_in_rate, status)
SELECT gen_uuid('BRW_RCF_EUR_'||CAST(y AS VARCHAR)),
       'CF_RCF_EUR_500M','GR_EU_BV',
       TO_DATE(CAST(y AS VARCHAR)||'-09-01','YYYY-MM-DD'),
       TO_DATE(CAST(y+1 AS VARCHAR)||'-01-31','YYYY-MM-DD'),
       100000000 + MOD(ABS(FNV_HASH('EUR'||CAST(y AS VARCHAR))),30000001), 'EUR',
       (SELECT rate FROM t_bmk_on_date WHERE benchmark_code='ESTR' AND rate_date = TO_DATE(CAST(y AS VARCHAR)||'-09-01','YYYY-MM-DD')) + 1.10,
       CASE WHEN y < 2026 THEN 'REPAID' ELSE 'OUTSTANDING' END
FROM (SELECT 2023 AS y UNION ALL SELECT 2024 UNION ALL SELECT 2025) yrs;

-- Term loan
INSERT INTO borrowing (uuid, facility_ref, company_ref, drawdown_date, repayment_date,
                       principal_amount, currency_code, all_in_rate, status)
SELECT gen_uuid(code), 'CF_TERM_USD_300M','GR_HOLDINGS', dd, rd, amt, 'USD',
       (SELECT rate FROM t_bmk_on_date WHERE benchmark_code='SOFR' AND rate_date = dd) + 1.75, st
FROM (
    SELECT 'BRW_TERM_DRAW' AS code, DATE '2023-08-01' AS dd, DATE '2028-08-01' AS rd, 300000000::NUMERIC AS amt,'OUTSTANDING' AS st UNION ALL
    SELECT 'BRW_TERM_AM_2024', DATE '2024-08-01', DATE '2024-08-01', 30000000, 'REPAID' UNION ALL
    SELECT 'BRW_TERM_AM_2025', DATE '2025-08-01', DATE '2025-08-01', 30000000, 'REPAID'
) t;

-- CP rolls
INSERT INTO borrowing (uuid, facility_ref, company_ref, drawdown_date, repayment_date,
                       principal_amount, currency_code, all_in_rate, status)
SELECT
    gen_uuid('BRW_CP_'||CAST(n+1 AS VARCHAR)), 'CF_CP_USD_750M','GR_HOLDINGS',
    DATEADD(day, MOD(ABS(FNV_HASH('CP_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01'),
    DATEADD(day, MOD(ABS(FNV_HASH('CP_TEN|'||CAST(n AS VARCHAR))),61)+30,
            DATEADD(day, MOD(ABS(FNV_HASH('CP_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')),
    50000000 + MOD(ABS(FNV_HASH('CP_AMT|'||CAST(n AS VARCHAR))),100000001),
    'USD',
    (SELECT rate FROM t_bmk_on_date
      WHERE benchmark_code='SOFR'
        AND rate_date = DATEADD(day, MOD(ABS(FNV_HASH('CP_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')) + 0.35,
    CASE WHEN MOD(ABS(FNV_HASH('CP_ST|'||CAST(n AS VARCHAR))),100)<5 THEN 'OUTSTANDING' ELSE 'REPAID' END
FROM gen_numbers WHERE n < 250;

-- UK overdraft
INSERT INTO borrowing (uuid, facility_ref, company_ref, drawdown_date, repayment_date,
                       principal_amount, currency_code, all_in_rate, status)
SELECT
    gen_uuid('BRW_OD_'||CAST(n+1 AS VARCHAR)), 'CF_OD_GBP_50M','GR_GB',
    DATEADD(day, MOD(ABS(FNV_HASH('OD_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01'),
    DATEADD(day, MOD(ABS(FNV_HASH('OD_TEN|'||CAST(n AS VARCHAR))),6)+2,
            DATEADD(day, MOD(ABS(FNV_HASH('OD_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')),
    2000000 + MOD(ABS(FNV_HASH('OD_AMT|'||CAST(n AS VARCHAR))),13000001),
    'GBP',
    (SELECT rate FROM t_bmk_on_date
      WHERE benchmark_code='SONIA'
        AND rate_date = DATEADD(day, MOD(ABS(FNV_HASH('OD_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')) + 2.25,
    'REPAID'
FROM gen_numbers WHERE n < 80;

-- IC loans
INSERT INTO borrowing (uuid, facility_ref, company_ref, drawdown_date, repayment_date,
                       principal_amount, currency_code, all_in_rate, status)
SELECT
    gen_uuid('BRW_IC_'||cf.code||'_'||CAST(c.calendar_date AS VARCHAR)),
    cf.code, cf.company_ref, c.calendar_date, DATEADD(month,1,c.calendar_date),
    5000000 + MOD(ABS(FNV_HASH(cf.code||'|'||CAST(c.calendar_date AS VARCHAR))),25000001),
    cf.currency_ref, 8.30, 'REPAID'
FROM credit_facility cf
JOIN gen_calendar c ON EXTRACT(DAY FROM c.calendar_date)=1 AND c.is_weekday
WHERE cf.facility_type='INTERCOMPANY';

-- -----------------------------------------------------------------------------
-- 7.5 INTEREST_ACCRUAL
-- -----------------------------------------------------------------------------
TRUNCATE TABLE interest_accrual;

INSERT INTO interest_accrual (uuid, accrual_date, source_type, source_uuid, company_ref,
                              amount, currency_code, direction)
SELECT gen_uuid('INTACC_BRW_'||b.uuid||'_'||CAST(c.calendar_date AS VARCHAR)),
       c.calendar_date, 'BORROWING', b.uuid, b.company_ref,
       ROUND((b.principal_amount * b.all_in_rate / 100.0 / 365.0)::NUMERIC, 2),
       b.currency_code, 'EXPENSE'
FROM borrowing b
JOIN gen_calendar c ON c.calendar_date BETWEEN b.drawdown_date AND b.repayment_date
WHERE b.principal_amount > 0;

INSERT INTO interest_accrual (uuid, accrual_date, source_type, source_uuid, company_ref,
                              amount, currency_code, direction)
SELECT gen_uuid('INTACC_INV_'||p.instrument_ref||'_'||CAST(p.as_of_date AS VARCHAR)),
       p.as_of_date, 'INVESTMENT_POSITION', p.uuid, p.company_ref,
       ROUND((p.face_amount * COALESCE(p.yield_to_maturity,4.5) / 100.0 * 7.0/365.0)::NUMERIC, 2),
       p.currency_code, 'INCOME'
FROM investment_position p WHERE COALESCE(p.yield_to_maturity, 0) > 0;

INSERT INTO interest_accrual (uuid, accrual_date, source_type, source_uuid, company_ref,
                              amount, currency_code, direction)
SELECT gen_uuid('INTACC_CFEE_'||cf.code||'_'||CAST(c.calendar_date AS VARCHAR)),
       c.calendar_date,'CREDIT_FACILITY_FEE',cf.uuid,cf.company_ref,
       ROUND(((cf.commitment_amount - COALESCE(b.outstanding,0))
              * COALESCE(cf.commitment_fee_bps,0) / 10000.0 / 12.0)::NUMERIC, 2),
       cf.currency_ref, 'EXPENSE'
FROM credit_facility cf
JOIN gen_calendar c ON c.calendar_date = LAST_DAY(c.calendar_date)
LEFT JOIN ( SELECT facility_ref, SUM(principal_amount) AS outstanding
            FROM borrowing WHERE status='OUTSTANDING' GROUP BY facility_ref ) b
       ON b.facility_ref = cf.code
WHERE cf.commitment_fee_bps IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 7.6 Cash flow legs
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT gen_uuid('CF_LOAN_DR_'||b.uuid),
       'IHB_'||b.currency_code||'_DISBURSEMENT', 'LOAN_DRAW','BC_TREAS','CONFIRMED',
       b.drawdown_date, b.drawdown_date,
       b.principal_amount, b.currency_code, b.principal_amount,
       b.principal_amount, b.currency_code, 1.0,
       cf.lender_bank_ref, 'Loan drawdown', b.uuid, 'WIRE'
FROM borrowing b
JOIN credit_facility cf ON cf.code = b.facility_ref
WHERE b.principal_amount > 0
UNION ALL
SELECT gen_uuid('CF_LOAN_RP_'||b.uuid),
       'IHB_'||b.currency_code||'_DISBURSEMENT', 'LOAN_REPAY','BC_TREAS','CONFIRMED',
       b.repayment_date, b.repayment_date,
       -b.principal_amount, b.currency_code, -b.principal_amount,
       -b.principal_amount, b.currency_code, 1.0,
       cf.lender_bank_ref, 'Loan repayment', b.uuid, 'WIRE'
FROM borrowing b
JOIN credit_facility cf ON cf.code = b.facility_ref
WHERE b.status='REPAID';

INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT gen_uuid('CF_INV_'||t.uuid),
       t.bank_account_ref,
       CASE t.transaction_type WHEN 'PURCHASE' THEN 'INV_PURCHASE'
                               WHEN 'MATURITY' THEN 'INV_MATURITY'
                               ELSE 'INV_INTEREST' END,
       'BC_TREAS','CONFIRMED', t.trade_date, t.settle_date,
       t.amount, t.currency_code, t.amount,
       t.amount, t.currency_code, 1.0,
       i.issuer_name, 'Investment '||t.transaction_type||' '||t.instrument_ref,
       t.uuid, 'WIRE'
FROM investment_transaction t
JOIN investment_instrument i ON i.code = t.instrument_ref
WHERE t.bank_account_ref IS NOT NULL;

-- Monthly LOAN_INTEREST settlements
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT gen_uuid('CF_LOANINT_'||ia.source_uuid||'_'||TO_CHAR(DATE_TRUNC('month',ia.accrual_date),'YYYYMM')),
       'IHB_'||ia.currency_code||'_DISBURSEMENT',
       'LOAN_INTEREST','BC_TREAS','CONFIRMED',
       LAST_DAY(ia.accrual_date), LAST_DAY(ia.accrual_date),
       -ROUND(SUM(ia.amount),2), ia.currency_code, -ROUND(SUM(ia.amount),2),
       -ROUND(SUM(ia.amount),2), ia.currency_code, 1.0,
       'Lender Bank', 'Monthly interest settlement',
       ia.source_uuid||'-'||TO_CHAR(LAST_DAY(ia.accrual_date),'YYYYMM'), 'WIRE'
FROM interest_accrual ia
WHERE ia.source_type='BORROWING' AND ia.direction='EXPENSE'
GROUP BY ia.source_uuid, ia.currency_code, DATE_TRUNC('month',ia.accrual_date), LAST_DAY(ia.accrual_date);

-- VERIFY
SELECT 'INVESTMENT_TRANSACTION' AS t, COUNT(*) FROM investment_transaction
UNION ALL SELECT 'INVESTMENT_POSITION', COUNT(*) FROM investment_position
UNION ALL SELECT 'BORROWING', COUNT(*) FROM borrowing
UNION ALL SELECT 'INTEREST_ACCRUAL', COUNT(*) FROM interest_accrual;
