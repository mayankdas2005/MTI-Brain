-- =============================================================================
-- 02_rates.sql (Redshift) — FX_RATE and BENCHMARK_RATE
-- Daily rates with random walk + scripted shocks per spec §2.3 / §3
-- =============================================================================
SET search_path TO lpp;

TRUNCATE TABLE fx_rate;

DROP TABLE IF EXISTS t_fx_anchors;
CREATE TEMP TABLE t_fx_anchors AS
SELECT * FROM (
    SELECT 'USD' AS base_currency,'EUR' AS quote_currency,0.9080::FLOAT AS anchor_rate,0.0::FLOAT AS daily_drift,0.0050::FLOAT AS daily_sigma UNION ALL
    SELECT 'USD','GBP',0.7980,0.0,0.0055 UNION ALL
    SELECT 'USD','JPY',137.20,0.0002,0.0060 UNION ALL
    SELECT 'USD','CNY',6.92,0.0001,0.0040 UNION ALL
    SELECT 'USD','HKD',7.85,0.0,0.0010 UNION ALL
    SELECT 'USD','SGD',1.327,0.0,0.0035 UNION ALL
    SELECT 'USD','AUD',1.515,0.0001,0.0070 UNION ALL
    SELECT 'USD','KRW',1330.0,0.0001,0.0075 UNION ALL
    SELECT 'USD','INR',81.80,0.0001,0.0040 UNION ALL
    SELECT 'USD','AED',3.6725,0.0,0.0001 UNION ALL
    SELECT 'USD','CAD',1.353,0.0,0.0040 UNION ALL
    SELECT 'USD','MXN',17.92,0.0,0.0090 UNION ALL
    SELECT 'USD','BRL',5.04,0.0,0.0100 UNION ALL
    SELECT 'USD','CLP',800.0,0.0001,0.0085 UNION ALL
    SELECT 'USD','PLN',4.18,0.0,0.0070 UNION ALL
    SELECT 'USD','SEK',10.30,0.0,0.0070 UNION ALL
    SELECT 'USD','CHF',0.895,0.0,0.0050 UNION ALL
    SELECT 'USD','USD',1.0,0.0,0.0
) t;

DROP TABLE IF EXISTS t_fx_path;
CREATE TEMP TABLE t_fx_path AS
WITH d AS (
    SELECT calendar_date, ROW_NUMBER() OVER (ORDER BY calendar_date) - 1 AS dnum
    FROM gen_calendar
),
shocks AS (
    SELECT * FROM (
        SELECT 'USD' AS base_currency,'EUR' AS quote_currency, DATE '2023-08-15' AS shock_start, DATE '2023-09-30' AS shock_end, -0.0010::FLOAT AS shock_drift UNION ALL
        SELECT 'USD','JPY', DATE '2024-01-15', DATE '2024-04-30',  0.0015 UNION ALL
        SELECT 'USD','BRL', DATE '2024-10-15', DATE '2024-12-15',  0.0040 UNION ALL
        SELECT 'USD','GBP', DATE '2025-01-15', DATE '2025-06-30', -0.0006 UNION ALL
        SELECT 'USD','MXN', DATE '2025-09-01', DATE '2025-10-15',  0.0050
    ) s
),
joined AS (
    SELECT
      d.calendar_date, d.dnum,
      a.base_currency, a.quote_currency, a.anchor_rate,
      a.daily_drift +
        COALESCE((SELECT shock_drift FROM shocks s
                  WHERE s.base_currency  = a.base_currency
                    AND s.quote_currency = a.quote_currency
                    AND d.calendar_date BETWEEN s.shock_start AND s.shock_end),0)
        AS effective_drift,
      a.daily_sigma,
      gen_normal(a.base_currency||'_'||a.quote_currency||'_'||CAST(d.calendar_date AS VARCHAR)) AS eps
    FROM d
    CROSS JOIN t_fx_anchors a
)
SELECT
    calendar_date, base_currency, quote_currency,
    anchor_rate * EXP(SUM(effective_drift + daily_sigma * eps)
                      OVER (PARTITION BY base_currency, quote_currency
                            ORDER BY dnum
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS rate
FROM joined;

INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT calendar_date, base_currency, quote_currency,
       ROUND(rate::NUMERIC, 10), 'SPOT',
       CASE WHEN MOD(ABS(FNV_HASH(base_currency||quote_currency||CAST(calendar_date AS VARCHAR))),1000) < 3 THEN 'INTERNAL'
            WHEN quote_currency='EUR' OR base_currency='EUR' THEN 'ECB'
            ELSE 'BLOOMBERG' END,
       calendar_date::TIMESTAMPTZ
FROM t_fx_path;

-- Mirror USD->X into X->USD
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT rate_date, quote_currency, base_currency,
       ROUND(1.0 / rate, 10), 'SPOT', source, as_of_timestamp
FROM fx_rate
WHERE base_currency='USD' AND quote_currency <> 'USD';

-- Cross rates EUR->X and GBP->X
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT a.rate_date, b.quote_currency, a.quote_currency,
       ROUND(a.rate / b.rate, 10), 'SPOT', 'INTERNAL', a.as_of_timestamp
FROM fx_rate a
JOIN fx_rate b ON b.rate_date = a.rate_date
WHERE a.base_currency='USD' AND a.quote_currency NOT IN ('USD','EUR','GBP')
  AND b.base_currency='USD' AND b.quote_currency IN ('EUR','GBP');

-- Full non-USD cross matrix: for every pair (X,Y) where neither is USD and X<>Y,
-- triangulate X->Y = (USD->Y) / (USD->X). Skip pairs already inserted above.
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT a.rate_date, a.quote_currency AS base_currency, b.quote_currency AS quote_currency,
       ROUND(b.rate / a.rate, 10), 'SPOT', 'INTERNAL', a.as_of_timestamp
FROM fx_rate a
JOIN fx_rate b ON b.rate_date = a.rate_date
WHERE a.rate_type='SPOT' AND a.base_currency='USD' AND a.quote_currency <> 'USD'
  AND b.rate_type='SPOT' AND b.base_currency='USD' AND b.quote_currency <> 'USD'
  AND a.quote_currency <> b.quote_currency
  AND a.quote_currency NOT IN ('EUR','GBP');  -- those already produced above

-- CLOSING duplicate
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT rate_date, base_currency, quote_currency, rate, 'CLOSING', source, as_of_timestamp
FROM fx_rate WHERE rate_type='SPOT';

-- AVG — week-ending
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT MAX(rate_date), base_currency, quote_currency,
       ROUND(AVG(rate),10),'AVG','INTERNAL',MAX(as_of_timestamp)
FROM fx_rate
WHERE rate_type='SPOT'
GROUP BY base_currency, quote_currency, DATE_TRUNC('week', rate_date);

-- AVG — month-end
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, rate_type, source, as_of_timestamp)
SELECT LAST_DAY(DATE_TRUNC('month', rate_date))::DATE, base_currency, quote_currency,
       ROUND(AVG(rate),10),'AVG','INTERNAL',MAX(as_of_timestamp)
FROM fx_rate
WHERE rate_type='SPOT'
GROUP BY base_currency, quote_currency, DATE_TRUNC('month', rate_date);

-- Edge case — delete 3 BRL Spot days
DELETE FROM fx_rate
WHERE rate_type='SPOT' AND base_currency='USD' AND quote_currency='BRL'
  AND rate_date IN (DATE '2024-11-12', DATE '2024-11-13', DATE '2024-11-14');

-- -----------------------------------------------------------------------------
-- 2.2 BENCHMARK_RATE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE benchmark_rate;

DROP TABLE IF EXISTS t_bmk_anchors;
CREATE TEMP TABLE t_bmk_anchors AS
SELECT * FROM (
    SELECT 'SOFR' AS code,'USD' AS ccy,5.30::FLOAT AS anchor,3.75::FLOAT AS terminal UNION ALL
    SELECT 'T_BILL_3M','USD',5.05,3.65 UNION ALL
    SELECT 'ESTR','EUR',3.85,2.50 UNION ALL
    SELECT 'EURIBOR_3M','EUR',4.05,2.65 UNION ALL
    SELECT 'SONIA','GBP',4.45,3.85 UNION ALL
    SELECT 'SARON','CHF',1.40,0.90 UNION ALL
    SELECT 'TONA','JPY',0.05,0.50 UNION ALL
    SELECT 'SORA','SGD',3.65,2.85 UNION ALL
    SELECT 'CDOR','CAD',4.85,0.0 UNION ALL
    SELECT 'HONIA','HKD',4.10,3.20 UNION ALL
    SELECT 'CDI','BRL',13.65,9.80 UNION ALL
    SELECT 'TIIE_28D','MXN',11.50,8.50 UNION ALL
    SELECT 'AUD_BBSW_3M','AUD',3.85,4.05
) t;

INSERT INTO benchmark_rate (benchmark_code, rate_date, tenor, rate, currency_code, source)
SELECT b.code, c.calendar_date,
       CASE WHEN b.code IN ('T_BILL_3M','EURIBOR_3M','AUD_BBSW_3M') THEN '3M'
            WHEN b.code = 'TIIE_28D' THEN '1M'
            ELSE 'ON' END,
       ROUND(
         (b.anchor + (b.terminal - b.anchor) * (DATEDIFF(day, DATE '2023-05-01', c.calendar_date)/1100.0)
         + 0.05 * gen_normal(b.code||'_'||CAST(c.calendar_date AS VARCHAR)))::NUMERIC
       , 6),
       b.ccy, 'CENTRALBANK'
FROM t_bmk_anchors b
CROSS JOIN gen_calendar c
WHERE NOT (b.code='CDOR' AND c.calendar_date > DATE '2024-06-28');

INSERT INTO benchmark_rate (benchmark_code, rate_date, tenor, rate, currency_code, source)
SELECT benchmark_code, rate_date, '1M', ROUND(rate + 0.05,6), currency_code, source
FROM benchmark_rate WHERE benchmark_code IN ('SOFR','ESTR','SONIA') AND tenor='ON';

INSERT INTO benchmark_rate (benchmark_code, rate_date, tenor, rate, currency_code, source)
SELECT benchmark_code, rate_date, '3M', ROUND(rate + 0.10,6), currency_code, source
FROM benchmark_rate WHERE benchmark_code IN ('SOFR','ESTR','SONIA') AND tenor='ON';

-- VERIFY
SELECT 'FX_RATE' AS t, COUNT(*) FROM fx_rate
UNION ALL SELECT 'FX_RATE_SPOT_USD', COUNT(*) FROM fx_rate WHERE rate_type='SPOT' AND base_currency='USD'
UNION ALL SELECT 'BENCHMARK_RATE', COUNT(*) FROM benchmark_rate
UNION ALL SELECT 'CDOR_after_cessation', COUNT(*) FROM benchmark_rate WHERE benchmark_code='CDOR' AND rate_date > DATE '2024-06-28';

SELECT rate_date, rate FROM fx_rate
WHERE base_currency='USD' AND quote_currency='JPY' AND rate_type='SPOT'
  AND rate_date IN (DATE '2023-12-29', DATE '2024-03-29', DATE '2024-06-28', DATE '2024-12-31', DATE '2025-04-30')
ORDER BY rate_date;
