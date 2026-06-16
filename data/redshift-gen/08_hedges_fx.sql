-- =============================================================================
-- 08_hedges_fx.sql (Redshift) — FX_FORWARD, HEDGE_RELATIONSHIP, FX_EXPOSURE_FORECAST,
-- DERIVATIVE_MTM, HEDGE_DEDESIGNATION
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 8.1 FX_FORWARD — ~1,800 deals
-- -----------------------------------------------------------------------------
TRUNCATE TABLE fx_forward;

INSERT INTO fx_forward (
    uuid, deal_id, company_ref, counterparty_bank_ref,
    trade_date, value_date,
    buy_currency, buy_amount,
    sell_currency, sell_amount,
    forward_rate, spot_at_trade, status
)
WITH base AS (
    SELECT
        n AS seq,
        DATEADD(day, MOD(ABS(FNV_HASH(CAST(n AS VARCHAR))),1101), DATE '2023-05-01')::DATE AS trade_dt,
        (CASE
            WHEN gen_rand(CAST(n AS VARCHAR))<0.40 THEN 30
            WHEN gen_rand(CAST(n AS VARCHAR))<0.75 THEN 90
            WHEN gen_rand(CAST(n AS VARCHAR))<0.90 THEN 180
            ELSE 365 END) AS tenor_days,
        (CASE MOD(ABS(FNV_HASH('FXE|'||CAST(n AS VARCHAR))),11)
           WHEN 0 THEN 'CNY' WHEN 1 THEN 'EUR' WHEN 2 THEN 'JPY' WHEN 3 THEN 'GBP'
           WHEN 4 THEN 'MXN' WHEN 5 THEN 'BRL' WHEN 6 THEN 'AUD' WHEN 7 THEN 'HKD'
           WHEN 8 THEN 'SGD' WHEN 9 THEN 'KRW' ELSE 'CAD' END) AS exp_ccy,
        (CASE MOD(ABS(FNV_HASH('FXCP|'||CAST(n AS VARCHAR))),10)
           WHEN 0 THEN 'BANK_HSBC' WHEN 1 THEN 'BANK_HSBC' WHEN 2 THEN 'BANK_HSBC'
           WHEN 3 THEN 'BANK_BNP'  WHEN 4 THEN 'BANK_BNP'  WHEN 5 THEN 'BANK_CITI'
           WHEN 6 THEN 'BANK_CITI' WHEN 7 THEN 'BANK_JPM'  WHEN 8 THEN 'BANK_MUFG'
           ELSE 'BANK_SCB' END) AS cp,
        (CASE MOD(ABS(FNV_HASH('FXCO|'||CAST(n AS VARCHAR))),6)
           WHEN 0 THEN 'GR_US_INC' WHEN 1 THEN 'GR_TREASURY' WHEN 2 THEN 'GR_HOLDINGS'
           WHEN 3 THEN 'GR_EU_BV' WHEN 4 THEN 'GR_APAC_PTE' ELSE 'GR_LATAM_SA' END) AS co
    FROM gen_numbers WHERE n < 1800
), enriched AS (
    SELECT b.*,
        DATEADD(day, b.tenor_days, b.trade_dt) AS value_dt,
        ROUND((EXP(13 + 1.0 * gen_normal('FX_'||CAST(b.seq AS VARCHAR)))
               * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC,2) AS notional_usd
    FROM base b
), priced AS (
    SELECT e.*,
        spot.rate AS spot_rate,
        ROUND((spot.rate * (1 + (MOD(ABS(FNV_HASH('FXFWD|'||CAST(e.seq AS VARCHAR))),81)-40)/10000.0))::NUMERIC, 10) AS fwd_rate
    FROM enriched e
    LEFT JOIN fx_rate spot ON spot.rate_date = e.trade_dt
                           AND spot.base_currency = 'USD'
                           AND spot.quote_currency = e.exp_ccy
                           AND spot.rate_type = 'SPOT'
)
SELECT
    gen_uuid('FXF_'||CAST(seq AS VARCHAR)), 'FXF-'||LPAD(CAST(seq AS VARCHAR),7,'0'),
    co, cp, trade_dt, value_dt,
    exp_ccy, ROUND((notional_usd * COALESCE(spot_rate,1))::NUMERIC, 2),
    'USD', notional_usd,
    fwd_rate, spot_rate,
    CASE
      WHEN value_dt > DATE '2026-05-04' THEN 'OPEN'
      WHEN gen_rand('FX_S_'||CAST(seq AS VARCHAR)) < 0.01 THEN 'CANCELLED'
      ELSE 'SETTLED'
    END
FROM priced;

-- -----------------------------------------------------------------------------
-- 8.2 HEDGE_RELATIONSHIP
-- -----------------------------------------------------------------------------
TRUNCATE TABLE hedge_relationship;

INSERT INTO hedge_relationship (
    uuid, code, company_ref, hedge_type, hedged_item_type,
    hedged_currency, designation_date, dedesignation_date,
    instrument_type, instrument_ref,
    notional_amount, notional_currency,
    effectiveness_method, status
)
SELECT
    gen_uuid('HR_'||f.deal_id),
    'HR-'||SUBSTRING(f.deal_id,5),
    f.company_ref,
    CASE WHEN MOD(ABS(FNV_HASH(f.deal_id)),100) < 60 THEN 'CASH_FLOW'
         WHEN MOD(ABS(FNV_HASH(f.deal_id)),100) < 90 THEN 'FAIR_VALUE'
         ELSE 'NET_INVESTMENT' END,
    (CASE MOD(ABS(FNV_HASH(f.deal_id||'|ITEM')),5)
       WHEN 0 THEN 'FORECAST_PURCHASE' WHEN 1 THEN 'FORECAST_SALE'
       WHEN 2 THEN 'RECOGNIZED_AP' WHEN 3 THEN 'RECOGNIZED_AR' ELSE 'NET_INVESTMENT' END),
    f.buy_currency, f.trade_date,
    CASE WHEN MOD(ABS(FNV_HASH(f.deal_id)), 100) < 3 AND f.trade_date BETWEEN DATE '2024-04-01' AND DATE '2024-09-30'
         THEN DATEADD(day,60,f.trade_date) ELSE NULL END,
    'FX_FORWARD', f.deal_id,
    f.buy_amount, f.buy_currency,
    'DOLLAR_OFFSET',
    CASE WHEN MOD(ABS(FNV_HASH(f.deal_id)), 100) < 3 AND f.trade_date BETWEEN DATE '2024-04-01' AND DATE '2024-09-30'
         THEN 'DEDESIGNATED'
         WHEN f.status='CANCELLED' THEN 'TERMINATED'
         ELSE 'ACTIVE' END
FROM fx_forward f
WHERE MOD(ABS(FNV_HASH(f.deal_id)), 3) = 0;

TRUNCATE TABLE hedge_dedesignation;
INSERT INTO hedge_dedesignation (uuid, hedge_ref, company_ref, currency_ref, dedesignation_date, amount, reason)
SELECT gen_uuid('HDD_'||hr.code), hr.code, hr.company_ref, hr.hedged_currency,
       hr.dedesignation_date, hr.notional_amount,
       (CASE MOD(ABS(FNV_HASH(hr.code)),4)
          WHEN 0 THEN 'Forecast no longer probable' WHEN 1 THEN 'Underlying instrument terminated'
          WHEN 2 THEN 'Restructuring' ELSE 'Effectiveness failed' END)
FROM hedge_relationship hr WHERE hr.dedesignation_date IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 8.3 FX_EXPOSURE_FORECAST
-- -----------------------------------------------------------------------------
TRUNCATE TABLE fx_exposure_forecast;

INSERT INTO fx_exposure_forecast (
    uuid, company_ref, forecast_period, tenor_bucket,
    exposure_currency, functional_currency, gross_exposure_amount,
    direction, source, snapshot_date
)
SELECT
    gen_uuid('FXE_'||co.code||'_'||CAST(c.calendar_date AS VARCHAR)||'_'||p.tenor||'_'||p.ccy||'_'||p.dir),
    co.code, c.calendar_date, p.tenor, p.ccy,
    CASE co.code WHEN 'GR_HOLDINGS' THEN 'GBP' WHEN 'GR_TREASURY' THEN 'GBP'
                 WHEN 'GR_US_INC' THEN 'USD' WHEN 'GR_EU_BV' THEN 'EUR'
                 WHEN 'GR_APAC_PTE' THEN 'SGD' WHEN 'GR_LATAM_SA' THEN 'MXN'
                 ELSE 'USD' END,
    ROUND((EXP(13 + 1.0 * gen_normal(co.code||'_'||CAST(c.calendar_date AS VARCHAR)||'_'||p.ccy||'_'||p.dir))
            * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
    p.dir, p.source,
    LAST_DAY(c.calendar_date)
FROM company co
CROSS JOIN gen_calendar c
CROSS JOIN (
    SELECT '0-1M' AS tenor,'CNY' AS ccy,'SHORT' AS dir,'FORECAST_PURCHASES' AS source UNION ALL
    SELECT '1-3M','CNY','SHORT','FORECAST_PURCHASES' UNION ALL
    SELECT '3-6M','CNY','SHORT','FORECAST_PURCHASES' UNION ALL
    SELECT '0-1M','EUR','LONG' ,'FORECAST_SALES' UNION ALL
    SELECT '0-1M','USD','SHORT','AP' UNION ALL
    SELECT '1-3M','USD','SHORT','AP' UNION ALL
    SELECT '0-1M','JPY','LONG' ,'AR' UNION ALL
    SELECT '1-3M','JPY','LONG' ,'AR' UNION ALL
    SELECT '0-1M','MXN','LONG' ,'INTERCOMPANY_LOAN' UNION ALL
    SELECT '1-3M','BRL','SHORT','AP'
) p
WHERE co.code IN ('GR_HOLDINGS','GR_TREASURY','GR_US_INC','GR_EU_BV','GR_APAC_PTE','GR_LATAM_SA')
  AND c.calendar_date = LAST_DAY(c.calendar_date)
  AND c.is_weekday;

-- -----------------------------------------------------------------------------
-- 8.4 DERIVATIVE_MTM
-- -----------------------------------------------------------------------------
TRUNCATE TABLE derivative_mtm;

INSERT INTO derivative_mtm (
    uuid, instrument_type, instrument_ref, counterparty_bank_ref,
    company_ref, valuation_date, mtm_amount, mtm_currency
)
SELECT
    gen_uuid('MTM_'||f.deal_id||'_'||CAST(c.calendar_date AS VARCHAR)),
    'FX_FORWARD', f.deal_id, f.counterparty_bank_ref, f.company_ref, c.calendar_date,
    ROUND(((COALESCE(spot.rate, f.spot_at_trade) - f.forward_rate) * f.sell_amount)::NUMERIC, 2),
    'USD'
FROM fx_forward f
JOIN gen_calendar c ON c.calendar_date BETWEEN f.trade_date AND LEAST(f.value_date, DATE '2026-05-04')
                   AND ((f.status='OPEN' AND c.is_weekday)
                        OR (f.status='SETTLED' AND EXTRACT(DOW FROM c.calendar_date)=5))
LEFT JOIN fx_rate spot ON spot.rate_date = c.calendar_date
                       AND spot.base_currency = 'USD'
                       AND spot.quote_currency = f.buy_currency
                       AND spot.rate_type = 'SPOT';

-- -----------------------------------------------------------------------------
-- 8.5 FX settlement cash-flow legs
-- -----------------------------------------------------------------------------
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_FX_BUY_'||f.deal_id),
    CASE WHEN f.company_ref='GR_TREASURY' THEN 'IHB_'||f.buy_currency||'_CONCENTRATION'
         ELSE COALESCE((SELECT MIN(code) FROM bank_account WHERE company_ref=f.company_ref AND currency_ref=f.buy_currency),
                       'IHB_'||f.buy_currency||'_CONCENTRATION') END,
    'FX_SETTLE_BUY','BC_TREAS','CONFIRMED',
    f.value_date, f.value_date,
    f.buy_amount, f.buy_currency, f.buy_amount,
    f.buy_amount, f.buy_currency, 1.0,
    f.counterparty_bank_ref, 'FX forward buy leg', f.deal_id, 'WIRE'
FROM fx_forward f
WHERE f.status='SETTLED'
UNION ALL
SELECT
    gen_uuid('CF_FX_SELL_'||f.deal_id),
    CASE WHEN f.company_ref='GR_TREASURY' THEN 'IHB_'||f.sell_currency||'_DISBURSEMENT'
         ELSE COALESCE((SELECT MIN(code) FROM bank_account WHERE company_ref=f.company_ref AND currency_ref=f.sell_currency),
                       'IHB_USD_DISBURSEMENT') END,
    'FX_SETTLE_SELL','BC_TREAS','CONFIRMED',
    f.value_date, f.value_date,
    -f.sell_amount, f.sell_currency, -f.sell_amount,
    -f.sell_amount, f.sell_currency, 1.0,
    f.counterparty_bank_ref, 'FX forward sell leg', f.deal_id, 'WIRE'
FROM fx_forward f
WHERE f.status='SETTLED';

-- -----------------------------------------------------------------------------
-- 8.6 COUNTERPARTY_EXPOSURE — initial pass (rebuilt in script 11 from final CB)
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
      AND NOT cb.includes_confirmed AND NOT cb.includes_estimated
      AND cb.amount > 0
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
    FULL OUTER JOIN bank_invest i  ON i.bank_ref = d.bank_ref AND i.as_of_date  = d.calendar_date
    FULL OUTER JOIN bank_mtm m     ON m.bank_ref = COALESCE(d.bank_ref, i.bank_ref) AND m.valuation_date = COALESCE(d.calendar_date, i.as_of_date)
)
SELECT
    as_of_date, bank_ref, dep, inv, mtm,
    dep + inv + mtm AS total_exposure,
    'USD',
    ROUND((100.0 * (dep+inv+mtm) / NULLIF(SUM(dep+inv+mtm) OVER (PARTITION BY as_of_date),0))::NUMERIC,4)
FROM combined
WHERE bank_ref IS NOT NULL;

-- VERIFY
SELECT 'FX_FORWARD' AS t, COUNT(*) FROM fx_forward
UNION ALL SELECT 'HEDGE_RELATIONSHIP', COUNT(*) FROM hedge_relationship
UNION ALL SELECT 'HEDGE_DEDESIGNATION', COUNT(*) FROM hedge_dedesignation
UNION ALL SELECT 'FX_EXPOSURE_FORECAST', COUNT(*) FROM fx_exposure_forecast
UNION ALL SELECT 'DERIVATIVE_MTM', COUNT(*) FROM derivative_mtm
UNION ALL SELECT 'COUNTERPARTY_EXPOSURE', COUNT(*) FROM counterparty_exposure;
