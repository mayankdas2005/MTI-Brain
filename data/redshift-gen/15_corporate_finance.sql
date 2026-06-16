-- =============================================================================
-- 15_corporate_finance.sql (Redshift) — Sections N, O, P of extensions #2.
--
-- Populates:
--   N. company_financial_metric, credit_rating, equity_action, capital_allocation_actual
--   O. letter_of_credit
--   P. pension_plan, pension_valuation
--
-- Scripted scenarios:
--   • Top entity (GR_HOLDINGS) consolidated revenue ~$240B by 2025 (Costco-like),
--     FCF $7-10B/yr, debt/EBITDA ~0.5, WACC ~7.5%.
--   • GR_HOLDINGS rated AA- (S&P), Aa3 (Moodys), AA- (Fitch), stable.
--     One upgrade event 2024-Q2 (A+ → AA- on S&P) and one outlook change
--     2025-Q3 (STABLE → POSITIVE on Moodys).
--   • Buyback program ~$2.5B/quarter; one special dividend in 2024-Q4.
--   • 2-3 letters of credit expiring within 90 days of WINDOW_END (2026-05-04).
--   • US pension plan funded 105% in 2024 → ~96% in 2025 (rate-driven OCI hit).
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 15.1 COMPANY_FINANCIAL_METRIC
-- 12 quarters (2023-Q3..2026-Q1) + 3 FY rows per top entity.
-- For brevity, generate detailed metrics for the top 8 entities; smaller
-- subsidiaries get a thinner row set.
-- -----------------------------------------------------------------------------
TRUNCATE TABLE company_financial_metric;

DROP TABLE IF EXISTS t_fin_periods;
CREATE TEMP TABLE t_fin_periods AS
SELECT * FROM (
    SELECT DATE '2023-06-30' AS period_date,'Q' AS period_type, 2023 AS fy, 1 AS q_idx UNION ALL
    SELECT DATE '2023-09-30','Q', 2023, 2 UNION ALL
    SELECT DATE '2023-12-31','Q', 2023, 3 UNION ALL
    SELECT DATE '2024-03-31','Q', 2024, 4 UNION ALL
    SELECT DATE '2024-06-30','Q', 2024, 5 UNION ALL
    SELECT DATE '2024-09-30','Q', 2024, 6 UNION ALL
    SELECT DATE '2024-12-31','Q', 2024, 7 UNION ALL
    SELECT DATE '2025-03-31','Q', 2025, 8 UNION ALL
    SELECT DATE '2025-06-30','Q', 2025, 9 UNION ALL
    SELECT DATE '2025-09-30','Q', 2025, 10 UNION ALL
    SELECT DATE '2025-12-31','Q', 2025, 11 UNION ALL
    SELECT DATE '2026-03-31','Q', 2026, 12 UNION ALL
    SELECT DATE '2023-12-31','FY', 2023, 0 UNION ALL
    SELECT DATE '2024-12-31','FY', 2024, 0 UNION ALL
    SELECT DATE '2025-12-31','FY', 2025, 0 UNION ALL
    SELECT DATE '2025-12-31','TTM',2025, 0 UNION ALL
    SELECT DATE '2026-03-31','TTM',2026, 0
) t;

DROP TABLE IF EXISTS t_fin_entities;
CREATE TEMP TABLE t_fin_entities AS
SELECT * FROM (
    -- Top entity (Costco-like)
    SELECT 'GR_HOLDINGS' AS code, 'GBP' AS ccy, 220000000000::NUMERIC AS rev_fy_base,
           18000000000::NUMERIC AS ebitda_fy_base, 8000000000::NUMERIC AS fcf_fy_base,
           4500000000::NUMERIC AS total_debt_base, 0.075::NUMERIC AS wacc UNION ALL
    SELECT 'GR_US_INC',     'USD', 130000000000, 11000000000, 5200000000, 2500000000, 0.075 UNION ALL
    SELECT 'GR_TREASURY',   'GBP',     500000000,    50000000,   25000000,           0, 0.060 UNION ALL
    SELECT 'GR_EU_BV',      'EUR',  45000000000,  3500000000, 1800000000, 1200000000, 0.072 UNION ALL
    SELECT 'GR_APAC_PTE',   'SGD',  18000000000,  1500000000,  800000000,  600000000, 0.080 UNION ALL
    SELECT 'GR_LATAM_SA',   'MXN',   8000000000,   700000000,  350000000,  300000000, 0.110 UNION ALL
    SELECT 'GR_GB',         'GBP',   8500000000,   700000000,  320000000,  200000000, 0.075 UNION ALL
    SELECT 'GR_DE',         'EUR',   4200000000,   320000000,  150000000,  100000000, 0.073
) t;

INSERT INTO company_financial_metric (
    company_ref, period_date, period_type, reporting_currency,
    revenue, cogs, ebitda, operating_income, interest_income, interest_expense,
    net_income, diluted_eps,
    cash_from_operations, capex, free_cash_flow,
    total_debt, short_term_debt, long_term_debt, total_equity,
    cash_and_equivalents, short_term_investments, net_debt,
    debt_to_ebitda, leverage_ratio, interest_coverage,
    wacc_pct, weighted_avg_cost_of_debt_pct, return_on_capital_pct,
    diluted_shares_outstanding,
    fy_ebitda_guidance_low, fy_ebitda_guidance_high, fy_fcf_target
)
SELECT
    e.code, p.period_date, p.period_type, e.ccy,
    -- Revenue: quarterly = 1/4 FY * yoy growth applied vs. base year; FY/TTM full
    ROUND( e.rev_fy_base
           * POWER(1.065, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END
           * (0.97 + gen_rand('REV_'||e.code||TO_CHAR(p.period_date,'YYYYMMDD'))*0.06)
         , 2),
    ROUND( e.rev_fy_base * 0.87
           * POWER(1.063, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.ebitda_fy_base * POWER(1.07, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.ebitda_fy_base * 0.75 * POWER(1.07, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.total_debt_base * 0.04
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.total_debt_base * 0.045
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.ebitda_fy_base * 0.55 * POWER(1.08, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    CASE WHEN e.code='GR_HOLDINGS'
         THEN ROUND( 16.0 * POWER(1.08, p.fy - 2023)
                     * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 4)
         ELSE NULL END,
    ROUND( e.fcf_fy_base * 1.15 * POWER(1.06, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.fcf_fy_base * 0.40 * POWER(1.10, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    ROUND( e.fcf_fy_base * POWER(1.06, p.fy - 2023)
           * CASE p.period_type WHEN 'Q' THEN 0.25 ELSE 1.0 END, 2),
    e.total_debt_base,
    ROUND( e.total_debt_base * 0.15, 2),
    ROUND( e.total_debt_base * 0.85, 2),
    ROUND( e.rev_fy_base * 0.18, 2),
    ROUND( e.fcf_fy_base * 1.5, 2),
    ROUND( e.fcf_fy_base * 0.8, 2),
    ROUND( e.total_debt_base - e.fcf_fy_base * 1.5, 2),
    ROUND( e.total_debt_base / NULLIF(e.ebitda_fy_base * POWER(1.07, p.fy - 2023),0), 4),
    ROUND( e.total_debt_base / NULLIF(e.ebitda_fy_base,0), 4),
    ROUND( e.ebitda_fy_base / NULLIF(e.total_debt_base * 0.045,0), 4),
    e.wacc, 0.045, 0.18,
    CASE WHEN e.code='GR_HOLDINGS' THEN 445000000 ELSE NULL END,
    CASE WHEN p.period_type='FY' AND p.fy=2025 THEN ROUND(e.ebitda_fy_base * 1.13, 2) ELSE NULL END,
    CASE WHEN p.period_type='FY' AND p.fy=2025 THEN ROUND(e.ebitda_fy_base * 1.20, 2) ELSE NULL END,
    CASE WHEN p.period_type='FY' AND p.fy=2025 THEN ROUND(e.fcf_fy_base * 1.10, 2) ELSE NULL END
FROM t_fin_entities e
CROSS JOIN t_fin_periods p;

-- -----------------------------------------------------------------------------
-- 15.2 CREDIT_RATING
-- -----------------------------------------------------------------------------
TRUNCATE TABLE credit_rating;

INSERT INTO credit_rating (uuid, company_ref, agency, rating_grade, outlook, rating_action, as_of_date, is_current)
SELECT gen_uuid('CR_'||code||'_'||agency||'_'||TO_CHAR(as_of_date,'YYYYMMDD')),
       code, agency, rating_grade, outlook, rating_action, as_of_date, is_current
FROM (
    -- GR_HOLDINGS (top entity) rating history
    SELECT 'GR_HOLDINGS' AS code,'SP'     AS agency,'A+'  AS rating_grade,'POSITIVE' AS outlook,'AFFIRMED' AS rating_action, DATE '2023-09-15' AS as_of_date, FALSE AS is_current UNION ALL
    SELECT 'GR_HOLDINGS','SP','AA-','STABLE','UPGRADED', DATE '2024-04-22', TRUE UNION ALL
    SELECT 'GR_HOLDINGS','MOODYS','A1','STABLE','AFFIRMED', DATE '2023-09-15', FALSE UNION ALL
    SELECT 'GR_HOLDINGS','MOODYS','Aa3','STABLE','UPGRADED', DATE '2024-06-10', FALSE UNION ALL
    SELECT 'GR_HOLDINGS','MOODYS','Aa3','POSITIVE','AFFIRMED', DATE '2025-09-10', TRUE UNION ALL
    SELECT 'GR_HOLDINGS','FITCH','AA-','STABLE','AFFIRMED', DATE '2024-05-01', TRUE UNION ALL
    -- GR_US_INC (US subsidiary) inherits group rating
    SELECT 'GR_US_INC','SP','AA-','STABLE','AFFIRMED', DATE '2024-04-22', TRUE UNION ALL
    SELECT 'GR_US_INC','MOODYS','Aa3','POSITIVE','AFFIRMED', DATE '2025-09-10', TRUE UNION ALL
    -- Smaller entities — A+
    SELECT 'GR_EU_BV','SP','A+','STABLE','AFFIRMED', DATE '2024-05-15', TRUE UNION ALL
    SELECT 'GR_EU_BV','MOODYS','A1','STABLE','AFFIRMED', DATE '2024-05-15', TRUE UNION ALL
    SELECT 'GR_APAC_PTE','SP','A','STABLE','AFFIRMED', DATE '2024-08-01', TRUE UNION ALL
    SELECT 'GR_LATAM_SA','SP','BBB+','STABLE','AFFIRMED', DATE '2024-08-01', TRUE UNION ALL
    SELECT 'GR_TREASURY','SP','AA-','STABLE','AFFIRMED', DATE '2024-04-22', TRUE
) t;

-- -----------------------------------------------------------------------------
-- 15.3 EQUITY_ACTION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE equity_action;

-- Quarterly buybacks 2023-Q3..2026-Q1 — ~$2.5B/quarter on GR_HOLDINGS
INSERT INTO equity_action (
    uuid, company_ref, action_type, action_date, settle_date,
    shares, price_per_share, total_amount, currency_code, dividend_per_share,
    program_name, authorization_remaining
)
SELECT
    gen_uuid('EA_BB_'||TO_CHAR(qd,'YYYYMMDD')),
    'GR_HOLDINGS','BUYBACK', qd, DATEADD(day,3,qd),
    ROUND((amt_M * 1000000.0) / pps, 0), pps, amt_M * 1000000.0, 'USD', NULL,
    'GR_HOLDINGS_BUYBACK_2023_30B',
    GREATEST(30000000000 - q_idx * 2500000000, 0)
FROM (
    SELECT DATE '2023-09-29' AS qd, 2200::NUMERIC AS amt_M, 510::NUMERIC AS pps, 1 AS q_idx UNION ALL
    SELECT DATE '2023-12-29', 2500, 540, 2 UNION ALL
    SELECT DATE '2024-03-29', 2700, 580, 3 UNION ALL
    SELECT DATE '2024-06-28', 2500, 620, 4 UNION ALL
    SELECT DATE '2024-09-27', 2300, 670, 5 UNION ALL
    SELECT DATE '2024-12-31', 2800, 720, 6 UNION ALL
    SELECT DATE '2025-03-31', 2600, 760, 7 UNION ALL
    SELECT DATE '2025-06-30', 2400, 790, 8 UNION ALL
    SELECT DATE '2025-09-30', 2700, 810, 9 UNION ALL
    SELECT DATE '2025-12-31', 2500, 835, 10 UNION ALL
    SELECT DATE '2026-03-31', 2300, 870, 11
) bb;

-- Quarterly dividends
INSERT INTO equity_action (
    uuid, company_ref, action_type, action_date, settle_date,
    shares, price_per_share, total_amount, currency_code, dividend_per_share,
    program_name, authorization_remaining
)
SELECT
    gen_uuid('EA_DIV_'||TO_CHAR(qd,'YYYYMMDD')),
    'GR_HOLDINGS','DIVIDEND', qd, DATEADD(day,14,qd),
    445000000, NULL, ROUND(445000000 * dps, 2), 'USD', dps,
    'GR_HOLDINGS_QUARTERLY_DIVIDEND', NULL
FROM (
    SELECT DATE '2023-08-15' AS qd, 1.02::NUMERIC AS dps UNION ALL
    SELECT DATE '2023-11-15', 1.02 UNION ALL
    SELECT DATE '2024-02-15', 1.10 UNION ALL
    SELECT DATE '2024-05-15', 1.10 UNION ALL
    SELECT DATE '2024-08-15', 1.10 UNION ALL
    SELECT DATE '2024-11-15', 1.18 UNION ALL
    SELECT DATE '2025-02-15', 1.18 UNION ALL
    SELECT DATE '2025-05-15', 1.18 UNION ALL
    SELECT DATE '2025-08-15', 1.25 UNION ALL
    SELECT DATE '2025-11-15', 1.25 UNION ALL
    SELECT DATE '2026-02-15', 1.30
) d;

-- Special dividend Q4-2024
INSERT INTO equity_action (
    uuid, company_ref, action_type, action_date, settle_date,
    shares, price_per_share, total_amount, currency_code, dividend_per_share,
    program_name, authorization_remaining
) VALUES (
    gen_uuid('EA_SPDIV_2024'),'GR_HOLDINGS','SPECIAL_DIVIDEND',
    DATE '2024-12-23', DATE '2024-12-30',
    445000000, NULL, 6230000000, 'USD', 14.00,
    'GR_HOLDINGS_SPECIAL_DIV_2024', NULL
);

-- -----------------------------------------------------------------------------
-- 15.4 CAPITAL_ALLOCATION_ACTUAL
-- Per top entity per quarter × 5 buckets, with framework target percentages
-- -----------------------------------------------------------------------------
TRUNCATE TABLE capital_allocation_actual;

INSERT INTO capital_allocation_actual (
    uuid, company_ref, period_date, bucket, amount, currency_code, framework_target_pct
)
SELECT
    gen_uuid('CAP_'||e.code||'_'||TO_CHAR(q.qend,'YYYYMMDD')||'_'||b.bucket),
    e.code, q.qend, b.bucket,
    ROUND(e.fcf_fy_base * 0.25 * b.share
          * (0.85 + gen_rand('CAP_'||e.code||TO_CHAR(q.qend,'YYYYMMDD')||b.bucket) * 0.30), 2),
    e.ccy, b.target_pct
FROM (
    SELECT 'GR_HOLDINGS' AS code,'GBP' AS ccy, 8000000000::NUMERIC AS fcf_fy_base UNION ALL
    SELECT 'GR_US_INC','USD', 5200000000 UNION ALL
    SELECT 'GR_EU_BV','EUR', 1800000000
) e
CROSS JOIN (
    SELECT DATE '2023-09-30' AS qend UNION ALL SELECT DATE '2023-12-31' UNION ALL
    SELECT DATE '2024-03-31' UNION ALL SELECT DATE '2024-06-30' UNION ALL
    SELECT DATE '2024-09-30' UNION ALL SELECT DATE '2024-12-31' UNION ALL
    SELECT DATE '2025-03-31' UNION ALL SELECT DATE '2025-06-30' UNION ALL
    SELECT DATE '2025-09-30' UNION ALL SELECT DATE '2025-12-31' UNION ALL
    SELECT DATE '2026-03-31'
) q
CROSS JOIN (
    SELECT 'CAPEX'         AS bucket, 0.40::NUMERIC AS share, 0.4000::NUMERIC AS target_pct UNION ALL
    SELECT 'DIVIDENDS',     0.20, 0.2000 UNION ALL
    SELECT 'BUYBACKS',      0.30, 0.3000 UNION ALL
    SELECT 'M_AND_A',       0.05, 0.0500 UNION ALL
    SELECT 'DEBT_PAYDOWN',  0.05, 0.0500
) b;

-- -----------------------------------------------------------------------------
-- 15.5 LETTER_OF_CREDIT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE letter_of_credit;

INSERT INTO letter_of_credit (
    uuid, lc_number, issuing_bank_ref, applicant_company_ref,
    beneficiary_name, beneficiary_country, lc_type, purpose,
    issue_date, expiration_date, face_amount, drawn_amount, currency_code,
    status, credit_facility_ref, fee_bps
)
SELECT
    gen_uuid('LC_'||lc_number), lc_number, issuing_bank_ref, applicant_company_ref,
    beneficiary_name, beneficiary_country, lc_type, purpose,
    issue_date, expiration_date, face_amount, drawn_amount, currency_code,
    status, credit_facility_ref, fee_bps
FROM (
    -- Standby LCs (utilities, insurance, workers' comp)
    SELECT 'LC-2024-00001' AS lc_number,'BANK_JPM' AS issuing_bank_ref,'GR_US_INC' AS applicant_company_ref,
           'CA Workers Comp Fund' AS beneficiary_name,'US' AS beneficiary_country,'STANDBY' AS lc_type,
           'Workers compensation insurance' AS purpose, DATE '2024-01-15' AS issue_date,
           DATE '2026-06-30' AS expiration_date, 25000000::NUMERIC AS face_amount, 0::NUMERIC AS drawn_amount,
           'USD' AS currency_code,'OPEN' AS status,'CF_RCF_USD_2B' AS credit_facility_ref, 75::NUMERIC AS fee_bps UNION ALL
    SELECT 'LC-2024-00002','BANK_JPM','GR_US_INC','Pacific Gas & Electric','US','STANDBY',
           'Utility deposit', DATE '2024-02-01', DATE '2026-07-15', 8000000, 0,'USD','OPEN','CF_RCF_USD_2B', 65 UNION ALL
    SELECT 'LC-2024-00003','BANK_HSBC','GR_GB','HM Customs','GB','STANDBY','Customs duty deferment',
           DATE '2024-03-01', DATE '2026-05-31', 5000000, 0,'GBP','OPEN',NULL, 70 UNION ALL
    -- *** LCs expiring within 90 days of WINDOW_END 2026-05-04 → drives Manager-F #3 ***
    SELECT 'LC-2025-00010','BANK_JPM','GR_US_INC','LA County Tax Collector','US','STANDBY','Property tax deferment',
           DATE '2025-05-15', DATE '2026-05-30', 12000000, 0,'USD','OPEN','CF_RCF_USD_2B', 70 UNION ALL
    SELECT 'LC-2025-00011','BANK_BNP','GR_EU_BV','Hafen Hamburg GmbH','DE','COMMERCIAL','Port concession',
           DATE '2025-06-01', DATE '2026-06-15', 7500000, 0,'EUR','OPEN','CF_RCF_EUR_500M', 80 UNION ALL
    SELECT 'LC-2025-00012','BANK_HSBC','GR_HK','HK Lands Department','HK','STANDBY','Building deposit',
           DATE '2025-07-01', DATE '2026-07-20', 4500000, 0,'HKD','OPEN',NULL, 65 UNION ALL
    -- Commercial LCs for inventory imports
    SELECT 'LC-2024-00020','BANK_CITI','GR_US_INC','Shanghai Apparel Mfg 12','CN','COMMERCIAL','Apparel imports H2 2024',
           DATE '2024-07-01', DATE '2025-01-31', 18000000, 18000000,'USD','DRAWN','CF_RCF_USD_2B', 90 UNION ALL
    SELECT 'LC-2024-00021','BANK_CITI','GR_EU_BV','Istanbul Apparel Mfg 7','TR','COMMERCIAL','Apparel imports',
           DATE '2024-09-15', DATE '2025-03-31', 8000000, 7800000,'EUR','DRAWN','CF_RCF_EUR_500M', 95 UNION ALL
    SELECT 'LC-2024-00022','BANK_HSBC','GR_GB','Dhaka Apparel Mfg 22','BD','COMMERCIAL','Apparel imports',
           DATE '2024-10-01', DATE '2025-04-30', 6000000, 6000000,'GBP','DRAWN',NULL, 95 UNION ALL
    SELECT 'LC-2025-00030','BANK_MUFG','GR_JP','Yokohama Construction KK','JP','STANDBY','Building lease deposit',
           DATE '2025-04-01', DATE '2027-03-31', 1500000000, 0,'JPY','OPEN',NULL, 60 UNION ALL
    SELECT 'LC-2025-00031','BANK_SCB','GR_AE','Jebel Ali Port Authority','AE','STANDBY','Port concession',
           DATE '2025-05-15', DATE '2027-05-14', 6000000, 0,'AED','OPEN',NULL, 75 UNION ALL
    SELECT 'LC-2025-00032','BANK_HSBC','GR_GB','Tesco Refrigeration Lease','GB','STANDBY','Equipment lease',
           DATE '2025-08-01', DATE '2027-07-31', 3500000, 0,'GBP','OPEN',NULL, 70 UNION ALL
    SELECT 'LC-2023-00100','BANK_BAC','GR_US_INC','Allstate Insurance','US','STANDBY','Self-insurance retention',
           DATE '2023-06-15', DATE '2025-06-15', 15000000, 0,'USD','EXPIRED','CF_RCF_USD_2B', 60 UNION ALL
    SELECT 'LC-2023-00101','BANK_BNP','GR_FR','EDF Energie','FR','STANDBY','Utility deposit',
           DATE '2023-07-01', DATE '2024-07-01', 2500000, 0,'EUR','EXPIRED',NULL, 65 UNION ALL
    SELECT 'LC-2024-00040','BANK_RBC','GR_CA','Canada Revenue Agency','CA','STANDBY','GST deferment',
           DATE '2024-05-01', DATE '2026-12-31', 4500000, 0,'CAD','OPEN',NULL, 70
) t;

-- -----------------------------------------------------------------------------
-- 15.6 PENSION_PLAN  (3 plans)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE pension_plan;

INSERT INTO pension_plan (uuid, code, company_ref, plan_name, plan_type, country, open_to_new_participants)
VALUES
    (gen_uuid('PP_US_DB'),'PP_US_DB','GR_US_INC','GlobalRetail US Pension Plan','DB','US',FALSE),
    (gen_uuid('PP_UK_DB'),'PP_UK_DB','GR_GB','GlobalRetail UK Pension Scheme','DB','GB',FALSE),
    (gen_uuid('PP_JP_DB'),'PP_JP_DB','GR_JP','GlobalRetail Japan Pension','HYBRID','JP',TRUE);

-- -----------------------------------------------------------------------------
-- 15.7 PENSION_VALUATION
-- US plan goes from ~105% funded in 2024 to ~96% in 2025 (rate-driven OCI hit).
-- -----------------------------------------------------------------------------
TRUNCATE TABLE pension_valuation;

INSERT INTO pension_valuation (
    uuid, plan_ref, as_of_date, projected_benefit_obligation, plan_assets_fair_value,
    funded_status, funded_status_pct, discount_rate_pct, expected_return_on_assets_pct,
    projected_contribution_y1, projected_contribution_y2, projected_contribution_y3,
    oci_impact, currency_code
)
SELECT
    gen_uuid('PV_'||plan_ref||'_'||TO_CHAR(as_of_date,'YYYYMMDD')),
    plan_ref, as_of_date, pbo, assets, assets - pbo, ROUND((assets-pbo)/NULLIF(pbo,0)*100, 4),
    disc, eroa, contrib_y1, contrib_y2, contrib_y3, oci, ccy
FROM (
    -- US DB plan
    SELECT 'PP_US_DB' AS plan_ref, DATE '2023-12-31' AS as_of_date,
           3950000000::NUMERIC AS pbo, 4080000000::NUMERIC AS assets,
           5.50::NUMERIC AS disc, 6.50::NUMERIC AS eroa,
           50000000::NUMERIC AS contrib_y1, 55000000::NUMERIC AS contrib_y2, 55000000::NUMERIC AS contrib_y3,
           0::NUMERIC AS oci,'USD' AS ccy UNION ALL
    SELECT 'PP_US_DB', DATE '2024-06-30', 4000000000, 4150000000, 5.40, 6.50, 50000000, 55000000, 55000000, 35000000,'USD' UNION ALL
    SELECT 'PP_US_DB', DATE '2024-12-31', 4050000000, 4250000000, 5.25, 6.50, 50000000, 55000000, 55000000, 120000000,'USD' UNION ALL
    -- 2025: rate dropped to 4.40% → PBO inflates, funded status flips negative
    SELECT 'PP_US_DB', DATE '2025-06-30', 4400000000, 4280000000, 4.55, 6.50, 80000000, 90000000, 95000000, -180000000,'USD' UNION ALL
    SELECT 'PP_US_DB', DATE '2025-12-31', 4550000000, 4350000000, 4.40, 6.50, 100000000, 110000000, 115000000, -320000000,'USD' UNION ALL
    -- UK DB plan
    SELECT 'PP_UK_DB', DATE '2023-12-31', 850000000, 920000000, 5.10, 5.80, 8000000, 9000000, 9000000, 0,'GBP' UNION ALL
    SELECT 'PP_UK_DB', DATE '2024-12-31', 870000000, 940000000, 4.95, 5.80, 8000000, 9000000, 9000000, 12000000,'GBP' UNION ALL
    SELECT 'PP_UK_DB', DATE '2025-12-31', 920000000, 935000000, 4.55, 5.80, 12000000, 14000000, 14000000, -35000000,'GBP' UNION ALL
    -- Japan hybrid
    SELECT 'PP_JP_DB', DATE '2023-12-31', 45000000000, 46000000000, 1.20, 2.00, 800000000, 900000000, 900000000, 0,'JPY' UNION ALL
    SELECT 'PP_JP_DB', DATE '2024-12-31', 46000000000, 47500000000, 1.30, 2.00, 800000000, 900000000, 900000000, 200000000,'JPY' UNION ALL
    SELECT 'PP_JP_DB', DATE '2025-12-31', 47000000000, 48200000000, 1.40, 2.00, 800000000, 900000000, 900000000, 150000000,'JPY'
) t;

-- VERIFY
SELECT 'company_financial_metric' AS t, COUNT(*) FROM company_financial_metric
UNION ALL SELECT 'credit_rating', COUNT(*) FROM credit_rating
UNION ALL SELECT 'equity_action', COUNT(*) FROM equity_action
UNION ALL SELECT 'capital_allocation_actual', COUNT(*) FROM capital_allocation_actual
UNION ALL SELECT 'letter_of_credit', COUNT(*) FROM letter_of_credit
UNION ALL SELECT 'pension_plan', COUNT(*) FROM pension_plan
UNION ALL SELECT 'pension_valuation', COUNT(*) FROM pension_valuation;
