-- =============================================================================
-- 06_intercompany_sweeps.sql (Redshift) — SWEEP_EXECUTION + INTERCOMPANY_TRANSACTION
-- Final balances rebuilt in script 10.
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- Interim cash balance for sweep input
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS t_interim_bal;
CREATE TEMP TABLE t_interim_bal AS
WITH daily_net AS (
    SELECT account_ref, value_date AS d, SUM(signed_amount) AS net
    FROM cash_flow WHERE value_date IS NOT NULL
    GROUP BY account_ref, value_date
),
opening AS (
    SELECT code AS account_ref, DATE '2023-04-30' AS d, COALESCE(initial_accounting_balance,0) AS net
    FROM bank_account
),
all_flows AS (
    SELECT * FROM daily_net UNION ALL SELECT * FROM opening
),
running AS (
    SELECT ar.account_ref, c.calendar_date,
           SUM(COALESCE(a.net,0))
             OVER (PARTITION BY ar.account_ref ORDER BY c.calendar_date
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS bal
    FROM (SELECT DISTINCT account_ref FROM all_flows) ar
    CROSS JOIN gen_calendar c
    LEFT JOIN all_flows a ON a.account_ref = ar.account_ref AND a.d = c.calendar_date
)
SELECT * FROM running;

-- -----------------------------------------------------------------------------
-- 6.1 SWEEP_EXECUTION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE sweep_execution;

DROP TABLE IF EXISTS t_sweep_runs;
CREATE TEMP TABLE t_sweep_runs AS
SELECT
    si.code AS instruction_code,
    si.source_account_ref,
    si.target_account_ref,
    si.target_balance,
    si.target_balance_ccy,
    si.threshold_amount,
    si.sweep_type,
    c.calendar_date,
    COALESCE(b.bal, 0) AS pre_balance,
    gen_rand('SW_'||si.code||'_'||CAST(c.calendar_date AS VARCHAR)) AS r
FROM sweep_instruction si
JOIN gen_calendar c ON c.is_weekday AND NOT c.is_global_holiday
                   AND c.calendar_date >= si.effective_from
                   AND (si.effective_to IS NULL OR c.calendar_date <= si.effective_to)
LEFT JOIN t_interim_bal b ON b.account_ref = si.source_account_ref AND b.calendar_date = c.calendar_date
WHERE si.active OR si.effective_to IS NOT NULL;

INSERT INTO sweep_execution (
    uuid, instruction_ref, execution_date,
    source_account_ref, target_account_ref,
    swept_amount, currency_code,
    pre_sweep_balance, post_sweep_balance, residual_amount,
    status, cash_flow_uuid, counter_cash_flow_uuid
)
SELECT
    gen_uuid('SX_'||instruction_code||'_'||CAST(calendar_date AS VARCHAR)),
    instruction_code, calendar_date,
    source_account_ref, target_account_ref,
    CASE
      WHEN sweep_type='ZBA'        THEN GREATEST(pre_balance, 0)
      WHEN sweep_type='TARGET'     THEN GREATEST(pre_balance - COALESCE(target_balance,0),0)
      WHEN sweep_type='THRESHOLD'  THEN CASE WHEN pre_balance > COALESCE(threshold_amount,0)
                                              THEN pre_balance - COALESCE(threshold_amount,0)*0.9
                                              ELSE 0 END
      WHEN sweep_type='NOTIONAL_POOL' THEN 0
      ELSE 0
    END,
    target_balance_ccy, pre_balance,
    pre_balance - CASE
      WHEN sweep_type='ZBA'       THEN GREATEST(pre_balance, 0)
      WHEN sweep_type='TARGET'    THEN GREATEST(pre_balance - COALESCE(target_balance,0),0)
      WHEN sweep_type='THRESHOLD' THEN CASE WHEN pre_balance > COALESCE(threshold_amount,0)
                                              THEN pre_balance - COALESCE(threshold_amount,0)*0.9 ELSE 0 END
      ELSE 0 END,
    CASE WHEN instruction_code='SW_USA_RGNL_CONCENTRATION' THEN 200000 ELSE 0 END,
    CASE
      WHEN sweep_type='THRESHOLD' AND pre_balance < COALESCE(threshold_amount,0) THEN 'SKIPPED'
      WHEN r < 0.01
        OR (calendar_date BETWEEN DATE '2024-02-12' AND DATE '2024-02-15' AND target_balance_ccy='BRL')
        OR (calendar_date BETWEEN DATE '2024-04-29' AND DATE '2024-05-06' AND target_balance_ccy='JPY')
        OR (calendar_date= DATE '2023-11-24' AND target_balance_ccy='USD')
      THEN 'FAILED'
      WHEN r < 0.04 THEN 'SKIPPED'
      ELSE 'EXECUTED'
    END,
    gen_uuid('CF_SW_DR_'||instruction_code||'_'||CAST(calendar_date AS VARCHAR)),
    gen_uuid('CF_SW_CR_'||instruction_code||'_'||CAST(calendar_date AS VARCHAR))
FROM t_sweep_runs;

-- Sweep cash flow legs
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT sx.cash_flow_uuid, sx.source_account_ref, 'SWEEP_DEBIT','BC_TREAS','CONFIRMED',
       sx.execution_date, sx.execution_date,
       -ROUND(sx.swept_amount,2), sx.currency_code, -ROUND(sx.swept_amount,2),
       -ROUND(sx.swept_amount,2), sx.currency_code, 1.0,
       'Sweep '||sx.target_account_ref, 'Sweep debit - '||sx.instruction_ref, sx.uuid, 'BOOK'
FROM sweep_execution sx WHERE sx.status='EXECUTED' AND sx.swept_amount > 0
UNION ALL
SELECT sx.counter_cash_flow_uuid, sx.target_account_ref, 'SWEEP_CREDIT','BC_TREAS','CONFIRMED',
       sx.execution_date, sx.execution_date,
       ROUND(sx.swept_amount,2), sx.currency_code, ROUND(sx.swept_amount,2),
       ROUND(sx.swept_amount,2), sx.currency_code, 1.0,
       'Sweep '||sx.source_account_ref, 'Sweep credit - '||sx.instruction_ref, sx.uuid, 'BOOK'
FROM sweep_execution sx WHERE sx.status='EXECUTED' AND sx.swept_amount > 0;

-- -----------------------------------------------------------------------------
-- 6.2 INTERCOMPANY_TRANSACTION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE intercompany_transaction;

DROP TABLE IF EXISTS t_ic;
CREATE TEMP TABLE t_ic AS
WITH base AS (
    SELECT
        n+1 AS seq,
        DATEADD(day, MOD(ABS(FNV_HASH('IC_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')::DATE AS txn_dt,
        (CASE MOD(ABS(FNV_HASH('IC_PUR|'||CAST(n AS VARCHAR))),11)
           WHEN 0 THEN 'FUNDING' WHEN 1 THEN 'FUNDING' WHEN 2 THEN 'FUNDING' WHEN 3 THEN 'FUNDING'
           WHEN 4 THEN 'CASH_POOL' WHEN 5 THEN 'CASH_POOL'
           WHEN 6 THEN 'LOAN' WHEN 7 THEN 'LOAN' WHEN 8 THEN 'SERVICE_FEE'
           WHEN 9 THEN 'ROYALTY' ELSE 'DIVIDEND' END) AS purpose,
        (CASE MOD(ABS(FNV_HASH('IC_CCY|'||CAST(n AS VARCHAR))),14)
           WHEN 0 THEN 'USD' WHEN 1 THEN 'EUR' WHEN 2 THEN 'GBP' WHEN 3 THEN 'JPY'
           WHEN 4 THEN 'HKD' WHEN 5 THEN 'SGD' WHEN 6 THEN 'AUD' WHEN 7 THEN 'MXN'
           WHEN 8 THEN 'BRL' WHEN 9 THEN 'CAD' WHEN 10 THEN 'SEK' WHEN 11 THEN 'PLN'
           WHEN 12 THEN 'AED' ELSE 'KRW' END) AS ccy,
        (CASE MOD(ABS(FNV_HASH('IC_CO|'||CAST(n AS VARCHAR))),17)
           WHEN 0 THEN 'GR_US_INC' WHEN 1 THEN 'GR_GB' WHEN 2 THEN 'GR_FR' WHEN 3 THEN 'GR_DE'
           WHEN 4 THEN 'GR_JP' WHEN 5 THEN 'GR_HK' WHEN 6 THEN 'GR_AU' WHEN 7 THEN 'GR_MX'
           WHEN 8 THEN 'GR_BR' WHEN 9 THEN 'GR_AE' WHEN 10 THEN 'GR_KR' WHEN 11 THEN 'GR_SE'
           WHEN 12 THEN 'GR_PL' WHEN 13 THEN 'GR_CA' WHEN 14 THEN 'GR_IE' WHEN 15 THEN 'GR_IT'
           ELSE 'GR_ES' END) AS opco
    FROM gen_numbers WHERE n < 9000
)
SELECT b.*,
       'GR_TREASURY' AS treasury_code,
       'IHB_'||b.ccy||'_CONCENTRATION' AS treasury_acct,
       (SELECT MIN(code) FROM bank_account
         WHERE company_ref = b.opco AND currency_ref = b.ccy AND NOT closed_account
       ) AS opco_acct,
       ROUND((EXP(11 + 1.0 * gen_normal('IC_'||CAST(b.seq AS VARCHAR)))
              * yoy_growth(b.txn_dt)
              * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt,
       gen_rand('IC_S_'||CAST(b.seq AS VARCHAR)) AS sr
FROM base b;

INSERT INTO intercompany_transaction (
    uuid, reference, transaction_date, value_date,
    amount, currency_code, purpose,
    source_company_ref, source_account_ref, source_cash_flow_ref,
    target_company_ref, target_account_ref, target_cash_flow_ref,
    status
)
SELECT
    gen_uuid('IC_'||CAST(seq AS VARCHAR)), 'IC-'||LPAD(CAST(seq AS VARCHAR),7,'0'),
    txn_dt, add_business_days(txn_dt,1),
    amt, ccy, purpose,
    treasury_code, treasury_acct, gen_uuid('CF_IC_DR_'||CAST(seq AS VARCHAR)),
    opco, opco_acct, gen_uuid('CF_IC_CR_'||CAST(seq AS VARCHAR)),
    CASE WHEN sr < 0.013 THEN 'INITIATED' ELSE 'SETTLED' END
FROM t_ic
WHERE opco_acct IS NOT NULL;

-- IC cash flow legs
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT ic.source_cash_flow_ref, ic.source_account_ref,
       CASE ic.purpose WHEN 'LOAN' THEN 'IC_LOAN_DRAW' WHEN 'DIVIDEND' THEN 'IC_DIVIDEND'
                       WHEN 'ROYALTY' THEN 'IC_ROYALTY' WHEN 'SERVICE_FEE' THEN 'IC_SERVICE_FEE'
                       ELSE 'IC_FUNDING' END,
       'BC_TREAS','CONFIRMED', ic.transaction_date, ic.value_date,
       -ic.amount, ic.currency_code, -ic.amount,
       -ic.amount, ic.currency_code, 1.0,
       ic.target_company_ref, 'Intercompany '||ic.purpose||' to '||ic.target_company_ref,
       ic.reference, 'BOOK'
FROM intercompany_transaction ic WHERE ic.status='SETTLED'
UNION ALL
SELECT ic.target_cash_flow_ref, ic.target_account_ref,
       CASE ic.purpose WHEN 'LOAN' THEN 'IC_LOAN_DRAW' WHEN 'DIVIDEND' THEN 'IC_DIVIDEND'
                       WHEN 'ROYALTY' THEN 'IC_ROYALTY' WHEN 'SERVICE_FEE' THEN 'IC_SERVICE_FEE'
                       ELSE 'IC_FUNDING_IN' END,
       'BC_TREAS','CONFIRMED', ic.transaction_date, ic.value_date,
       ic.amount, ic.currency_code, ic.amount,
       ic.amount, ic.currency_code, 1.0,
       ic.source_company_ref, 'Intercompany '||ic.purpose||' from '||ic.source_company_ref,
       ic.reference, 'BOOK'
FROM intercompany_transaction ic WHERE ic.status='SETTLED';

-- VERIFY
SELECT 'SWEEP_EXECUTION' AS t, COUNT(*),
       SUM(CASE WHEN status='EXECUTED' THEN 1 ELSE 0 END) AS executed,
       SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed,
       SUM(CASE WHEN status='SKIPPED' THEN 1 ELSE 0 END) AS skipped
FROM sweep_execution;
SELECT 'INTERCOMPANY_TRANSACTION' AS t, COUNT(*),
       SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) AS settled,
       SUM(CASE WHEN status='INITIATED' THEN 1 ELSE 0 END) AS initiated
FROM intercompany_transaction;
