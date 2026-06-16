-- =============================================================================
-- 00_setup.sql (Redshift) — gen_control config, helper UDFs, gen_numbers row
-- generator, calendar with regional holidays, per-region seasonality multipliers.
--
-- Redshift notes:
--   • No session variables → values are written to gen_control and read with
--     scalar subqueries: (SELECT value FROM gen_control WHERE key='WINDOW_START')::DATE
--   • No row generator → gen_numbers(n INT) is pre-populated 0..100000
--   • HASH → FNV_HASH(... ::varchar)   (deterministic per seed; different
--     hash family from Snowflake — bucket assignments differ row-by-row but
--     volumes/seasonality/distributions are preserved)
--   • SHA2 → native in Redshift (current versions); used directly for gen_uuid
--     to keep UUID output bit-identical to the Snowflake build.
--   • DAYOFWEEK → EXTRACT(DOW FROM d); Sun=0, Sat=6 (same convention as source)
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- gen_control: holds run-time tunables in place of Snowflake session variables
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS gen_control CASCADE;
CREATE TABLE gen_control (
    key   VARCHAR(64) NOT NULL PRIMARY KEY,
    value VARCHAR(256)
);

INSERT INTO gen_control VALUES
    ('SCALE_FACTOR', '1.0'),
    ('SEED',         '42'),
    ('WINDOW_START', '2023-05-01'),
    ('WINDOW_END',   '2026-05-04'),
    ('DAYS',         CAST(DATEDIFF(day, DATE '2023-05-01', DATE '2026-05-04') AS VARCHAR));

-- -----------------------------------------------------------------------------
-- gen_numbers: 0..1,000,000 (replaces TABLE(GENERATOR(ROWCOUNT=>N)) / SEQ4())
-- Sized to cover the largest single-generator in the pipeline (B2B AR invoices
-- at SCALE_FACTOR=1.0 → 240k rows) with comfortable headroom. Built via a
-- 6-digit cross-join of a base 0..9 list.
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS gen_numbers CASCADE;
CREATE TABLE gen_numbers (n INTEGER NOT NULL);

INSERT INTO gen_numbers
SELECT a.d + b.d*10 + c.d*100 + e.d*1000 + f.d*10000 + g.d*100000
FROM (SELECT 0 d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
      UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) a
CROSS JOIN (SELECT 0 d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
            UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) b
CROSS JOIN (SELECT 0 d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
            UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) c
CROSS JOIN (SELECT 0 d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
            UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) e
CROSS JOIN (SELECT 0 d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
            UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) f
CROSS JOIN (SELECT 0 d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
            UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) g;
-- (gives 1,000,000 rows; add row for n=1000000 to make ranges inclusive)
INSERT INTO gen_numbers VALUES (1000000);

-- -----------------------------------------------------------------------------
-- Helper UDFs (Redshift SQL UDFs — pure expressions, IMMUTABLE)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION gen_rand(key VARCHAR(MAX)) RETURNS FLOAT
IMMUTABLE
AS $$ SELECT ((ABS(FNV_HASH($1::VARCHAR)) % 1000000) / 1000000.0)::FLOAT $$
LANGUAGE sql;

CREATE OR REPLACE FUNCTION gen_normal(key VARCHAR(MAX)) RETURNS FLOAT
IMMUTABLE
AS $$
    SELECT ( gen_rand($1||'_a') + gen_rand($1||'_b') + gen_rand($1||'_c')
           + gen_rand($1||'_d') + gen_rand($1||'_e') + gen_rand($1||'_f') - 3 ) * 1.4142
$$ LANGUAGE sql;

-- Redshift SHA2 returns the hex-encoded SHA-256 digest (64 hex chars), matching
-- Snowflake's SHA2 default. UUID output is bit-identical to the Snowflake build
-- given the same SEED + key.
CREATE OR REPLACE FUNCTION gen_uuid(key VARCHAR(MAX)) RETURNS VARCHAR(64)
IMMUTABLE
AS $$
    -- SEED hardcoded to '42' (Redshift SQL UDFs disallow subqueries); keep gen_control SEED in sync
    SELECT UPPER(
        SUBSTRING(SHA2($1 || '42', 256), 1, 8)  || '-' ||
        SUBSTRING(SHA2($1 || '42', 256), 9, 4)  || '-' ||
        SUBSTRING(SHA2($1 || '42', 256),13, 4)  || '-' ||
        SUBSTRING(SHA2($1 || '42', 256),17, 4)  || '-' ||
        SUBSTRING(SHA2($1 || '42', 256),21,12)
    )
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION add_business_days(d DATE, n INTEGER) RETURNS DATE
IMMUTABLE
AS $$
    SELECT (CASE
      WHEN $2 = 0 THEN $1
      WHEN $2 = 1 THEN
        CASE EXTRACT(DOW FROM $1)
          WHEN 5 THEN DATEADD(day,3,$1) WHEN 6 THEN DATEADD(day,2,$1) ELSE DATEADD(day,1,$1) END
      WHEN $2 = 2 THEN
        CASE EXTRACT(DOW FROM $1)
          WHEN 4 THEN DATEADD(day,4,$1) WHEN 5 THEN DATEADD(day,4,$1)
          WHEN 6 THEN DATEADD(day,3,$1) ELSE DATEADD(day,2,$1) END
      ELSE DATEADD(day, ($2 + 2*FLOOR($2/5))::INTEGER, $1)
    END)::DATE
$$ LANGUAGE sql;

-- Year-over-year revenue growth multiplier (compounded from window start)
CREATE OR REPLACE FUNCTION yoy_growth(d DATE) RETURNS FLOAT
IMMUTABLE
AS $$
    SELECT (POWER(1.065, DATEDIFF(year, DATE '2023-05-01', $1) -
                        CASE WHEN DATEDIFF(year, DATE '2023-05-01', $1) > 1 THEN 0.5 ELSE 0 END))::FLOAT
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION ap_inflation(d DATE) RETURNS FLOAT
IMMUTABLE
AS $$ SELECT (POWER(1.06, DATEDIFF(year, DATE '2023-05-01', $1)))::FLOAT $$
LANGUAGE sql;

-- Regional revenue seasonality multiplier (date × region-derived bucket)
CREATE OR REPLACE FUNCTION region_seasonality(d DATE, region VARCHAR(16)) RETURNS FLOAT
IMMUTABLE
AS $$
    SELECT (CASE
      WHEN $2 IN ('AMER','EMEA') AND EXTRACT(MONTH FROM $1)=11 AND EXTRACT(DAY FROM $1) BETWEEN 24 AND 30 THEN 3.5
      WHEN $2 IN ('AMER','EMEA') AND EXTRACT(MONTH FROM $1)=12 AND EXTRACT(DAY FROM $1) IN (1,2)          THEN 2.5
      WHEN $2='APAC' AND EXTRACT(MONTH FROM $1)=11 AND EXTRACT(DAY FROM $1)=11                            THEN 4.0
      WHEN $2='APAC' AND EXTRACT(MONTH FROM $1)=11 AND EXTRACT(DAY FROM $1) BETWEEN 12 AND 15             THEN 1.6
      WHEN $2<>'MENA' AND EXTRACT(MONTH FROM $1)=12 AND EXTRACT(DAY FROM $1) BETWEEN 1 AND 22             THEN 1.8
      WHEN $2<>'MENA' AND EXTRACT(MONTH FROM $1)=12 AND EXTRACT(DAY FROM $1) BETWEEN 24 AND 31            THEN 0.4
      WHEN $2 IN ('AMER','EMEA') AND EXTRACT(MONTH FROM $1)=1                                              THEN 1.4
      WHEN $2 IN ('AMER','EMEA') AND EXTRACT(MONTH FROM $1)=8                                              THEN 1.5
      WHEN $2 IN ('AMER','EMEA') AND EXTRACT(MONTH FROM $1)=9 AND EXTRACT(DAY FROM $1) <= 15               THEN 1.3
      WHEN $2='APAC' AND EXTRACT(MONTH FROM $1)=2                                                          THEN 1.2
      WHEN $2='EMEA' AND EXTRACT(MONTH FROM $1) IN (7,8) AND EXTRACT(DAY FROM $1) <= 24                    THEN 0.85
      WHEN $2='MENA' AND (($1 BETWEEN DATE '2023-04-15' AND DATE '2023-04-25')
                       OR ($1 BETWEEN DATE '2024-04-05' AND DATE '2024-04-15')
                       OR ($1 BETWEEN DATE '2025-03-25' AND DATE '2025-04-05')
                       OR ($1 BETWEEN DATE '2026-03-15' AND DATE '2026-03-25')) THEN 1.4
      WHEN $2='LATAM' AND ( ($1 BETWEEN DATE '2024-02-12' AND DATE '2024-02-15')
                         OR ($1 BETWEEN DATE '2025-03-03' AND DATE '2025-03-06') ) THEN 0.3
      ELSE 1.0
    END)::FLOAT
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION ap_cny_factor(d DATE) RETURNS FLOAT
IMMUTABLE
AS $$
    SELECT (CASE
      WHEN $1 BETWEEN DATE '2024-02-05' AND DATE '2024-02-19' THEN 0.20
      WHEN $1 BETWEEN DATE '2025-01-26' AND DATE '2025-02-08' THEN 0.20
      WHEN $1 BETWEEN DATE '2026-02-12' AND DATE '2026-02-26' THEN 0.20
      ELSE 1.0
    END)::FLOAT
$$ LANGUAGE sql;

-- -----------------------------------------------------------------------------
-- Calendar with regional holiday flags
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS gen_holiday CASCADE;
CREATE TABLE gen_holiday (
    holiday_date DATE,
    region       VARCHAR(16),
    name         VARCHAR(256)
);

INSERT INTO gen_holiday VALUES
    (DATE '2023-01-01','GLOBAL','New Year'),(DATE '2024-01-01','GLOBAL','New Year'),
    (DATE '2025-01-01','GLOBAL','New Year'),(DATE '2026-01-01','GLOBAL','New Year'),
    (DATE '2023-07-04','AMER','US Jul 4'),(DATE '2024-07-04','AMER','US Jul 4'),(DATE '2025-07-04','AMER','US Jul 4'),
    (DATE '2023-11-23','AMER','US Thanksgiving'),(DATE '2024-11-28','AMER','US Thanksgiving'),(DATE '2025-11-27','AMER','US Thanksgiving'),
    (DATE '2023-12-25','GLOBAL','Christmas'),(DATE '2024-12-25','GLOBAL','Christmas'),(DATE '2025-12-25','GLOBAL','Christmas'),
    (DATE '2023-12-26','EMEA','Boxing Day'),(DATE '2024-12-26','EMEA','Boxing Day'),(DATE '2025-12-26','EMEA','Boxing Day'),
    (DATE '2023-05-01','EMEA','EU Labour'),(DATE '2024-05-01','EMEA','EU Labour'),(DATE '2025-05-01','EMEA','EU Labour'),
    (DATE '2023-04-07','EMEA','Good Friday'),(DATE '2024-03-29','EMEA','Good Friday'),(DATE '2025-04-18','EMEA','Good Friday'),
    (DATE '2023-04-10','EMEA','Easter Monday'),(DATE '2024-04-01','EMEA','Easter Monday'),(DATE '2025-04-21','EMEA','Easter Monday'),
    (DATE '2023-04-29','APAC','JP Golden Week'),(DATE '2023-05-03','APAC','JP Golden Week'),(DATE '2023-05-04','APAC','JP Golden Week'),(DATE '2023-05-05','APAC','JP Golden Week'),
    (DATE '2024-04-29','APAC','JP Golden Week'),(DATE '2024-05-03','APAC','JP Golden Week'),(DATE '2024-05-04','APAC','JP Golden Week'),(DATE '2024-05-05','APAC','JP Golden Week'),(DATE '2024-05-06','APAC','JP Golden Week'),
    (DATE '2025-04-29','APAC','JP Golden Week'),(DATE '2025-05-03','APAC','JP Golden Week'),(DATE '2025-05-04','APAC','JP Golden Week'),(DATE '2025-05-05','APAC','JP Golden Week'),
    (DATE '2024-02-10','APAC','CNY'),(DATE '2024-02-12','APAC','CNY'),(DATE '2024-02-13','APAC','CNY'),(DATE '2024-02-14','APAC','CNY'),(DATE '2024-02-15','APAC','CNY'),(DATE '2024-02-16','APAC','CNY'),(DATE '2024-02-17','APAC','CNY'),
    (DATE '2025-01-29','APAC','CNY'),(DATE '2025-01-30','APAC','CNY'),(DATE '2025-01-31','APAC','CNY'),(DATE '2025-02-03','APAC','CNY'),(DATE '2025-02-04','APAC','CNY'),
    (DATE '2023-10-02','APAC','CN National Day'),(DATE '2024-10-01','APAC','CN National Day'),(DATE '2025-10-01','APAC','CN National Day'),
    (DATE '2024-02-12','LATAM','Carnival'),(DATE '2024-02-13','LATAM','Carnival'),(DATE '2024-02-14','LATAM','Carnival'),
    (DATE '2025-03-03','LATAM','Carnival'),(DATE '2025-03-04','LATAM','Carnival'),(DATE '2025-03-05','LATAM','Carnival'),
    (DATE '2023-04-21','MENA','Eid al-Fitr'),(DATE '2024-04-10','MENA','Eid al-Fitr'),(DATE '2025-03-31','MENA','Eid al-Fitr'),
    (DATE '2023-12-02','MENA','UAE National Day'),(DATE '2024-12-02','MENA','UAE National Day'),(DATE '2025-12-02','MENA','UAE National Day');

DROP TABLE IF EXISTS gen_calendar CASCADE;
CREATE TABLE gen_calendar AS
WITH days AS (
    SELECT DATEADD(day, n, DATE '2023-05-01')::DATE AS d
    FROM gen_numbers
    WHERE n < 1100 AND DATEADD(day, n, DATE '2023-05-01') <= DATE '2026-05-04'
)
SELECT
    d                                              AS calendar_date,
    EXTRACT(YEAR  FROM d)                          AS yr,
    EXTRACT(MONTH FROM d)                          AS mo,
    EXTRACT(DAY   FROM d)                          AS dy,
    EXTRACT(DOW   FROM d)                          AS dow,
    CASE WHEN EXTRACT(DOW FROM d) IN (0,6) THEN FALSE ELSE TRUE END AS is_weekday,
    EXISTS(SELECT 1 FROM gen_holiday h WHERE h.holiday_date = d AND h.region='GLOBAL') AS is_global_holiday
FROM days;

-- Per-region business day view
DROP TABLE IF EXISTS gen_regional_bd CASCADE;
CREATE TABLE gen_regional_bd AS
SELECT
    c.calendar_date,
    r.region,
    (c.is_weekday AND MAX(COALESCE(hflag.has_holiday,0)) = 0) AS is_bd
FROM gen_calendar c
CROSS JOIN ( SELECT 'AMER' AS region UNION ALL SELECT 'EMEA' UNION ALL SELECT 'APAC'
             UNION ALL SELECT 'LATAM' UNION ALL SELECT 'MENA' ) r
LEFT JOIN (
    SELECT DISTINCT holiday_date, region AS hregion, 1 AS has_holiday
    FROM gen_holiday
) hflag
  ON hflag.holiday_date = c.calendar_date
 AND (hflag.hregion = 'GLOBAL' OR hflag.hregion = r.region)
GROUP BY c.calendar_date, r.region, c.is_weekday;

CREATE OR REPLACE VIEW v_business_days AS
SELECT calendar_date FROM gen_calendar
WHERE is_weekday AND calendar_date NOT IN (SELECT holiday_date FROM gen_holiday WHERE region='GLOBAL');

CREATE OR REPLACE VIEW v_last_bd_of_month AS
SELECT MAX(calendar_date) AS calendar_date
FROM v_business_days GROUP BY EXTRACT(YEAR FROM calendar_date), EXTRACT(MONTH FROM calendar_date);

-- Sanity
SELECT * FROM gen_control;
SELECT MIN(calendar_date), MAX(calendar_date), COUNT(*) AS n_days,
       COUNT(CASE WHEN is_weekday THEN 1 END) AS n_weekdays
FROM gen_calendar;
SELECT region, COUNT(CASE WHEN is_bd THEN 1 END) AS bd_count FROM gen_regional_bd GROUP BY region;
