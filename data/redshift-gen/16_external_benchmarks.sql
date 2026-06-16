-- =============================================================================
-- 16_external_benchmarks.sql (Redshift) — Section Q of extensions #2.
--
-- Populates peer_company, peer_company_metric, macro_indicator.
-- Real deployments load these from S&P/Bloomberg/Refinitiv ETLs; here we
-- synthesize plausible (NOT real-filing) values for demo prompts.
--
-- Scripted shapes:
--   • 7 retail peers (WMT, TGT, HD, KR, LOW, AMZN, BJ) over 20 quarters
--     (2021-Q1..2025-Q4) so V43 has ≥20 rows per peer.
--   • Macro indicators monthly 2023-2026 + daily FED_FUNDS, SOFR_3M.
--     Rate-hike path: FED_FUNDS 0.50%→5.50% 2022-2023, plateau 5.50% 2024,
--     start of easing late-2024 stepping to ~4.25% by mid-2025.
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 16.1 PEER_COMPANY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE peer_company;
INSERT INTO peer_company (code, name, ticker, sector, peer_group, country) VALUES
    ('WMT','Walmart Inc.','WMT','Consumer Staples Retail','BIG_BOX_RETAIL','US'),
    ('TGT','Target Corp.','TGT','Consumer Discretionary Retail','BIG_BOX_RETAIL','US'),
    ('HD','The Home Depot Inc.','HD','Consumer Discretionary Retail','HOME_IMPROVEMENT','US'),
    ('KR','The Kroger Co.','KR','Consumer Staples Retail','GROCERY','US'),
    ('LOW','Lowe''s Companies Inc.','LOW','Consumer Discretionary Retail','HOME_IMPROVEMENT','US'),
    ('AMZN','Amazon.com Inc.','AMZN','Consumer Discretionary / Cloud','ECOMMERCE','US'),
    ('BJ','BJ''s Wholesale Club','BJ','Consumer Staples Retail','WAREHOUSE_CLUB','US');

-- -----------------------------------------------------------------------------
-- 16.2 PEER_COMPANY_METRIC — 20 quarters × 7 peers = 140 rows + FY/TTM
-- -----------------------------------------------------------------------------
TRUNCATE TABLE peer_company_metric;

DROP TABLE IF EXISTS t_peer_qends;
CREATE TEMP TABLE t_peer_qends AS
SELECT * FROM (
    SELECT DATE '2021-03-31' AS qend, 1 AS q_idx UNION ALL
    SELECT DATE '2021-06-30',  2 UNION ALL SELECT DATE '2021-09-30',  3 UNION ALL
    SELECT DATE '2021-12-31',  4 UNION ALL SELECT DATE '2022-03-31',  5 UNION ALL
    SELECT DATE '2022-06-30',  6 UNION ALL SELECT DATE '2022-09-30',  7 UNION ALL
    SELECT DATE '2022-12-31',  8 UNION ALL SELECT DATE '2023-03-31',  9 UNION ALL
    SELECT DATE '2023-06-30', 10 UNION ALL SELECT DATE '2023-09-30', 11 UNION ALL
    SELECT DATE '2023-12-31', 12 UNION ALL SELECT DATE '2024-03-31', 13 UNION ALL
    SELECT DATE '2024-06-30', 14 UNION ALL SELECT DATE '2024-09-30', 15 UNION ALL
    SELECT DATE '2024-12-31', 16 UNION ALL SELECT DATE '2025-03-31', 17 UNION ALL
    SELECT DATE '2025-06-30', 18 UNION ALL SELECT DATE '2025-09-30', 19 UNION ALL
    SELECT DATE '2025-12-31', 20
) t;

INSERT INTO peer_company_metric (
    peer_code, period_date, period_type, reporting_currency,
    revenue, ebitda, free_cash_flow, net_debt,
    leverage_ratio, debt_to_ebitda, return_on_capital_pct,
    dividend_yield_pct, buyback_yield_pct, shareholder_return_yield_pct,
    wacc_pct,
    payments_cost_pct_revenue, fraud_loss_bps, interchange_pct
)
SELECT
    p.code, q.qend, 'Q', 'USD',
    -- Revenue (quarterly), with rough growth + seasonality (Q4 + 20%)
    ROUND( p.rev_q_base
           * POWER(1 + p.growth, q.q_idx / 4.0)
           * (CASE WHEN EXTRACT(MONTH FROM q.qend)=12 THEN 1.20 ELSE 1.0 END)
           * (0.97 + gen_rand('PR_'||p.code||TO_CHAR(q.qend,'YYYYMMDD'))*0.06)
         , 2),
    ROUND( p.rev_q_base * p.ebitda_margin
           * POWER(1 + p.growth, q.q_idx/4.0)
           * (CASE WHEN EXTRACT(MONTH FROM q.qend)=12 THEN 1.20 ELSE 1.0 END), 2),
    ROUND( p.rev_q_base * p.fcf_margin
           * POWER(1 + p.growth, q.q_idx/4.0), 2),
    ROUND( p.net_debt_base * (1 + (q.q_idx - 10)*0.01), 2),
    p.leverage, p.dte, p.roc_pct,
    p.div_yld, p.bb_yld, p.div_yld + p.bb_yld, p.wacc,
    p.pmt_cost_pct, p.fraud_bps, p.interchange_pct
FROM (
    -- Per-peer base quarterly revenue in $M, margins, capital structure
    SELECT 'WMT' AS code, 145000000000::NUMERIC AS rev_q_base, 0.060::NUMERIC AS ebitda_margin,
           0.040::NUMERIC AS fcf_margin, 60000000000::NUMERIC AS net_debt_base, 1.8::NUMERIC AS leverage,
           1.4::NUMERIC AS dte, 14.5::NUMERIC AS roc_pct, 1.50::NUMERIC AS div_yld, 1.30::NUMERIC AS bb_yld,
           0.072::NUMERIC AS wacc, 1.65::NUMERIC AS pmt_cost_pct, 4.2::NUMERIC AS fraud_bps, 1.55::NUMERIC AS interchange_pct,
           0.045::NUMERIC AS growth UNION ALL
    SELECT 'TGT', 27000000000, 0.085, 0.045, 16000000000, 1.6, 1.2, 13.0, 2.20, 2.10, 0.078, 1.80, 5.0, 1.65, 0.040 UNION ALL
    SELECT 'HD',  39000000000, 0.155, 0.090, 42000000000, 2.2, 1.5, 28.0, 2.40, 3.20, 0.082, 1.55, 3.8, 1.50, 0.035 UNION ALL
    SELECT 'KR',  37000000000, 0.045, 0.025, 13000000000, 2.0, 1.7, 11.0, 2.10, 1.70, 0.076, 1.85, 5.5, 1.70, 0.030 UNION ALL
    SELECT 'LOW', 22000000000, 0.135, 0.080, 33000000000, 2.5, 1.8, 26.0, 2.00, 4.10, 0.085, 1.55, 3.9, 1.50, 0.035 UNION ALL
    SELECT 'AMZN',135000000000, 0.180, 0.080, -10000000000, 0.4, 0.3, 18.0, 0.00, 1.10, 0.090, 1.95, 6.8, 1.75, 0.080 UNION ALL
    SELECT 'BJ',   4500000000, 0.065, 0.045,  1200000000, 0.9, 0.6, 16.0, 0.00, 1.50, 0.084, 1.70, 4.5, 1.62, 0.060
) p
CROSS JOIN t_peer_qends q;

-- Add FY rows for each peer (2023, 2024, 2025) — aggregate of quarterly
INSERT INTO peer_company_metric (
    peer_code, period_date, period_type, reporting_currency,
    revenue, ebitda, free_cash_flow, net_debt,
    leverage_ratio, debt_to_ebitda, return_on_capital_pct,
    dividend_yield_pct, buyback_yield_pct, shareholder_return_yield_pct, wacc_pct,
    payments_cost_pct_revenue, fraud_loss_bps, interchange_pct
)
SELECT peer_code, DATE '2024-12-31','FY','USD',
       SUM(CASE WHEN period_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' AND period_type='Q' THEN revenue ELSE 0 END),
       SUM(CASE WHEN period_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' AND period_type='Q' THEN ebitda ELSE 0 END),
       SUM(CASE WHEN period_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' AND period_type='Q' THEN free_cash_flow ELSE 0 END),
       AVG(CASE WHEN period_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' AND period_type='Q' THEN net_debt END),
       AVG(leverage_ratio), AVG(debt_to_ebitda), AVG(return_on_capital_pct),
       AVG(dividend_yield_pct), AVG(buyback_yield_pct), AVG(shareholder_return_yield_pct), AVG(wacc_pct),
       AVG(payments_cost_pct_revenue), AVG(fraud_loss_bps), AVG(interchange_pct)
FROM peer_company_metric
WHERE period_type='Q'
GROUP BY peer_code;

INSERT INTO peer_company_metric (
    peer_code, period_date, period_type, reporting_currency,
    revenue, ebitda, free_cash_flow, net_debt,
    leverage_ratio, debt_to_ebitda, return_on_capital_pct,
    dividend_yield_pct, buyback_yield_pct, shareholder_return_yield_pct, wacc_pct,
    payments_cost_pct_revenue, fraud_loss_bps, interchange_pct
)
SELECT peer_code, DATE '2025-12-31','FY','USD',
       SUM(CASE WHEN period_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31' AND period_type='Q' THEN revenue ELSE 0 END),
       SUM(CASE WHEN period_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31' AND period_type='Q' THEN ebitda ELSE 0 END),
       SUM(CASE WHEN period_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31' AND period_type='Q' THEN free_cash_flow ELSE 0 END),
       AVG(CASE WHEN period_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31' AND period_type='Q' THEN net_debt END),
       AVG(leverage_ratio), AVG(debt_to_ebitda), AVG(return_on_capital_pct),
       AVG(dividend_yield_pct), AVG(buyback_yield_pct), AVG(shareholder_return_yield_pct), AVG(wacc_pct),
       AVG(payments_cost_pct_revenue), AVG(fraud_loss_bps), AVG(interchange_pct)
FROM peer_company_metric
WHERE period_type='Q'
GROUP BY peer_code;

-- -----------------------------------------------------------------------------
-- 16.3 MACRO_INDICATOR
-- Monthly observations for CPI_US, EUR_USD_VOL, OIL_BRENT, GEOPOL_RISK_INDEX
-- Daily observations for FED_FUNDS, SOFR_3M
-- -----------------------------------------------------------------------------
TRUNCATE TABLE macro_indicator;

-- Daily FED_FUNDS — rate-hike path 2022-2023, plateau, easing
INSERT INTO macro_indicator (indicator_code, region, as_of_date, value, unit, source)
SELECT 'FED_FUNDS','US', c.calendar_date,
       CASE
         WHEN c.calendar_date < DATE '2022-03-15' THEN 0.25
         WHEN c.calendar_date < DATE '2022-05-04' THEN 0.50
         WHEN c.calendar_date < DATE '2022-06-15' THEN 1.00
         WHEN c.calendar_date < DATE '2022-07-27' THEN 1.75
         WHEN c.calendar_date < DATE '2022-09-21' THEN 2.50
         WHEN c.calendar_date < DATE '2022-11-02' THEN 3.25
         WHEN c.calendar_date < DATE '2022-12-14' THEN 4.00
         WHEN c.calendar_date < DATE '2023-02-01' THEN 4.50
         WHEN c.calendar_date < DATE '2023-03-22' THEN 4.75
         WHEN c.calendar_date < DATE '2023-05-03' THEN 5.00
         WHEN c.calendar_date < DATE '2023-07-26' THEN 5.25
         WHEN c.calendar_date < DATE '2024-09-18' THEN 5.50
         WHEN c.calendar_date < DATE '2024-11-07' THEN 5.00
         WHEN c.calendar_date < DATE '2024-12-18' THEN 4.75
         WHEN c.calendar_date < DATE '2025-06-15' THEN 4.50
         WHEN c.calendar_date < DATE '2025-12-15' THEN 4.25
         ELSE 4.00 END,
       'pct','FRED'
FROM gen_calendar c
WHERE EXTRACT(DOW FROM c.calendar_date) IN (1,3,5);  -- M/W/F observations

-- Daily SOFR_3M
INSERT INTO macro_indicator (indicator_code, region, as_of_date, value, unit, source)
SELECT 'SOFR_3M','US', c.calendar_date,
       CASE
         WHEN c.calendar_date < DATE '2023-07-26' THEN 4.80
         WHEN c.calendar_date < DATE '2024-09-18' THEN 5.30
         WHEN c.calendar_date < DATE '2025-06-15' THEN 4.50
         ELSE 4.10 END
       + gen_normal('SOFR_'||CAST(c.calendar_date AS VARCHAR)) * 0.05,
       'pct','FRBNY'
FROM gen_calendar c
WHERE EXTRACT(DOW FROM c.calendar_date) = 3;  -- weekly

-- Monthly CPI_US, EUR_USD_VOL, OIL_BRENT, GEOPOL_RISK_INDEX
INSERT INTO macro_indicator (indicator_code, region, as_of_date, value, unit, source)
SELECT ind.indicator_code, ind.region, m.month_end, ind.base + gen_normal(ind.indicator_code||TO_CHAR(m.month_end,'YYYYMM')) * ind.vol,
       ind.unit, ind.source
FROM (
    SELECT 'CPI_US' AS indicator_code,'US' AS region, 3.5::NUMERIC AS base, 0.3::NUMERIC AS vol,'pct_yoy' AS unit,'BLS' AS source UNION ALL
    SELECT 'EUR_USD_VOL','GLOBAL', 7.5, 1.5,'pct','CME' UNION ALL
    SELECT 'OIL_BRENT','GLOBAL', 80.0, 8.0,'usd_per_bbl','ICE' UNION ALL
    SELECT 'GEOPOL_RISK_INDEX','GLOBAL', 100.0, 25.0,'index','BBAEP'
) ind
CROSS JOIN (
    SELECT DISTINCT LAST_DAY(calendar_date) AS month_end FROM gen_calendar
    WHERE calendar_date >= DATE '2022-01-01'
) m;

-- VERIFY
SELECT 'peer_company' AS t, COUNT(*) FROM peer_company
UNION ALL SELECT 'peer_company_metric', COUNT(*) FROM peer_company_metric
UNION ALL SELECT 'macro_indicator', COUNT(*) FROM macro_indicator;
