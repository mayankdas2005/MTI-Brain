-- =============================================================================
-- 04_cash_flows.sql (Redshift) — operational CASH_FLOW.
-- Balances rebuilt later in 10_recompute_balances.sql.
-- Notes:
--   • SCALE_FACTOR read inline via (SELECT value::FLOAT FROM gen_control ...)
--   • WEEKISO → EXTRACT(WEEK FROM d) (Redshift ISO week)
-- =============================================================================
SET search_path TO lpp;

TRUNCATE TABLE cash_flow;

-- -----------------------------------------------------------------------------
-- 4.1 Per-account POS baseline
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS t_pos_baseline;
CREATE TEMP TABLE t_pos_baseline AS
SELECT
    ba.code AS account_code,
    ba.currency_ref AS ccy,
    ba.company_ref,
    cr.region,
    CASE ba.currency_ref
      WHEN 'USD' THEN 22000  WHEN 'GBP' THEN 18000  WHEN 'EUR' THEN 15000
      WHEN 'JPY' THEN 1800000 WHEN 'MXN' THEN 90000 WHEN 'BRL' THEN 75000
      WHEN 'AUD' THEN 24000  WHEN 'CAD' THEN 20000  WHEN 'HKD' THEN 95000
      WHEN 'KRW' THEN 18000000 WHEN 'AED' THEN 60000 WHEN 'SGD' THEN 18000
      WHEN 'PLN' THEN 50000  WHEN 'SEK' THEN 130000 WHEN 'CLP' THEN 12000000
      ELSE 15000
    END AS daily_baseline,
    ba.opening_date, ba.closing_date, ba.closed_account
FROM bank_account ba
JOIN gen_company_region cr ON cr.company_code = ba.company_ref
WHERE ba.account_purpose IN ('OPERATING','COLLECTION');

-- -----------------------------------------------------------------------------
-- 4.2 POS receipts
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date, update_date_time,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_POS_'||src.account_code||'_'||CAST(src.calendar_date AS VARCHAR)),
    src.account_code, 'RCPT_POS','BC_OPS','CONFIRMED',
    src.calendar_date, add_business_days(src.calendar_date, 2),
    src.calendar_date::TIMESTAMPTZ,
    src.amt, src.ccy, src.amt, src.amt, src.ccy, 1.0,
    'RETAIL_CONSUMER','RETAIL_CONSUMER',
    'Daily POS aggregated receipts (gross of interchange)',
    'POS-'||src.account_code||'-'||TO_CHAR(src.calendar_date,'YYYYMMDD'),
    'OTHER'
FROM (
    SELECT p.account_code, p.ccy, p.closed_account, p.closing_date,
           c.calendar_date,
           ROUND((p.daily_baseline
                 * region_seasonality(c.calendar_date, p.region)
                 * yoy_growth(c.calendar_date)
                 * (1 + 0.18 * gen_normal('POS_'||p.account_code||'_'||CAST(c.calendar_date AS VARCHAR)))
                 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC
                , CASE WHEN p.ccy IN ('JPY','KRW','CLP') THEN 0 ELSE 2 END) AS amt
    FROM t_pos_baseline p
    JOIN gen_calendar c ON c.is_weekday AND NOT c.is_global_holiday
    WHERE NOT (p.closed_account AND c.calendar_date > p.closing_date)
) src
WHERE src.amt > 0;

-- -----------------------------------------------------------------------------
-- 4.3 Card interchange debit
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT gen_uuid('CF_INT_'||cf.uuid),
       cf.account_ref, 'CARD_INTERCHANGE','BC_OPS','CONFIRMED',
       cf.value_date, cf.value_date,
       -ROUND(cf.flow_amount * 0.022, 2), cf.flow_currency, -ROUND(cf.flow_amount * 0.022, 2),
       -ROUND(cf.flow_amount * 0.022, 2), cf.flow_currency, 1.0,
       'PSP Adyen', 'TP_PSP_ADYEN', 'Card interchange & processing fee',
       cf.uuid, 'BOOK'
FROM cash_flow cf WHERE cf.flow_code_ref='RCPT_POS';

-- -----------------------------------------------------------------------------
-- 4.4 Card chargebacks (~1%)
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT gen_uuid('CF_CB_'||cf.uuid),
       cf.account_ref, 'CARD_CHARGEBACK','BC_OPS','CONFIRMED',
       DATEADD(day, 7, cf.value_date), DATEADD(day, 7, cf.value_date),
       -ROUND(cf.flow_amount * (0.01 + 0.04 * gen_rand('CB_'||cf.uuid)), 2),
       cf.flow_currency,
       -ROUND(cf.flow_amount * (0.01 + 0.04 * gen_rand('CB_'||cf.uuid)), 2),
       -ROUND(cf.flow_amount * (0.01 + 0.04 * gen_rand('CB_'||cf.uuid)), 2),
       cf.flow_currency, 1.0,
       'PSP Chargeback','TP_PSP_ADYEN','Card chargeback / refund',
       cf.uuid, 'BOOK'
FROM cash_flow cf
WHERE cf.flow_code_ref='RCPT_POS'
  AND gen_rand('CB_FLAG_'||cf.uuid) < 0.01;

-- -----------------------------------------------------------------------------
-- 4.5 E-commerce settlements
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_ECOM_'||src.code||'_'||CAST(src.calendar_date AS VARCHAR)),
    src.code, 'RCPT_ECOM','BC_OPS','CONFIRMED',
    src.calendar_date, add_business_days(src.calendar_date,2),
    src.amt, src.currency_ref, src.amt, src.amt, src.currency_ref, 1.0,
    'PSP_AGGREGATOR','TP_PSP_STRIPE','E-commerce PSP settlement',
    'ECOM-'||src.code||'-'||TO_CHAR(src.calendar_date,'YYYYMMDD'),
    'ACH'
FROM (
    SELECT ba.code, ba.currency_ref, c.calendar_date,
           ROUND((50000
                 * region_seasonality(c.calendar_date, cr.region)
                 * yoy_growth(c.calendar_date)
                 * (1 + 0.20 * gen_normal('ECOM_'||ba.code||'_'||CAST(c.calendar_date AS VARCHAR)))
                 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt
    FROM bank_account ba
    JOIN gen_company_region cr ON cr.company_code = ba.company_ref
    JOIN gen_calendar c ON c.is_weekday AND NOT c.is_global_holiday
    WHERE ba.account_purpose='COLLECTION' AND NOT ba.closed_account
) src;

-- -----------------------------------------------------------------------------
-- 4.7 Payroll
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_PAY_'||src.code||'_'||CAST(src.calendar_date AS VARCHAR)),
    src.code, 'DISB_PAYROLL','BC_OPS','CONFIRMED',
    src.calendar_date, src.calendar_date,
    -src.amt, src.currency_ref, -src.amt, -src.amt, src.currency_ref, 1.0,
    'Payroll Provider','TP_IT_0001','Payroll run',
    'PAY-'||src.code||'-'||TO_CHAR(src.calendar_date,'YYYYMMDD'),
    'ACH'
FROM (
    SELECT ba.code, ba.currency_ref, ba.closed_account, ba.closing_date,
           c.calendar_date,
           ROUND((CASE ba.currency_ref
                   WHEN 'USD' THEN 350000 WHEN 'GBP' THEN 280000 WHEN 'EUR' THEN 250000
                   WHEN 'JPY' THEN 35000000 WHEN 'MXN' THEN 1500000 WHEN 'BRL' THEN 900000
                   WHEN 'AUD' THEN 380000 ELSE 200000
                 END
                 * yoy_growth(c.calendar_date)
                 * (1 + 0.10 * gen_normal('PAY_'||ba.code||'_'||CAST(c.calendar_date AS VARCHAR)))
                 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt
    FROM bank_account ba
    -- LINT FIX (#6/Redshift): correlated scalar subquery in JOIN ON was unsupported. Pre-join v_last_bd_of_month onto gen_calendar via LEFT JOIN, then use the resulting flag in the predicate.
    JOIN (
        SELECT c.*, (lbm.calendar_date IS NOT NULL) AS is_last_bd_of_month
        FROM gen_calendar c
        LEFT JOIN v_last_bd_of_month lbm ON lbm.calendar_date = c.calendar_date
    ) c ON
        ((ba.currency_ref='USD' AND EXTRACT(DOW FROM c.calendar_date)=5 AND MOD(EXTRACT(WEEK FROM c.calendar_date)::INT,2)=0)
         OR (ba.currency_ref<>'USD' AND c.is_weekday AND c.is_last_bd_of_month))
    WHERE ba.account_purpose='PAYROLL' AND NOT ba.closed_account
) src
WHERE NOT (src.closed_account AND src.calendar_date > src.closing_date);

-- -----------------------------------------------------------------------------
-- 4.8 Rent — monthly 5th
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_RENT_'||src.code||'_'||CAST(src.calendar_date AS VARCHAR)),
    src.code, 'DISB_RENT','BC_OPS','CONFIRMED',
    src.calendar_date, src.calendar_date,
    -src.amt, src.currency_ref, -src.amt, -src.amt, src.currency_ref, 1.0,
    'Landlord Holdings',
    'TP_LAND_'||LPAD(CAST((MOD(ABS(FNV_HASH(src.code)), 40)+1) AS VARCHAR),4,'0'),
    'Monthly rent',
    'RENT-'||src.code||'-'||TO_CHAR(src.calendar_date,'YYYYMM'),
    CASE WHEN src.currency_ref='USD' THEN 'ACH'
         WHEN src.currency_ref IN ('EUR','GBP') THEN 'SEPA_CT' ELSE 'WIRE' END
FROM (
    SELECT ba.code, ba.currency_ref, ba.closed_account, ba.closing_date,
           c.calendar_date,
           ROUND((CASE ba.currency_ref
                   WHEN 'USD' THEN 120000 WHEN 'GBP' THEN 95000 WHEN 'EUR' THEN 85000
                   WHEN 'JPY' THEN 12000000 WHEN 'MXN' THEN 600000 WHEN 'AUD' THEN 130000
                   ELSE 60000
                 END
                 * ap_inflation(c.calendar_date)
                 * (1 + 0.05 * gen_normal('RENT_'||ba.code||'_'||CAST(c.calendar_date AS VARCHAR)))
                 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt
    FROM bank_account ba
    JOIN gen_calendar c ON EXTRACT(DAY FROM c.calendar_date)=5 AND c.is_weekday
    WHERE ba.account_purpose='OPERATING' AND NOT ba.closed_account
) src;

-- -----------------------------------------------------------------------------
-- 4.9 Tax payments
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_TAX_'||src.code||'_'||CAST(src.calendar_date AS VARCHAR)),
    src.code, 'DISB_TAX','BC_TAX','CONFIRMED',
    src.calendar_date, src.calendar_date,
    -src.amt, src.currency_ref, -src.amt, -src.amt, src.currency_ref, 1.0,
    'Tax Authority',
    CASE src.currency_ref WHEN 'USD' THEN 'TP_TAX_IRS' WHEN 'GBP' THEN 'TP_TAX_HMRC'
         WHEN 'EUR' THEN 'TP_TAX_BMF' WHEN 'JPY' THEN 'TP_TAX_NTA'
         WHEN 'MXN' THEN 'TP_TAX_SAT' WHEN 'BRL' THEN 'TP_TAX_RFB' ELSE 'TP_TAX_HMRC' END,
    'Tax payment',
    'TAX-'||src.code||'-'||TO_CHAR(src.calendar_date,'YYYYMM'),
    'WIRE'
FROM (
    SELECT ba.code, ba.currency_ref, ba.closed_account, ba.closing_date,
           c.calendar_date,
           ROUND((CASE ba.currency_ref
                   WHEN 'USD' THEN 850000 WHEN 'GBP' THEN 600000 WHEN 'EUR' THEN 550000
                   WHEN 'JPY' THEN 80000000 WHEN 'MXN' THEN 4000000 WHEN 'AUD' THEN 700000
                   ELSE 250000
                 END
                 * yoy_growth(c.calendar_date)
                 * (1 + 0.08 * gen_normal('TAX_'||ba.code||'_'||CAST(c.calendar_date AS VARCHAR)))
                 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR')
                 * CASE WHEN EXTRACT(MONTH FROM c.calendar_date) IN (1,4,7,10) THEN 1.0 ELSE 0.4 END
               )::NUMERIC, 2) AS amt
    FROM bank_account ba
    JOIN gen_calendar c ON EXTRACT(DAY FROM c.calendar_date)=15 AND c.is_weekday
    WHERE ba.account_purpose='TAX' AND NOT ba.closed_account
) src;

-- -----------------------------------------------------------------------------
-- 4.10 Utilities / IT (3/month per opco operating)
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_UTIL_'||src.account_code||'_'||CAST(src.calendar_date AS VARCHAR)||'_'||CAST(src.sub_seq AS VARCHAR)),
    src.account_code, 'DISB_UTIL','BC_OPS','CONFIRMED',
    src.calendar_date, src.calendar_date,
    -src.amt, src.ccy, -src.amt, -src.amt, src.ccy, 1.0,
    'Utility/IT',
    'TP_UTIL_'||LPAD(CAST((MOD(ABS(FNV_HASH(src.account_code||'|'||CAST(src.sub_seq AS VARCHAR))), 30)+1) AS VARCHAR),4,'0'),
    'Utility / IT subscription',
    'UTIL-'||src.account_code||'-'||TO_CHAR(src.calendar_date,'YYYYMM')||'-'||CAST(src.sub_seq AS VARCHAR),
    'SEPA_DD'
FROM (
    SELECT ba.code AS account_code, ba.currency_ref AS ccy,
           c.calendar_date, g.seq AS sub_seq,
           ROUND((CASE ba.currency_ref
                   WHEN 'USD' THEN 8000 WHEN 'GBP' THEN 6500 WHEN 'EUR' THEN 6000
                   WHEN 'JPY' THEN 800000 WHEN 'MXN' THEN 30000 ELSE 4000
                 END
                 * ap_inflation(c.calendar_date)
                 * (1 + 0.15 * gen_normal('UTIL_'||ba.code||'_'||CAST(c.calendar_date AS VARCHAR)||'_'||CAST(g.seq AS VARCHAR)))
                 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt
    FROM bank_account ba
    JOIN gen_calendar c ON EXTRACT(DAY FROM c.calendar_date) = (10 + (MOD(ABS(FNV_HASH(ba.code)), 15)))
                       AND c.is_weekday
    JOIN ( SELECT n+1 AS seq FROM gen_numbers WHERE n < 3 ) g ON TRUE
    WHERE ba.account_purpose='OPERATING' AND NOT ba.closed_account
) src;

-- -----------------------------------------------------------------------------
-- 4.11 CAPEX disbursements
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
WITH capex_events AS (
    SELECT
        g.n+1 AS seq,
        ba.code  AS account_code,
        ba.currency_ref AS ccy,
        DATEADD(day, MOD(ABS(FNV_HASH(ba.code||'|'||CAST(g.n AS VARCHAR))),
                          DATEDIFF(day, DATE '2023-05-01', DATE '2026-05-04')+1),
                DATE '2023-05-01')::DATE AS dt,
        ba.company_ref
    FROM bank_account ba
    JOIN gen_numbers g ON g.n < 36
    WHERE (ba.account_purpose IN ('OPERATING','DISBURSEMENT') AND ba.code LIKE '%RGNL_%')
       OR ba.company_ref='GR_HOLDINGS'
)
SELECT
    gen_uuid('CF_CAPEX_'||account_code||'_'||CAST(seq AS VARCHAR)),
    account_code, 'DISB_CAPEX','BC_CAPEX','CONFIRMED',
    dt, add_business_days(dt,2),
    -ROUND((EXP(13 + 0.7 * gen_normal('CAPEX_'||account_code||'_'||CAST(seq AS VARCHAR)))
           * (CASE WHEN company_ref='GR_APAC_PTE' AND dt BETWEEN DATE '2024-07-01' AND DATE '2024-12-31'
                    THEN 1.12 ELSE 1.0 END)
           * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
    ccy,
    -ROUND((EXP(13 + 0.7 * gen_normal('CAPEX_'||account_code||'_'||CAST(seq AS VARCHAR)))
           * (CASE WHEN company_ref='GR_APAC_PTE' AND dt BETWEEN DATE '2024-07-01' AND DATE '2024-12-31' THEN 1.12 ELSE 1.0 END)
           * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
    NULL, ccy, 1.0,
    'CAPEX Vendor',
    'TP_CAPEX_'||LPAD(CAST((MOD(ABS(FNV_HASH(account_code||'|'||CAST(seq AS VARCHAR))), 5)+1) AS VARCHAR),4,'0'),
    'Capital expenditure milestone',
    'CAPEX-'||account_code||'-'||TO_CHAR(dt,'YYYYMMDD')||'-'||CAST(seq AS VARCHAR),
    'WIRE'
FROM capex_events;

-- -----------------------------------------------------------------------------
-- 4.12 BEC fraud incident (scripted)
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
) VALUES
(gen_uuid('CF_BEC_OUT'),'IHB_USD_DISBURSEMENT','DISB_AP_WIRE','BC_OPS','CONFIRMED',
 DATE '2024-09-14', DATE '2024-09-14',-480000.00,'USD',-480000.00,NULL,'USD',1.0,
 'Fraudulent Vendor (HK)','Suspected BEC wire — under investigation','BEC-2024-09-14','WIRE'),
(gen_uuid('CF_BEC_REV'),'IHB_USD_DISBURSEMENT','FRAUD_REVERSAL','BC_OPS','CONFIRMED',
 DATE '2024-10-12', DATE '2024-10-12', 312000.00,'USD', 312000.00,NULL,'USD',1.0,
 'Recovered Funds','BEC partial recovery (65%)','BEC-2024-09-14-REC','WIRE');

-- VERIFY
SELECT 'CASH_FLOW after 04' AS t, COUNT(*) FROM cash_flow;
SELECT flow_code_ref, COUNT(*) AS n FROM cash_flow GROUP BY flow_code_ref ORDER BY n DESC;
