-- =============================================================================
-- 11_metrics_recon.sql (Redshift) — WORKING_CAPITAL_METRIC, GL_BALANCE,
-- GL_RECONCILIATION, COUNTERPARTY_EXPOSURE (rebuilt from final cash balances).
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 11.1 WORKING_CAPITAL_METRIC
-- -----------------------------------------------------------------------------
TRUNCATE TABLE working_capital_metric;

INSERT INTO working_capital_metric (
    company_ref, period_date, dso_days, dpo_days, dio_days, ccc_days,
    ar_balance, ap_balance, revenue_ttm, cogs_ttm, currency_code
)
WITH
ar_open AS (
    SELECT company_ref, LAST_DAY(issue_date) AS pd, SUM(open_amount) AS ar_bal
    FROM ar_invoice GROUP BY company_ref, LAST_DAY(issue_date)
),
ap_open AS (
    SELECT company_ref, LAST_DAY(issue_date) AS pd, SUM(open_amount) AS ap_bal
    FROM ap_invoice GROUP BY company_ref, LAST_DAY(issue_date)
),
cogs_only AS (
    SELECT company_ref, LAST_DAY(issue_date) AS pd, SUM(invoice_amount) AS cogs_amt
    FROM ap_invoice
    WHERE vendor_ref LIKE 'TP_APPAREL_%' OR vendor_ref LIKE 'TP_LOGI_%' OR vendor_ref LIKE 'TP_CAPEX_%'
    GROUP BY company_ref, LAST_DAY(issue_date)
),
rev_base AS (
    SELECT company_ref, LAST_DAY(issue_date) AS pd, SUM(invoice_amount) AS rev
    FROM ar_invoice GROUP BY company_ref, LAST_DAY(issue_date)
),
rev_ttm AS (
    SELECT company_ref, pd,
           SUM(rev) OVER (PARTITION BY company_ref ORDER BY pd
                          ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS rev_amt
    FROM rev_base
),
cogs_ttm AS (
    SELECT company_ref, pd,
           SUM(cogs_amt) OVER (PARTITION BY company_ref ORDER BY pd
                               ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS cogs_amt
    FROM cogs_only
)
SELECT
    co.code, ar.pd,
    ROUND((NULLIF(ar.ar_bal,0) / NULLIF(r.rev_amt/365.0,0)
        + CASE WHEN co.code='GR_ES' AND ar.pd BETWEEN DATE '2024-04-30' AND DATE '2024-06-30' THEN 12 ELSE 0 END
    )::NUMERIC, 2),
    ROUND((NULLIF(ap.ap_bal,0) / NULLIF(g.cogs_amt/365.0,0)
        + CASE WHEN ar.pd >= DATE '2024-01-31' THEN 4 ELSE 0 END
    )::NUMERIC, 2),
    ROUND((85
        + CASE EXTRACT(MONTH FROM ar.pd) WHEN 9 THEN 22 WHEN 10 THEN 25
                            WHEN 2 THEN -12 WHEN 3 THEN -10 ELSE 0 END
        + 5 * gen_normal('DIO_'||co.code||CAST(ar.pd AS VARCHAR))
    )::NUMERIC, 2),
    NULL,
    ar.ar_bal, ap.ap_bal, r.rev_amt, g.cogs_amt,
    'USD'
FROM company co
JOIN ar_open ar    ON ar.company_ref = co.code
LEFT JOIN ap_open ap   ON ap.company_ref = co.code AND ap.pd = ar.pd
LEFT JOIN rev_ttm r    ON r.company_ref = co.code  AND r.pd = ar.pd
LEFT JOIN cogs_ttm g   ON g.company_ref = co.code  AND g.pd = ar.pd;

UPDATE working_capital_metric
SET ccc_days = COALESCE(dso_days,0) + COALESCE(dio_days,0) - COALESCE(dpo_days,0);

-- -----------------------------------------------------------------------------
-- 11.2 GL_BALANCE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE gl_balance;

-- Cash GL accounts
INSERT INTO gl_balance (gl_account_ref, balance_date, balance_type, amount, currency_code, source_system, loaded_at)
SELECT g.code, c.calendar_date, 'CLOSING',
       COALESCE(cb.amount,0), g.currency_ref,
       CASE g.chart_of_accounts WHEN 'SAP' THEN 'SAP' WHEN 'ORACLE' THEN 'ORACLE' WHEN 'NETSUITE' THEN 'NETSUITE' ELSE 'UNKNOWN' END,
       c.calendar_date::TIMESTAMPTZ
FROM gl_account g
JOIN gen_calendar c ON c.calendar_date = LAST_DAY(c.calendar_date) AND c.is_weekday
LEFT JOIN cash_balance cb ON cb.account_ref = g.bank_account_ref
                          AND cb.balance_date = c.calendar_date
                          AND cb.date_basis='VALUE_DATE' AND cb.includes_actual AND cb.includes_intraday
                          AND NOT cb.includes_confirmed AND NOT cb.includes_estimated
WHERE g.bank_account_ref IS NOT NULL;

-- Non-cash GL — monthly delta with YTD or lifetime roll-forward
INSERT INTO gl_balance (gl_account_ref, balance_date, balance_type, amount, currency_code, source_system, loaded_at)
WITH months AS (
    SELECT DISTINCT LAST_DAY(calendar_date) AS pd, EXTRACT(YEAR FROM calendar_date) AS yr
    FROM gen_calendar
    WHERE calendar_date = LAST_DAY(calendar_date) AND is_weekday
),
monthly_delta AS (
    SELECT g.code, m.pd, m.yr, g.currency_ref, g.chart_of_accounts,
           ROUND((EXP(13 + 0.4 * gen_normal(g.code||'_DELTA_'||CAST(m.pd AS VARCHAR)))
                  * CASE g.account_class WHEN 'LIABILITY' THEN -1 WHEN 'EQUITY' THEN -1
                                          WHEN 'REVENUE'   THEN -1 ELSE 1 END
                  * CASE WHEN g.code LIKE '4000%' THEN 1.0 + 0.5*(CASE WHEN EXTRACT(MONTH FROM m.pd) IN (11,12) THEN 1 ELSE 0 END)
                         ELSE 1.0 END)::NUMERIC, 2) AS mo_delta
    FROM gl_account g CROSS JOIN months m
    WHERE g.bank_account_ref IS NULL
)
SELECT code, pd, 'CLOSING',
       ROUND(SUM(mo_delta) OVER (
           PARTITION BY code,
             CASE WHEN code LIKE '4000%' OR code LIKE '5000%' OR code LIKE '6000%'
                       OR code LIKE '7000%' OR code LIKE '7100%' OR code LIKE '7500%'
                  THEN yr ELSE NULL END
           ORDER BY pd ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 2),
       currency_ref,
       CASE chart_of_accounts WHEN 'SAP' THEN 'SAP' WHEN 'ORACLE' THEN 'ORACLE' WHEN 'NETSUITE' THEN 'NETSUITE' ELSE 'UNKNOWN' END,
       pd::TIMESTAMPTZ
FROM monthly_delta;

-- -----------------------------------------------------------------------------
-- 11.3 GL_RECONCILIATION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE gl_reconciliation;

INSERT INTO gl_reconciliation (
    uuid, bank_account_ref, gl_account_ref, as_of_date,
    bank_balance, gl_balance, variance_amount, variance_currency, status, notes
)
SELECT
    gen_uuid('GLR_'||g.code||'_'||CAST(c.calendar_date AS VARCHAR)),
    g.bank_account_ref, g.code, c.calendar_date,
    cb.amount, gb.amount,
    ROUND((COALESCE(cb.amount,0) - COALESCE(gb.amount,0))::NUMERIC, 2),
    g.currency_ref,
    CASE
      WHEN cb.amount IS NULL OR gb.amount IS NULL THEN 'INVESTIGATING'
      WHEN ABS(cb.amount - gb.amount) <= ABS(cb.amount) * 0.0001 THEN 'MATCHED'
      WHEN ABS(cb.amount - gb.amount) BETWEEN 1000 AND 50000 THEN 'UNMATCHED'
      WHEN ABS(cb.amount - gb.amount) > 50000 THEN 'INVESTIGATING'
      ELSE 'MATCHED'
    END,
    CASE
      WHEN ABS(COALESCE(cb.amount,0) - COALESCE(gb.amount,0)) > 50000
        THEN 'Wire in transit / large recon item' ELSE NULL END
FROM gl_account g
JOIN gen_calendar c ON c.calendar_date = LAST_DAY(c.calendar_date) AND c.is_weekday
LEFT JOIN cash_balance cb ON cb.account_ref = g.bank_account_ref AND cb.balance_date = c.calendar_date
                          AND cb.date_basis='VALUE_DATE' AND cb.includes_actual AND cb.includes_intraday
                          AND NOT cb.includes_confirmed AND NOT cb.includes_estimated
LEFT JOIN gl_balance gb  ON gb.gl_account_ref = g.code AND gb.balance_date = c.calendar_date AND gb.balance_type='CLOSING'
WHERE g.bank_account_ref IS NOT NULL;

-- BANK_DB EUR persistent variance
DELETE FROM gl_reconciliation
WHERE bank_account_ref='GR_DE_OPERATING_1' AND as_of_date IN
    (DATE '2024-07-31', DATE '2024-08-30', DATE '2024-09-30', DATE '2024-10-31', DATE '2024-11-29', DATE '2024-12-31');

INSERT INTO gl_reconciliation (
    uuid, bank_account_ref, gl_account_ref, as_of_date,
    bank_balance, gl_balance, variance_amount, variance_currency, status, notes
)
SELECT gen_uuid('GLR_DB_'||CAST(c.calendar_date AS VARCHAR)),
       'GR_DE_OPERATING_1','1110-GR_DE_OPERATING_1', c.calendar_date,
       COALESCE(cb.amount, 1500000),
       COALESCE(cb.amount, 1500000) - 4500.00,
       4500.00, 'EUR', 'INVESTIGATING',
       'Persistent fee-accrual mapping issue at BANK_DB; tracker EUR-2024-Q3-Q4'
FROM gen_calendar c
LEFT JOIN cash_balance cb ON cb.account_ref='GR_DE_OPERATING_1' AND cb.balance_date=c.calendar_date
                           AND cb.date_basis='VALUE_DATE' AND cb.includes_actual AND cb.includes_intraday
                           AND NOT cb.includes_confirmed AND NOT cb.includes_estimated
WHERE c.calendar_date IN (DATE '2024-07-31', DATE '2024-08-30', DATE '2024-09-30', DATE '2024-10-31', DATE '2024-11-29', DATE '2024-12-31');

-- One-off $1.2M intercompany leg mis-route
INSERT INTO gl_reconciliation (
    uuid, bank_account_ref, gl_account_ref, as_of_date,
    bank_balance, gl_balance, variance_amount, variance_currency, status, notes
)
SELECT gen_uuid('GLR_IC_2025_03'),'IHB_USD_CONCENTRATION','1110-IHB_USD_CONCENTRATION', DATE '2025-03-31',
       COALESCE(cb.amount, 50000000),
       COALESCE(cb.amount, 50000000) - 1200000.00,
       1200000.00,'USD','INVESTIGATING',
       'Mis-routed intercompany leg between IHB and GR_AU; resolved as DQ defect April 2025'
FROM ( SELECT amount FROM cash_balance
        WHERE account_ref='IHB_USD_CONCENTRATION' AND balance_date= DATE '2025-03-31'
          AND date_basis='VALUE_DATE' AND includes_actual AND includes_intraday
          AND NOT includes_confirmed AND NOT includes_estimated LIMIT 1 ) cb;

-- -----------------------------------------------------------------------------
-- 11.4 COUNTERPARTY_EXPOSURE — rebuilt from final cash balances
-- -----------------------------------------------------------------------------
TRUNCATE TABLE counterparty_exposure;

INSERT INTO counterparty_exposure (
    as_of_date, counterparty_bank_ref,
    deposits_amount, investments_amount, derivative_mtm_amount,
    total_exposure, reporting_currency, pct_of_total
)
WITH bank_deposits AS (
    SELECT br.bank_ref, c.calendar_date, SUM(cb.amount * COALESCE(fx.rate,1)) AS dep
    FROM cash_balance cb
    JOIN bank_account ba ON ba.code = cb.account_ref
    JOIN bank_branch br ON br.code = ba.branch_ref
    JOIN gen_calendar c ON c.calendar_date = cb.balance_date AND EXTRACT(DOW FROM c.calendar_date)=5
    LEFT JOIN fx_rate fx ON fx.rate_date = c.calendar_date AND fx.rate_type='SPOT'
                         AND fx.base_currency = cb.currency_code AND fx.quote_currency='USD'
    WHERE cb.date_basis='VALUE_DATE' AND cb.includes_actual AND cb.includes_intraday
      AND NOT cb.includes_confirmed AND NOT cb.includes_estimated AND cb.amount > 0
    GROUP BY br.bank_ref, c.calendar_date
),
bank_invest AS (
    SELECT i.issuer_bank_ref AS bank_ref, p.as_of_date,
           SUM(p.market_value * COALESCE(fx.rate,1)) AS inv
    FROM investment_position p JOIN investment_instrument i ON i.code = p.instrument_ref
    LEFT JOIN fx_rate fx ON fx.rate_date = p.as_of_date AND fx.rate_type='SPOT'
                         AND fx.base_currency = p.currency_code AND fx.quote_currency='USD'
    WHERE i.issuer_bank_ref IS NOT NULL
    GROUP BY i.issuer_bank_ref, p.as_of_date
),
bank_mtm AS (
    SELECT counterparty_bank_ref AS bank_ref, valuation_date, SUM(mtm_amount) AS mtm
    FROM derivative_mtm
    WHERE counterparty_bank_ref IS NOT NULL AND EXTRACT(DOW FROM valuation_date)=5
    GROUP BY counterparty_bank_ref, valuation_date
),
combined AS (
    SELECT COALESCE(d.calendar_date, i.as_of_date, m.valuation_date) AS as_of_date,
           COALESCE(d.bank_ref, i.bank_ref, m.bank_ref) AS bank_ref,
           COALESCE(d.dep,0) AS dep, COALESCE(i.inv,0) AS inv, COALESCE(m.mtm,0) AS mtm
    FROM bank_deposits d
    FULL OUTER JOIN bank_invest i ON i.bank_ref=d.bank_ref AND i.as_of_date=d.calendar_date
    FULL OUTER JOIN bank_mtm m    ON m.bank_ref=COALESCE(d.bank_ref,i.bank_ref)
                                  AND m.valuation_date=COALESCE(d.calendar_date,i.as_of_date)
)
SELECT as_of_date, bank_ref, dep, inv, mtm,
       dep + inv + mtm AS total_exposure, 'USD',
       ROUND((100.0 * (dep+inv+mtm) / NULLIF(SUM(dep+inv+mtm) OVER (PARTITION BY as_of_date),0))::NUMERIC, 4)
FROM combined WHERE bank_ref IS NOT NULL;

-- VERIFY
SELECT 'WORKING_CAPITAL_METRIC' AS t, COUNT(*) FROM working_capital_metric
UNION ALL SELECT 'GL_BALANCE', COUNT(*) FROM gl_balance
UNION ALL SELECT 'GL_RECONCILIATION', COUNT(*) FROM gl_reconciliation
UNION ALL SELECT 'GL_RECON_INVESTIGATING', COUNT(*) FROM gl_reconciliation WHERE status='INVESTIGATING'
UNION ALL SELECT 'COUNTERPARTY_EXPOSURE', COUNT(*) FROM counterparty_exposure;
