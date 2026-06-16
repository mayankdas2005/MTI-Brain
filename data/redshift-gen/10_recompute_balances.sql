-- =============================================================================
-- 10_recompute_balances.sql (Redshift)
-- Rebuild BANK_STATEMENT_BALANCE and CASH_BALANCE from full CASH_FLOW table.
-- =============================================================================
SET search_path TO lpp;

TRUNCATE TABLE bank_statement_balance;
TRUNCATE TABLE cash_balance;

-- -----------------------------------------------------------------------------
-- 10.1 Daily net cash flow + opening balance row
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS t_val_net;
CREATE TEMP TABLE t_val_net AS
SELECT account_ref, value_date AS d, SUM(signed_amount) AS net
FROM cash_flow WHERE value_date IS NOT NULL
GROUP BY account_ref, value_date;

DROP TABLE IF EXISTS t_txn_net;
CREATE TEMP TABLE t_txn_net AS
SELECT account_ref, transaction_date AS d, SUM(signed_amount) AS net
FROM cash_flow WHERE transaction_date IS NOT NULL
GROUP BY account_ref, transaction_date;

DROP TABLE IF EXISTS t_openings;
CREATE TEMP TABLE t_openings AS
SELECT code AS account_ref,
       initial_accounting_balance_date AS d,
       COALESCE(initial_accounting_balance, 0) AS net
FROM bank_account
WHERE initial_accounting_balance_date IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 10.2 Running balances
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS t_run_val;
CREATE TEMP TABLE t_run_val AS
WITH all_flows AS (
    SELECT * FROM t_val_net UNION ALL SELECT * FROM t_openings
),
universe AS (SELECT DISTINCT account_ref FROM all_flows),
joined AS (
    SELECT u.account_ref, c.calendar_date,
           SUM(COALESCE(a.net, 0))
             OVER (PARTITION BY u.account_ref ORDER BY c.calendar_date
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS bal
    FROM universe u
    CROSS JOIN gen_calendar c
    LEFT JOIN all_flows a ON a.account_ref = u.account_ref AND a.d = c.calendar_date
)
SELECT * FROM joined;

DROP TABLE IF EXISTS t_run_txn;
CREATE TEMP TABLE t_run_txn AS
WITH all_flows AS (
    SELECT * FROM t_txn_net UNION ALL SELECT * FROM t_openings
),
universe AS (SELECT DISTINCT account_ref FROM all_flows),
joined AS (
    SELECT u.account_ref, c.calendar_date,
           SUM(COALESCE(a.net, 0))
             OVER (PARTITION BY u.account_ref ORDER BY c.calendar_date
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS bal
    FROM universe u
    CROSS JOIN gen_calendar c
    LEFT JOIN all_flows a ON a.account_ref = u.account_ref AND a.d = c.calendar_date
)
SELECT * FROM joined;

-- -----------------------------------------------------------------------------
-- 10.3 BANK_STATEMENT_BALANCE — CLOSING + OPENING + INTRADAY
-- -----------------------------------------------------------------------------
INSERT INTO bank_statement_balance (uuid, account_ref, statement_date, balance_type, amount, currency_code, source_file, quality_status)
SELECT
    gen_uuid('BSB_C_'||r.account_ref||'_'||CAST(r.calendar_date AS VARCHAR)),
    r.account_ref, r.calendar_date, 'CLOSING',
    ROUND(r.bal,2),
    ba.currency_ref,
    'BANK_FEED_'||TO_CHAR(r.calendar_date,'YYYYMMDD')||'.bai',
    CASE WHEN r.account_ref='USA_RGNL_OPERATING'
              AND r.calendar_date BETWEEN DATE '2025-09-15' AND DATE '2025-09-18' THEN 'FAILED'
         WHEN MOD(ABS(FNV_HASH(r.account_ref||'|'||CAST(r.calendar_date AS VARCHAR))),1000) < 4 THEN 'FAILED'
         ELSE 'PASSED' END
FROM t_run_val r
JOIN bank_account ba ON ba.code = r.account_ref
WHERE NOT (ba.closed_account AND r.calendar_date > ba.closing_date);

-- OPENING = prior day CLOSING
INSERT INTO bank_statement_balance (uuid, account_ref, statement_date, balance_type, amount, currency_code, source_file, quality_status)
SELECT
    gen_uuid('BSB_O_'||account_ref||'_'||CAST(statement_date AS VARCHAR)),
    account_ref, statement_date, 'OPENING',
    COALESCE(LAG(amount, 1) OVER (PARTITION BY account_ref ORDER BY statement_date), 0),
    currency_code, source_file, quality_status
FROM bank_statement_balance WHERE balance_type='CLOSING';

-- INTRADAY snapshots (3/day) for top 30 accounts
INSERT INTO bank_statement_balance (uuid, account_ref, statement_date, balance_type, amount, currency_code, source_file, quality_status)
WITH top30 AS (
    SELECT account_ref FROM cash_flow GROUP BY account_ref ORDER BY COUNT(*) DESC LIMIT 30
),
intraday_pts AS (
    SELECT t.account_ref, c.calendar_date, p.pt
    FROM top30 t
    CROSS JOIN gen_calendar c
    CROSS JOIN ( SELECT 1 AS pt UNION ALL SELECT 2 UNION ALL SELECT 3 ) p
    WHERE c.is_weekday AND NOT c.is_global_holiday
)
SELECT
    gen_uuid('BSB_I_'||ip.account_ref||'_'||CAST(ip.calendar_date AS VARCHAR)||'_'||CAST(ip.pt AS VARCHAR)),
    ip.account_ref, ip.calendar_date, 'INTRADAY',
    ROUND((o.amount + (cl.amount - o.amount) * ip.pt / 4.0)::NUMERIC, 2),
    o.currency_code,
    'INTRADAY_'||TO_CHAR(ip.calendar_date,'YYYYMMDD')||'_'||CAST(ip.pt AS VARCHAR)||'.bai',
    'PASSED'
FROM intraday_pts ip
JOIN bank_statement_balance o ON o.account_ref = ip.account_ref AND o.statement_date = ip.calendar_date AND o.balance_type='OPENING'
JOIN bank_statement_balance cl ON cl.account_ref = ip.account_ref AND cl.statement_date = ip.calendar_date AND cl.balance_type='CLOSING';

-- -----------------------------------------------------------------------------
-- 10.4 CASH_BALANCE — 7 canonical combinations
-- -----------------------------------------------------------------------------

-- Combo 1: VALUE_DATE, T/T/F/F
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT r.account_ref, r.calendar_date, 'VALUE_DATE',
       TRUE, TRUE, FALSE, FALSE,
       ROUND(r.bal,2), ba.currency_ref, 'CONFIRMED'
FROM t_run_val r JOIN bank_account ba ON ba.code = r.account_ref
WHERE NOT (ba.closed_account AND r.calendar_date > ba.closing_date);

-- Combo 2: TRANSACTION_DATE, T/T/F/F
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT r.account_ref, r.calendar_date, 'TRANSACTION_DATE',
       TRUE, TRUE, FALSE, FALSE,
       ROUND(r.bal,2), ba.currency_ref, 'CONFIRMED'
FROM t_run_txn r JOIN bank_account ba ON ba.code = r.account_ref
WHERE NOT (ba.closed_account AND r.calendar_date > ba.closing_date);

-- Combo 3: VALUE_DATE, T/F/F/F (EOD only)
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT account_ref, balance_date, 'VALUE_DATE',
       TRUE, FALSE, FALSE, FALSE,
       amount, currency_code, 'CONFIRMED'
FROM cash_balance
WHERE date_basis='VALUE_DATE' AND includes_actual AND includes_intraday
  AND NOT includes_confirmed AND NOT includes_estimated;

-- Combo 4: VALUE_DATE, T/T/T/F
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT account_ref, balance_date, 'VALUE_DATE',
       TRUE, TRUE, TRUE, FALSE,
       ROUND((amount + amount * 0.05 * gen_rand('FC_CONF_'||account_ref||CAST(balance_date AS VARCHAR)))::NUMERIC,2),
       currency_code, 'CONFIRMED'
FROM cash_balance
WHERE date_basis='VALUE_DATE' AND includes_actual AND includes_intraday
  AND NOT includes_confirmed AND NOT includes_estimated;

-- Combo 5: VALUE_DATE, T/T/T/T
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT account_ref, balance_date, 'VALUE_DATE',
       TRUE, TRUE, TRUE, TRUE,
       ROUND((amount + amount * 0.10 * gen_rand('FC_EST_'||account_ref||CAST(balance_date AS VARCHAR)))::NUMERIC,2),
       currency_code, 'ESTIMATED'
FROM cash_balance
WHERE date_basis='VALUE_DATE' AND includes_actual AND includes_intraday
  AND NOT includes_confirmed AND NOT includes_estimated;

-- Combo 6: VALUE_DATE, F/F/T/F (forecast-only)
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT account_ref, balance_date, 'VALUE_DATE',
       FALSE, FALSE, TRUE, FALSE,
       ROUND((amount * 0.08 * gen_rand('FC_CONLY_'||account_ref||CAST(balance_date AS VARCHAR)))::NUMERIC,2),
       currency_code, 'CONFIRMED'
FROM cash_balance
WHERE date_basis='VALUE_DATE' AND includes_actual AND includes_intraday
  AND NOT includes_confirmed AND NOT includes_estimated;

-- Combo 7: TRANSACTION_DATE, F/F/T/T
INSERT INTO cash_balance (account_ref, balance_date, date_basis,
    includes_actual, includes_intraday, includes_confirmed, includes_estimated,
    amount, currency_code, cash_flow_status)
SELECT account_ref, balance_date, 'TRANSACTION_DATE',
       FALSE, FALSE, TRUE, TRUE,
       ROUND((amount * 0.12 * gen_rand('FC_T_'||account_ref||CAST(balance_date AS VARCHAR)))::NUMERIC,2),
       currency_code, 'ESTIMATED'
FROM cash_balance
WHERE date_basis='TRANSACTION_DATE' AND includes_actual AND includes_intraday
  AND NOT includes_confirmed AND NOT includes_estimated;

-- VERIFY
SELECT 'BANK_STATEMENT_BALANCE' AS t, COUNT(*) FROM bank_statement_balance
UNION ALL SELECT 'BSB_INTRADAY', COUNT(*) FROM bank_statement_balance WHERE balance_type='INTRADAY'
UNION ALL SELECT 'BSB_FAILED',   COUNT(*) FROM bank_statement_balance WHERE quality_status='FAILED'
UNION ALL SELECT 'CASH_BALANCE', COUNT(*) FROM cash_balance;

WITH cf_sum AS (
    SELECT account_ref, value_date, SUM(signed_amount) AS net
    FROM cash_flow WHERE value_date IS NOT NULL
    GROUP BY account_ref, value_date
), bsb_lag AS (
    SELECT account_ref, statement_date, amount,
           LAG(amount,1) OVER (PARTITION BY account_ref ORDER BY statement_date) AS prev_amt
    FROM bank_statement_balance WHERE balance_type='CLOSING'
), bsb_delta AS (
    SELECT account_ref, statement_date, amount - NVL(prev_amt, 0) AS bal_delta
    FROM bsb_lag
)
SELECT 'CONSISTENCY_VIOLATIONS' AS t, COUNT(*)
FROM cf_sum c JOIN bsb_delta b ON b.account_ref=c.account_ref AND b.statement_date=c.value_date
WHERE ABS(c.net - b.bal_delta) > 0.01;
