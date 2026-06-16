-- =============================================================================
-- 09_fees_forecasts.sql (Redshift) — BANK_FEE, FORECAST_SNAPSHOT, FORECAST_CASH_FLOW,
-- FORECAST_VS_ACTUAL, STRESS_RUN_RESULT, FRAUD_DETECTION_EVENT
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 9.1 BANK_FEE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE bank_fee;

DROP TABLE IF EXISTS t_fee_units;
CREATE TEMP TABLE t_fee_units AS
SELECT ba.code AS account_code,
       br.bank_ref AS bank_ref,
       LAST_DAY(cf.transaction_date) AS month_end,
       cf.payment_rail,
       COUNT(*) AS units
FROM cash_flow cf
JOIN bank_account ba ON ba.code = cf.account_ref
JOIN bank_branch br ON br.code = ba.branch_ref
WHERE cf.transaction_date IS NOT NULL
GROUP BY ba.code, br.bank_ref, LAST_DAY(cf.transaction_date), cf.payment_rail;

DROP TABLE IF EXISTS t_rail_service;
CREATE TEMP TABLE t_rail_service AS
SELECT * FROM (
    SELECT 'WIRE' AS payment_rail,'251' AS service_code UNION ALL
    SELECT 'ACH','260' UNION ALL SELECT 'SEPA_CT','270' UNION ALL SELECT 'SEPA_DD','271' UNION ALL
    SELECT 'RTP','300' UNION ALL SELECT 'FEDNOW','300' UNION ALL SELECT 'CHECK','800' UNION ALL
    SELECT 'BOOK','100' UNION ALL SELECT 'OTHER','100'
) t;

-- Per-transaction fees
INSERT INTO bank_fee (
    uuid, bank_ref, bank_account_ref, service_code,
    statement_period, charge_date, units, charged_amount,
    currency_code, rate_card_ref, expected_amount, overage_amount, flagged
)
SELECT
    gen_uuid('FEE_'||u.account_code||'_'||CAST(u.month_end AS VARCHAR)||'_'||u.payment_rail),
    u.bank_ref, u.account_code, rs.service_code,
    u.month_end, add_business_days(u.month_end, 2), u.units,
    ROUND((u.units * rc.negotiated_rate
        * (CASE WHEN u.bank_ref='BANK_DB' AND u.month_end BETWEEN DATE '2024-07-01' AND DATE '2024-12-31'
                THEN 1.30
                WHEN gen_rand('FEE_'||u.account_code||'_'||CAST(u.month_end AS VARCHAR))<0.04 THEN 1.05
                WHEN gen_rand('FEE_'||u.account_code||'_'||CAST(u.month_end AS VARCHAR))<0.05 THEN 1.30
                ELSE 1.0 END))::NUMERIC, 2),
    'USD', rc.uuid,
    ROUND((u.units * rc.negotiated_rate)::NUMERIC, 2),
    ROUND((u.units * rc.negotiated_rate
        * (CASE WHEN u.bank_ref='BANK_DB' AND u.month_end BETWEEN DATE '2024-07-01' AND DATE '2024-12-31' THEN 0.30
                WHEN gen_rand('FEE_'||u.account_code||'_'||CAST(u.month_end AS VARCHAR))<0.04 THEN 0.05
                WHEN gen_rand('FEE_'||u.account_code||'_'||CAST(u.month_end AS VARCHAR))<0.05 THEN 0.30
                ELSE 0 END))::NUMERIC, 2),
    CASE WHEN u.bank_ref='BANK_DB' AND u.month_end BETWEEN DATE '2024-07-01' AND DATE '2024-12-31' THEN TRUE
         WHEN gen_rand('FEE_'||u.account_code||'_'||CAST(u.month_end AS VARCHAR))<0.05 THEN TRUE
         ELSE FALSE END
FROM t_fee_units u
JOIN t_rail_service rs ON rs.payment_rail = u.payment_rail
JOIN fee_rate_card rc ON rc.bank_ref = u.bank_ref AND rc.service_code = rs.service_code
                     AND u.month_end BETWEEN rc.effective_from AND COALESCE(rc.effective_to, DATE '2099-12-31');

-- Monthly maintenance
INSERT INTO bank_fee (
    uuid, bank_ref, bank_account_ref, service_code,
    statement_period, charge_date, units, charged_amount,
    currency_code, rate_card_ref, expected_amount, overage_amount, flagged
)
SELECT
    gen_uuid('FEE_MAINT_'||ba.code||'_'||CAST(lbd.calendar_date AS VARCHAR)),
    br.bank_ref, ba.code, '100', lbd.calendar_date, add_business_days(lbd.calendar_date,2),
    1, rc.negotiated_rate, 'USD', rc.uuid, rc.negotiated_rate, 0, FALSE
FROM bank_account ba
JOIN bank_branch br ON br.code = ba.branch_ref
JOIN v_last_bd_of_month lbd ON TRUE
JOIN fee_rate_card rc ON rc.bank_ref = br.bank_ref AND rc.service_code='100'
                     AND lbd.calendar_date BETWEEN rc.effective_from AND COALESCE(rc.effective_to, DATE '2099-12-31')
WHERE NOT ba.closed_account OR lbd.calendar_date < ba.closing_date;

-- Bank fee cash flow legs
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT gen_uuid('CF_FEE_'||bf.uuid), bf.bank_account_ref,
       'BANK_FEE','BC_TREAS','CONFIRMED',
       bf.charge_date, bf.charge_date,
       -bf.charged_amount, bf.currency_code, -bf.charged_amount,
       -bf.charged_amount, bf.currency_code, 1.0,
       bf.bank_ref, 'Bank fee '||bf.service_code, bf.uuid, 'BOOK'
FROM bank_fee bf;

-- Back-link cash_flow_ref
UPDATE bank_fee
SET cash_flow_ref = cf_lookup.uuid
FROM (SELECT reference, MIN(uuid) AS uuid FROM cash_flow GROUP BY reference) cf_lookup
WHERE cf_lookup.reference = bank_fee.uuid;

-- -----------------------------------------------------------------------------
-- 9.2 FORECAST_SNAPSHOT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE forecast_snapshot;
INSERT INTO forecast_snapshot (snapshot_id, snapshot_date, horizon_start_date, horizon_end_date, granularity, model_version, description)
SELECT
    'FC_'||TO_CHAR(c.calendar_date,'YYYYMMDD')||'_M' AS snapshot_id,
    c.calendar_date,
    DATEADD(day,1,c.calendar_date),
    DATEADD(day,91,c.calendar_date),
    'DAILY','v1.3','Monthly 13-week direct forecast'
FROM gen_calendar c
WHERE EXTRACT(DAY FROM c.calendar_date)=1 AND c.is_weekday
UNION ALL
SELECT
    'FC_'||TO_CHAR(c.calendar_date,'YYYYMMDD')||'_Q',
    c.calendar_date,
    DATEADD(day,1,c.calendar_date),
    DATEADD(month,18,c.calendar_date),
    'MONTHLY','v1.3','Quarterly 18-month indirect forecast'
FROM gen_calendar c
WHERE c.calendar_date IN (DATE '2023-07-01', DATE '2023-10-01', DATE '2024-01-01', DATE '2024-04-01', DATE '2024-07-01', DATE '2024-10-01',
                          DATE '2025-01-01', DATE '2025-04-01', DATE '2025-07-01', DATE '2025-10-01', DATE '2026-01-01', DATE '2026-04-01');

-- -----------------------------------------------------------------------------
-- 9.3 FORECAST_CASH_FLOW
-- -----------------------------------------------------------------------------
TRUNCATE TABLE forecast_cash_flow;

INSERT INTO forecast_cash_flow (
    uuid, snapshot_id, company_ref, account_ref,
    forecast_date, flow_category, flow_subcategory, direction,
    forecast_amount, currency_code, confidence, seasonality_factor
)
WITH cats AS (
    SELECT 'AR_COLLECTION' AS flow_category,'IN' AS dir,'HIGH' AS conf UNION ALL
    SELECT 'AP_DISBURSEMENT','OUT','MEDIUM' UNION ALL
    SELECT 'PAYROLL','OUT','HIGH' UNION ALL
    SELECT 'TAX','OUT','HIGH' UNION ALL
    SELECT 'CAPEX','OUT','LOW' UNION ALL
    SELECT 'DEBT_SERVICE','OUT','HIGH' UNION ALL
    SELECT 'INTERCOMPANY','BOTH','MEDIUM'
)
SELECT
    gen_uuid('FC_'||fs.snapshot_id||'_'||co.code||'_'||cats.flow_category),
    fs.snapshot_id, co.code, NULL,
    DATEADD(month,1,fs.snapshot_date),
    cats.flow_category, NULL, cats.dir,
    ROUND((EXP(13 + 0.6 * gen_normal(fs.snapshot_id||co.code||cats.flow_category))
           * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
    CASE co.country WHEN 'US' THEN 'USD' WHEN 'GB' THEN 'GBP' WHEN 'JP' THEN 'JPY' ELSE 'USD' END,
    cats.conf,
    CASE WHEN EXTRACT(MONTH FROM DATEADD(month,1,fs.snapshot_date)) IN (11,12) THEN 1.6
         WHEN EXTRACT(MONTH FROM DATEADD(month,1,fs.snapshot_date)) IN (1,2)   THEN 0.7
         ELSE 1.0 END
FROM forecast_snapshot fs
CROSS JOIN company co
CROSS JOIN cats
WHERE co.code IN ('GR_HOLDINGS','GR_TREASURY','GR_US_INC','GR_EU_BV','GR_APAC_PTE','GR_LATAM_SA','GR_GB','GR_DE','GR_FR','GR_JP');

-- -----------------------------------------------------------------------------
-- 9.4 FORECAST_VS_ACTUAL
-- -----------------------------------------------------------------------------
TRUNCATE TABLE forecast_vs_actual;

INSERT INTO forecast_vs_actual (
    company_ref, period_date, flow_category,
    forecast_amount, actual_amount, variance_amount, variance_pct,
    snapshot_id, currency_code
)
WITH fc_agg AS (
    SELECT fc.company_ref, fc.flow_category,
           LAST_DAY(fc.forecast_date) AS pd,
           fc.snapshot_id,
           SUM(fc.forecast_amount) AS f_amt
    FROM forecast_cash_flow fc
    GROUP BY fc.company_ref, fc.flow_category, LAST_DAY(fc.forecast_date), fc.snapshot_id
)
SELECT
    co.code,
    LAST_DAY(c.calendar_date),
    cats.flow_category,
    COALESCE(fa.f_amt,
             ROUND((EXP(13 + 0.6 * gen_normal('FVA_F_'||co.code||CAST(c.calendar_date AS VARCHAR)||cats.flow_category)))::NUMERIC,2)),
    ROUND((COALESCE(fa.f_amt,
                   EXP(13 + 0.6 * gen_normal('FVA_F_'||co.code||CAST(c.calendar_date AS VARCHAR)||cats.flow_category)))
           * (1 + 0.06 * gen_normal('FVA_VAR_'||co.code||CAST(c.calendar_date AS VARCHAR)||cats.flow_category)
             + CASE WHEN co.code IN ('GR_LATAM_SA','GR_MX','GR_BR') AND c.calendar_date BETWEEN DATE '2024-04-01' AND DATE '2024-09-30'
                     AND cats.flow_category='AR_COLLECTION' THEN -0.22
                    WHEN c.calendar_date= DATE '2025-04-30' AND cats.flow_category='AR_COLLECTION' THEN -0.22
                    WHEN cats.flow_category='CAPEX' AND c.calendar_date BETWEEN DATE '2024-07-01' AND DATE '2024-12-31' THEN 0.12
                    ELSE 0 END))::NUMERIC,2),
    NULL, NULL,
    'FC_'||TO_CHAR(DATE_TRUNC('month',c.calendar_date),'YYYYMMDD')||'_M',
    'USD'
FROM company co
CROSS JOIN gen_calendar c
CROSS JOIN ( SELECT 'AR_COLLECTION' AS flow_category UNION ALL SELECT 'AP_DISBURSEMENT'
             UNION ALL SELECT 'PAYROLL' UNION ALL SELECT 'CAPEX'
             UNION ALL SELECT 'TAX' UNION ALL SELECT 'FX_REVAL' ) cats
LEFT JOIN fc_agg fa ON fa.company_ref = co.code
                     AND fa.flow_category = cats.flow_category
                     AND fa.pd = LAST_DAY(c.calendar_date)
WHERE c.calendar_date = LAST_DAY(c.calendar_date) AND c.is_weekday;

UPDATE forecast_vs_actual SET
    variance_amount = actual_amount - forecast_amount,
    variance_pct    = ROUND((100.0 * (actual_amount - forecast_amount) / NULLIF(forecast_amount,0))::NUMERIC, 4);

-- -----------------------------------------------------------------------------
-- 9.5 STRESS_RUN_RESULT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE stress_run_result;

INSERT INTO stress_run_result (
    uuid, scenario_ref, run_date, company_ref,
    breach_date, min_projected_cash, threshold_amount,
    currency_code, breach_severity
)
SELECT
    gen_uuid('SR_'||s.code||'_'||CAST(c.calendar_date AS VARCHAR)||'_'||co.code),
    s.code,
    c.calendar_date::TIMESTAMPTZ,
    co.code,
    CASE
      WHEN s.code='STR_AR_DROP_30_60D' AND co.code IN ('GR_LATAM_SA','GR_MX','GR_BR')
           AND c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-06-30' THEN DATEADD(day,45,c.calendar_date)
      WHEN s.code='STR_RATE_PLUS_200' AND c.calendar_date BETWEEN DATE '2023-07-01' AND DATE '2023-09-30' THEN DATEADD(day,60,c.calendar_date)
      WHEN gen_rand('SR_'||s.code||co.code||CAST(c.calendar_date AS VARCHAR)) < 0.05 THEN DATEADD(day,30,c.calendar_date)
      ELSE NULL
    END,
    ROUND((EXP(15 + 0.5 * gen_normal('SR_MIN_'||s.code||co.code||CAST(c.calendar_date AS VARCHAR))))::NUMERIC,2),
    400000000, 'USD',
    CASE
      WHEN s.code='STR_AR_DROP_30_60D' AND co.code IN ('GR_LATAM_SA','GR_MX','GR_BR')
           AND c.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-06-30' THEN 'CRITICAL'
      WHEN s.code='STR_RATE_PLUS_200' AND c.calendar_date BETWEEN DATE '2023-07-01' AND DATE '2023-09-30' THEN 'WARNING'
      WHEN gen_rand('SR_'||s.code||co.code||CAST(c.calendar_date AS VARCHAR)) < 0.05 THEN 'WARNING'
      ELSE 'NONE'
    END
FROM stress_scenario s
CROSS JOIN company co
CROSS JOIN gen_calendar c
WHERE c.calendar_date = LAST_DAY(c.calendar_date) AND c.is_weekday
  AND c.calendar_date >= DATEADD(month,-18, DATE '2026-05-04')
  AND co.code IN ('GR_HOLDINGS','GR_TREASURY','GR_US_INC','GR_EU_BV','GR_APAC_PTE','GR_LATAM_SA');

-- -----------------------------------------------------------------------------
-- 9.6 FRAUD_DETECTION_EVENT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE fraud_detection_event;

INSERT INTO fraud_detection_event (uuid, transfer_uuid, file_uuid, decision, score, reason_codes, raised_at)
SELECT
    gen_uuid('FRAUD_'||CAST(n AS VARCHAR)),
    NULL, NULL,
    (CASE MOD(ABS(FNV_HASH('FDEC|'||CAST(n AS VARCHAR))),11)
       WHEN 0 THEN 'ALLOW' WHEN 1 THEN 'ALLOW' WHEN 2 THEN 'ALLOW' WHEN 3 THEN 'ALLOW'
       WHEN 4 THEN 'ALLOW' WHEN 5 THEN 'ALLOW' WHEN 6 THEN 'ALLOW'
       WHEN 7 THEN 'REVIEW' WHEN 8 THEN 'REVIEW' WHEN 9 THEN 'REVIEW' ELSE 'BLOCK' END),
    ROUND((gen_rand(CAST(n AS VARCHAR)) * 100)::NUMERIC, 2),
    -- LINT FIX (#3): replaced string-built JSON_PARSE with native ARRAY() SUPER constructor for NULL/quote safety.
    ARRAY(CASE MOD(ABS(FNV_HASH('FRC|'||CAST(n AS VARCHAR))),6)
       WHEN 0 THEN 'VENDOR_MISMATCH' WHEN 1 THEN 'UNUSUAL_AMOUNT' WHEN 2 THEN 'VELOCITY'
       WHEN 3 THEN 'GEO_MISMATCH' WHEN 4 THEN 'BANK_CHANGE' ELSE 'FIRST_PAYEE' END),
    DATEADD(day, MOD(ABS(FNV_HASH('FDT|'||CAST(n AS VARCHAR))),1101), TIMESTAMP '2023-05-01 00:00:00')::TIMESTAMPTZ
FROM gen_numbers WHERE n < 120;

INSERT INTO fraud_detection_event (uuid, transfer_uuid, file_uuid, decision, score, reason_codes, raised_at)
VALUES (gen_uuid('FRAUD_BEC_2024_09_14'), NULL, NULL, 'BLOCK', 96.50,
        -- LINT FIX (#3): use ARRAY() constructor instead of JSON_PARSE on a literal string.
        ARRAY('VENDOR_MISMATCH','BANK_CHANGE','GEO_MISMATCH','HIGH_VALUE_NEW_BENEFICIARY'),
        TIMESTAMP '2024-09-14 11:42:00'::TIMESTAMPTZ);

-- -----------------------------------------------------------------------------
-- Post-pass cleanup of cash_flow to enforce V10 / V11 invariants
-- -----------------------------------------------------------------------------
-- V10: AR/AP/bank-fee flows whose value_date spills past WINDOW_END must not be
-- CONFIRMED. Generators emit them because invoice due_dates compound from
-- issue_date + terms. Treat as forecast-grade.
UPDATE cash_flow
   SET status = 'FORECAST'
 WHERE status = 'CONFIRMED'
   AND value_date > (SELECT value::DATE FROM gen_control WHERE key='WINDOW_END');

-- V11: never leave booked cash flows on an account after closing_date. A few
-- POS / CARD_INTERCHANGE / BANK_FEE rows slip past because their settlement
-- leg is calendar_date + 2 BD; delete them.
DELETE FROM cash_flow
 USING bank_account ba
 WHERE ba.code = cash_flow.account_ref
   AND ba.closed_account
   AND cash_flow.value_date > ba.closing_date;

-- VERIFY
SELECT 'BANK_FEE' AS t, COUNT(*) FROM bank_fee
UNION ALL SELECT 'BANK_FEE_FLAGGED', COUNT(*) FROM bank_fee WHERE flagged
UNION ALL SELECT 'FORECAST_SNAPSHOT', COUNT(*) FROM forecast_snapshot
UNION ALL SELECT 'FORECAST_CASH_FLOW', COUNT(*) FROM forecast_cash_flow
UNION ALL SELECT 'FORECAST_VS_ACTUAL', COUNT(*) FROM forecast_vs_actual
UNION ALL SELECT 'STRESS_RUN_RESULT', COUNT(*) FROM stress_run_result
UNION ALL SELECT 'STRESS_CRITICAL', COUNT(*) FROM stress_run_result WHERE breach_severity='CRITICAL'
UNION ALL SELECT 'FRAUD_DETECTION_EVENT', COUNT(*) FROM fraud_detection_event;
