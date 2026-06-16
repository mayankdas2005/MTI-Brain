-- =============================================================================
-- 03_static_config.sql (Redshift) — sweeps, liquidity policy, credit facilities,
-- investment instruments, stress scenarios, fee rate cards, GL accounts
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 3.1 SWEEP_INSTRUCTION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE sweep_instruction;

INSERT INTO sweep_instruction (
    uuid, code, source_account_ref, target_account_ref, sweep_type, direction,
    target_balance, target_balance_ccy, schedule_cron, priority, active, effective_from
)
SELECT gen_uuid('SW_'||src.code),
       'SW_'||src.code, src.code,
       CASE
         WHEN src.currency_ref='USD' THEN 'USA_RGNL_CONCENTRATION'
         WHEN src.currency_ref='EUR' THEN 'EUR_RGNL_CONCENTRATION'
         WHEN src.currency_ref='GBP' THEN 'IHB_GBP_CONCENTRATION'
         WHEN src.currency_ref='SGD' THEN 'APC_RGNL_CONCENTRATION'
         WHEN src.currency_ref='MXN' THEN 'LAT_RGNL_CONCENTRATION'
         WHEN src.currency_ref='JPY' THEN 'IHB_JPY_CONCENTRATION'
         WHEN src.currency_ref='AUD' THEN 'IHB_AUD_CONCENTRATION'
         WHEN src.currency_ref='CAD' THEN 'IHB_CAD_CONCENTRATION'
         WHEN src.currency_ref='BRL' THEN 'IHB_BRL_CONCENTRATION'
         ELSE 'IHB_USD_CONCENTRATION'
       END,
       'ZBA','UP',
       0, src.currency_ref, 'EOD', 1, TRUE, DATE '2022-01-01'
FROM bank_account src
WHERE src.account_purpose='OPERATING' AND NOT src.closed_account;

INSERT INTO sweep_instruction (
    uuid, code, source_account_ref, target_account_ref, sweep_type, direction,
    target_balance, target_balance_ccy, schedule_cron, priority, active, effective_from
)
SELECT gen_uuid('SW_'||src),'SW_'||src, src, tgt, 'TARGET','UP', mb*1.2, ccy,'EOD', 2, TRUE, DATE '2022-01-01'
FROM (
    SELECT 'USA_RGNL_CONCENTRATION' AS src,'IHB_USD_CONCENTRATION' AS tgt,'USD' AS ccy,5000000::NUMERIC AS mb UNION ALL
    SELECT 'EUR_RGNL_CONCENTRATION','IHB_EUR_CONCENTRATION','EUR',4000000 UNION ALL
    SELECT 'APC_RGNL_CONCENTRATION','IHB_SGD_CONCENTRATION','SGD',3000000 UNION ALL
    SELECT 'LAT_RGNL_CONCENTRATION','IHB_MXN_CONCENTRATION','MXN',60000000
) t;

INSERT INTO sweep_instruction (
    uuid, code, source_account_ref, target_account_ref, sweep_type, direction,
    target_balance_ccy, schedule_cron, priority, active, effective_from
)
VALUES (gen_uuid('SW_EUR_NOTIONAL'),'SW_EUR_NOTIONAL','EUR_RGNL_CONCENTRATION','EUR_RGNL_CONCENTRATION',
        'NOTIONAL_POOL','TWO_WAY','EUR','EOD',5,TRUE, DATE '2022-01-01');

INSERT INTO sweep_instruction (
    uuid, code, source_account_ref, target_account_ref, sweep_type, direction,
    threshold_amount, target_balance_ccy, schedule_cron, priority, active, effective_from
)
VALUES (gen_uuid('SW_USD_THRESHOLD'),'SW_USD_THRESHOLD','USA_RGNL_CONCENTRATION','IHB_USD_INVESTMENT',
        'THRESHOLD','UP',10000000,'USD','EOD',3,TRUE, DATE '2023-01-01');

INSERT INTO sweep_instruction (
    uuid, code, source_account_ref, target_account_ref, sweep_type, direction,
    target_balance, target_balance_ccy, schedule_cron, priority, active, effective_from, effective_to
)
VALUES (gen_uuid('SW_DEACT_HK'),'SW_DEACT_HK','GR_HK_OP_2','APC_RGNL_CONCENTRATION',
        'TARGET','UP',500000,'HKD','EOD',4,FALSE, DATE '2022-01-01', DATE '2024-11-30');

-- -----------------------------------------------------------------------------
-- 3.2 LIQUIDITY_POLICY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE liquidity_policy;
INSERT INTO liquidity_policy (uuid, company_ref, company_group_ref, policy_type, threshold_amount, threshold_currency, threshold_pct, effective_from, effective_to, description) VALUES
    (gen_uuid('LP_GROUP_MIN_LIQ_V1'),NULL,'GROUP_GLOBAL','MIN_LIQUIDITY',350000000,'USD',NULL, DATE '2023-01-01', DATE '2024-06-30','Group minimum cash buffer'),
    (gen_uuid('LP_GROUP_MIN_LIQ_V2'),NULL,'GROUP_GLOBAL','MIN_LIQUIDITY',400000000,'USD',NULL, DATE '2024-07-01',NULL,'Group minimum cash buffer (raised)'),
    (gen_uuid('LP_AMER_MIN_OPS'),NULL,'GROUP_AMER','MIN_OPERATING_CASH',100000000,'USD',NULL, DATE '2023-01-01',NULL,'Americas operating cash floor'),
    (gen_uuid('LP_EMEA_MIN_OPS'),NULL,'GROUP_EMEA','MIN_OPERATING_CASH',90000000,'EUR',NULL, DATE '2023-01-01',NULL,'EMEA operating cash floor'),
    (gen_uuid('LP_APAC_MIN_OPS'),NULL,'GROUP_APAC','MIN_OPERATING_CASH',60000000,'USD',NULL, DATE '2023-01-01',NULL,'APAC operating cash floor'),
    (gen_uuid('LP_CP_TIER1'),NULL,'GROUP_GLOBAL','MAX_COUNTERPARTY_PCT',NULL,NULL,25.0, DATE '2023-01-01',NULL,'TIER_1 counterparty cap'),
    (gen_uuid('LP_CP_TIER2'),NULL,'GROUP_GLOBAL','MAX_COUNTERPARTY_PCT',NULL,NULL,12.0, DATE '2023-01-01',NULL,'TIER_2 counterparty cap'),
    (gen_uuid('LP_CP_TIER3'),NULL,'GROUP_GLOBAL','MAX_COUNTERPARTY_PCT',NULL,NULL,5.0,  DATE '2023-01-01',NULL,'TIER_3 counterparty cap'),
    (gen_uuid('LP_TENOR_MAX'),NULL,'GROUP_GLOBAL','MAX_INSTRUMENT_TENOR',NULL,NULL,NULL, DATE '2023-01-01', DATE '2024-12-31','Max 365 days investment tenor'),
    (gen_uuid('LP_TENOR_MAX_V2'),NULL,'GROUP_GLOBAL','MAX_INSTRUMENT_TENOR',NULL,NULL,NULL, DATE '2025-01-01',NULL,'Max 730 days investment tenor (Treasury exception)');

-- -----------------------------------------------------------------------------
-- 3.3 CREDIT_FACILITY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE credit_facility;
INSERT INTO credit_facility (
    uuid, code, facility_type, company_ref, lender_bank_ref, currency_ref,
    commitment_amount, start_date, maturity_date, benchmark_code, spread_bps, commitment_fee_bps, status
) VALUES
    (gen_uuid('CF_RCF_USD_2B'),'CF_RCF_USD_2B','REVOLVER','GR_HOLDINGS','BANK_JPM','USD',2000000000, DATE '2023-06-15', DATE '2027-06-15','SOFR',125, 25,'ACTIVE'),
    (gen_uuid('CF_RCF_EUR_500M'),'CF_RCF_EUR_500M','REVOLVER','GR_EU_BV','BANK_BNP','EUR',500000000, DATE '2023-06-15', DATE '2026-09-15','ESTR',110, 22,'ACTIVE'),
    (gen_uuid('CF_TERM_USD_300M'),'CF_TERM_USD_300M','TERM_LOAN','GR_HOLDINGS','BANK_HSBC','USD',300000000, DATE '2023-08-01', DATE '2028-08-01','SOFR',175,NULL,'ACTIVE'),
    (gen_uuid('CF_OD_GBP_50M'),'CF_OD_GBP_50M','OVERDRAFT','GR_GB','BANK_HSBC','GBP',50000000, DATE '2022-01-01', DATE '2027-01-01','SONIA',225,NULL,'ACTIVE'),
    (gen_uuid('CF_CP_USD_750M'),'CF_CP_USD_750M','COMMERCIAL_PAPER','GR_HOLDINGS','BANK_CITI','USD',750000000, DATE '2023-01-01', DATE '2027-12-31','SOFR',35,NULL,'ACTIVE');

INSERT INTO credit_facility (
    uuid, code, facility_type, company_ref, lender_bank_ref, currency_ref,
    commitment_amount, start_date, maturity_date, benchmark_code, spread_bps, status
)
SELECT gen_uuid('CF_IC_'||c.code), 'CF_IC_'||c.code, 'INTERCOMPANY',
       c.code, 'BANK_IHB',
       CASE c.code WHEN 'GR_JP' THEN 'JPY' WHEN 'GR_GB' THEN 'GBP'
                   WHEN 'GR_HK' THEN 'HKD' WHEN 'GR_AU' THEN 'AUD'
                   WHEN 'GR_BR' THEN 'BRL' WHEN 'GR_MX' THEN 'MXN'
                   WHEN 'GR_AE' THEN 'AED' WHEN 'GR_SE' THEN 'SEK'
                   WHEN 'GR_PL' THEN 'PLN' WHEN 'GR_KR' THEN 'KRW'
                   WHEN 'GR_CA' THEN 'CAD' ELSE 'EUR' END,
       100000000, DATE '2023-01-01', DATE '2028-12-31','SOFR',300,'ACTIVE'
FROM company c
WHERE c.code IN ('GR_JP','GR_GB','GR_HK','GR_AU','GR_BR','GR_MX','GR_AE','GR_SE','GR_PL','GR_KR','GR_CA','GR_DE');

-- -----------------------------------------------------------------------------
-- 3.4 INVESTMENT_INSTRUMENT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE investment_instrument;

-- 30 MMFs
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, issuer_bank_ref, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_MMF_'||n), 'INV_MMF_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'MMF',
       (CASE MOD(ABS(FNV_HASH('MMF_ISS_'||CAST(n AS VARCHAR))),5)
          WHEN 0 THEN 'JPM' WHEN 1 THEN 'Goldman Sachs' WHEN 2 THEN 'BlackRock'
          WHEN 3 THEN 'BNP' ELSE 'HSBC' END) || ' MMF',
       (CASE MOD(ABS(FNV_HASH('MMF_BANK_'||CAST(n AS VARCHAR))),5)
          WHEN 0 THEN 'BANK_JPM' WHEN 1 THEN 'BANK_HSBC' WHEN 2 THEN 'BANK_BNP'
          WHEN 3 THEN 'BANK_CITI' ELSE 'BANK_HSBC' END),
       (CASE MOD(ABS(FNV_HASH('MMF_CCY_'||CAST(n AS VARCHAR))),4)
          WHEN 0 THEN 'USD' WHEN 1 THEN 'EUR' WHEN 2 THEN 'GBP' ELSE 'SGD' END),
       NULL, DATE '2018-01-01', NULL, 'AAA-mf'
FROM gen_numbers WHERE n < 30;

-- 25 Time Deposits
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, issuer_bank_ref, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_TD_'||n), 'INV_TD_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'TIME_DEPOSIT',
       (CASE MOD(ABS(FNV_HASH('TD_ISS_'||CAST(n AS VARCHAR))),5)
          WHEN 0 THEN 'JPM' WHEN 1 THEN 'HSBC' WHEN 2 THEN 'BNP' WHEN 3 THEN 'MUFG' ELSE 'Citi' END)||' TD',
       (CASE MOD(ABS(FNV_HASH('TD_BANK_'||CAST(n AS VARCHAR))),5)
          WHEN 0 THEN 'BANK_JPM' WHEN 1 THEN 'BANK_HSBC' WHEN 2 THEN 'BANK_BNP'
          WHEN 3 THEN 'BANK_MUFG' ELSE 'BANK_CITI' END),
       (CASE MOD(ABS(FNV_HASH('TD_CCY_'||CAST(n AS VARCHAR))),3)
          WHEN 0 THEN 'USD' WHEN 1 THEN 'EUR' ELSE 'GBP' END),
       (MOD(ABS(FNV_HASH('TD_CPN_'||CAST(n AS VARCHAR))),201)+350)/100.0,
       DATEADD(day, MOD(ABS(FNV_HASH('TD_ISS_DT_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01'),
       DATEADD(day, MOD(ABS(FNV_HASH('TD_MAT_'||CAST(n AS VARCHAR))),151)+30,
               DATEADD(day, MOD(ABS(FNV_HASH('TD_ISS_DT2_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01')),
       'A-1'
FROM gen_numbers WHERE n < 25;

-- 20 CDs
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, issuer_bank_ref, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_CD_'||n), 'INV_CD_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'CD', 'US Bank '||CAST(n AS VARCHAR)||' CD','BANK_BAC','USD',
       (MOD(ABS(FNV_HASH('CD_CPN_'||CAST(n AS VARCHAR))),181)+380)/100.0,
       DATEADD(day, MOD(ABS(FNV_HASH('CD_ISS_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01'),
       DATEADD(day, MOD(ABS(FNV_HASH('CD_MAT_'||CAST(n AS VARCHAR))),151)+30,
               DATEADD(day, MOD(ABS(FNV_HASH('CD_ISS2_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01')),
       'A-1+'
FROM gen_numbers WHERE n < 20;

-- 15 US Treasuries
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_TBL_'||n), 'INV_TBL_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'TREASURY','US Treasury','USD',
       (MOD(ABS(FNV_HASH('TBL_CPN_'||CAST(n AS VARCHAR))),171)+350)/100.0,
       DATEADD(day, MOD(ABS(FNV_HASH('TBL_ISS_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01'),
       DATEADD(day,
               (CASE MOD(ABS(FNV_HASH('TBL_TEN_'||CAST(n AS VARCHAR))),4) WHEN 0 THEN 28 WHEN 1 THEN 91 WHEN 2 THEN 182 ELSE 365 END),
               DATEADD(day, MOD(ABS(FNV_HASH('TBL_ISS2_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01')),
       'AAA'
FROM gen_numbers WHERE n < 15;

-- 10 Eurozone treasuries
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_EZ_'||n), 'INV_EZ_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'TREASURY',
       (CASE MOD(ABS(FNV_HASH('EZ_ISS_'||CAST(n AS VARCHAR))),4)
          WHEN 0 THEN 'Bundesrepublik Deutschland' WHEN 1 THEN 'Republique Francaise'
          WHEN 2 THEN 'Repubblica Italiana' ELSE 'Reino de Espana' END),
       'EUR',
       (MOD(ABS(FNV_HASH('EZ_CPN_'||CAST(n AS VARCHAR))),161)+280)/100.0,
       DATEADD(day, MOD(ABS(FNV_HASH('EZ_ISS_DT_'||CAST(n AS VARCHAR))),801), DATE '2023-05-01'),
       DATEADD(day, MOD(ABS(FNV_HASH('EZ_MAT_'||CAST(n AS VARCHAR))),541)+180,
               DATEADD(day, MOD(ABS(FNV_HASH('EZ_ISS_DT2_'||CAST(n AS VARCHAR))),801), DATE '2023-05-01')),
       'AA'
FROM gen_numbers WHERE n < 10;

-- 10 CP
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_CP_'||n), 'INV_CP_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'COMMERCIAL_PAPER','Corp Issuer '||CAST(n AS VARCHAR),
       (CASE MOD(ABS(FNV_HASH('CP_CCY_'||CAST(n AS VARCHAR))),2) WHEN 0 THEN 'USD' ELSE 'EUR' END),
       (MOD(ABS(FNV_HASH('CP_CPN_'||CAST(n AS VARCHAR))),161)+420)/100.0,
       DATEADD(day, MOD(ABS(FNV_HASH('CP_ISS_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01'),
       DATEADD(day, MOD(ABS(FNV_HASH('CP_MAT_'||CAST(n AS VARCHAR))),61)+30,
               DATEADD(day, MOD(ABS(FNV_HASH('CP_ISS2_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01')),
       'A-1'
FROM gen_numbers WHERE n < 10;

-- 8 Repos
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, issuer_bank_ref, currency_ref, coupon_rate, issue_date, maturity_date, rating)
SELECT gen_uuid('INV_REPO_'||n), 'INV_REPO_'||LPAD(CAST(n AS VARCHAR),4,'0'),
       'REPO','Repo Counterparty '||CAST(n AS VARCHAR),
       (CASE MOD(ABS(FNV_HASH('REPO_BANK_'||CAST(n AS VARCHAR))),3)
          WHEN 0 THEN 'BANK_JPM' WHEN 1 THEN 'BANK_CITI' ELSE 'BANK_HSBC' END),
       'USD',
       (MOD(ABS(FNV_HASH('REPO_CPN_'||CAST(n AS VARCHAR))),141)+380)/100.0,
       DATEADD(day, MOD(ABS(FNV_HASH('REPO_ISS_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01'),
       DATEADD(day, MOD(ABS(FNV_HASH('REPO_MAT_'||CAST(n AS VARCHAR))),24)+7,
               DATEADD(day, MOD(ABS(FNV_HASH('REPO_ISS2_'||CAST(n AS VARCHAR))),1001), DATE '2023-05-01')),
       'A-1+'
FROM gen_numbers WHERE n < 8;

-- 2 longer-dated bonds
INSERT INTO investment_instrument (uuid, code, instrument_type, issuer_name, currency_ref, coupon_rate, issue_date, maturity_date, rating)
VALUES
    (gen_uuid('INV_BOND_001'),'INV_BOND_001','BOND','Apple Inc 3y Note','USD',4.50, DATE '2024-03-01', DATE '2027-03-01','AA+'),
    (gen_uuid('INV_BOND_002'),'INV_BOND_002','BOND','Microsoft 2y Note','USD',4.20, DATE '2024-09-15', DATE '2026-09-15','AAA');

-- -----------------------------------------------------------------------------
-- 3.5 STRESS_SCENARIO
-- -----------------------------------------------------------------------------
TRUNCATE TABLE stress_scenario;
INSERT INTO stress_scenario (uuid, code, description, scenario_type, parameters, created_at) VALUES
    (gen_uuid('STR_AR_DROP_20'),'STR_AR_DROP_20','AR receipts drop 20% for 30 days','RECEIPTS_DROP',
        JSON_PARSE('{"receipts_drop_pct":0.20,"duration_days":30}'),'2023-01-15'::TIMESTAMPTZ),
    (gen_uuid('STR_AR_DROP_30_60D'),'STR_AR_DROP_30_60D','AR receipts drop 30% for 60 days','RECEIPTS_DROP',
        JSON_PARSE('{"receipts_drop_pct":0.30,"duration_days":60}'),'2023-01-15'::TIMESTAMPTZ),
    (gen_uuid('STR_FX_USD_PLUS_10'),'STR_FX_USD_PLUS_10','USD strengthens 10% across pairs','FX_SHOCK',
        JSON_PARSE('{"shock_pct":0.10,"direction":"USD_UP"}'),'2023-01-15'::TIMESTAMPTZ),
    (gen_uuid('STR_RATE_PLUS_200'),'STR_RATE_PLUS_200','Rates +200bp parallel shift','RATE_SHOCK',
        JSON_PARSE('{"bp_shift":200}'),'2023-01-15'::TIMESTAMPTZ),
    (gen_uuid('STR_TOP_CUSTOMER_DEFAULT'),'STR_TOP_CUSTOMER_DEFAULT','Top-3 wholesale customers default','CUSTOMER_DEFAULT',
        JSON_PARSE('{"top_n":3,"recovery_pct":0.0}'),'2023-01-15'::TIMESTAMPTZ),
    (gen_uuid('STR_BANK_DOWNGRADE'),'STR_BANK_DOWNGRADE','TIER_2 counterparty downgraded','BANK_DOWNGRADE',
        JSON_PARSE('{"downgrade_steps":2,"redirect_within_days":5}'),'2023-01-15'::TIMESTAMPTZ);

-- -----------------------------------------------------------------------------
-- 3.6 FEE_RATE_CARD
-- -----------------------------------------------------------------------------
TRUNCATE TABLE fee_rate_card;
INSERT INTO fee_rate_card (uuid, bank_ref, service_code, company_ref, negotiated_rate, rate_unit, currency_code, effective_from, effective_to)
SELECT gen_uuid('FRC_'||b.code||'_'||s.code||'_v1'),
       b.code, s.code, NULL,
       CASE s.code
         WHEN '100' THEN 25.00 WHEN '150' THEN 5.00 WHEN '250' THEN 12.00 WHEN '251' THEN 35.00
         WHEN '252' THEN 8.00 WHEN '260' THEN 0.18 WHEN '261' THEN 0.12 WHEN '270' THEN 0.85
         WHEN '271' THEN 0.95 WHEN '300' THEN 0.50 WHEN '400' THEN 0.45 WHEN '410' THEN 0.30
         WHEN '500' THEN 4.00 WHEN '501' THEN 8.00 WHEN '600' THEN 25.00 WHEN '601' THEN 18.00
         WHEN '700' THEN 30.00 WHEN '800' THEN 25.00 WHEN '900' THEN 40.00
         WHEN 'AAA' THEN 1500.00 WHEN 'AAB' THEN 2500.00 WHEN 'AAC' THEN 750.00
         WHEN 'AAD' THEN 200.00 WHEN 'AAE' THEN 150.00 WHEN 'AAF' THEN 350.00
       END
       * (1 + 0.10*gen_normal(b.code||'_'||s.code)),
       CASE s.category
         WHEN 'ACCOUNT_MAINT' THEN 'PER_MONTH'
         WHEN 'POOL'          THEN 'PER_MONTH'
         WHEN 'REPORTING'     THEN 'PER_MONTH'
         WHEN 'FX'            THEN 'PER_USD_1000'
         ELSE                      'PER_TRANSACTION'
       END,
       'USD', DATE '2022-01-01', DATE '2024-06-30'
FROM bank b CROSS JOIN bank_service_type s
WHERE b.code <> 'BANK_IHB';

INSERT INTO fee_rate_card (uuid, bank_ref, service_code, company_ref, negotiated_rate, rate_unit, currency_code, effective_from, effective_to)
SELECT gen_uuid('FRC_'||bank_ref||'_'||service_code||'_v2'),
       bank_ref, service_code, NULL,
       negotiated_rate * CASE WHEN service_code IN ('251','250') THEN 0.80 ELSE 0.95 END,
       rate_unit, currency_code, DATE '2024-07-01', NULL
FROM fee_rate_card WHERE effective_to= DATE '2024-06-30';

-- -----------------------------------------------------------------------------
-- 3.7 GL_ACCOUNT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE gl_account;

INSERT INTO gl_account (uuid, code, description, chart_of_accounts, company_ref, bank_account_ref, account_class, currency_ref)
SELECT gen_uuid('GL_'||ba.code), '1110-'||ba.code, 'Cash - '||ba.description,
       CASE WHEN c.country IN ('US','CA','MX','BR','CL','VE') THEN 'ORACLE'
            WHEN c.country IN ('GB','IE','FR','DE','IT','ES','NL','PL','SE','AE') THEN 'SAP'
            ELSE 'NETSUITE' END,
       ba.company_ref, ba.code, 'ASSET', ba.currency_ref
FROM bank_account ba JOIN company c ON c.code = ba.company_ref;

INSERT INTO gl_account (uuid, code, description, chart_of_accounts, company_ref, account_class, currency_ref)
SELECT gen_uuid('GL_'||c.code||'_'||t.glcode),
       t.glcode||'-'||c.code, t.descr,
       CASE WHEN c.country IN ('US','CA','MX','BR','CL','VE') THEN 'ORACLE'
            WHEN c.country IN ('GB','IE','FR','DE','IT','ES','NL','PL','SE','AE') THEN 'SAP'
            ELSE 'NETSUITE' END,
       c.code, t.cls,
       CASE WHEN c.code='GR_HOLDINGS' THEN 'GBP'
            WHEN c.code='GR_TREASURY' THEN 'GBP'
            WHEN c.country='US'  THEN 'USD' WHEN c.country='CA'  THEN 'CAD'
            WHEN c.country='MX'  THEN 'MXN' WHEN c.country='BR'  THEN 'BRL'
            WHEN c.country='CL'  THEN 'CLP' WHEN c.country='VE'  THEN 'USD'
            WHEN c.country='GB'  THEN 'GBP' WHEN c.country='IE'  THEN 'EUR'
            WHEN c.country='FR'  THEN 'EUR' WHEN c.country='DE'  THEN 'EUR'
            WHEN c.country='IT'  THEN 'EUR' WHEN c.country='ES'  THEN 'EUR'
            WHEN c.country='NL'  THEN 'EUR' WHEN c.country='PL'  THEN 'PLN'
            WHEN c.country='SE'  THEN 'SEK' WHEN c.country='AE'  THEN 'AED'
            WHEN c.country='HK'  THEN 'HKD' WHEN c.country='JP'  THEN 'JPY'
            WHEN c.country='AU'  THEN 'AUD' WHEN c.country='KR'  THEN 'KRW'
            WHEN c.country='SG'  THEN 'SGD' ELSE 'USD' END
FROM company c CROSS JOIN (
    SELECT '1200' AS glcode,'Accounts Receivable' AS descr,'ASSET' AS cls UNION ALL
    SELECT '1300','Inventory','ASSET' UNION ALL SELECT '1500','Fixed Assets','ASSET' UNION ALL
    SELECT '2100','Accounts Payable','LIABILITY' UNION ALL SELECT '2200','Accrued Expenses','LIABILITY' UNION ALL
    SELECT '2300','Short-Term Debt','LIABILITY' UNION ALL SELECT '2400','Long-Term Debt','LIABILITY' UNION ALL
    SELECT '3000','Equity','EQUITY' UNION ALL SELECT '4000','Revenue','REVENUE' UNION ALL
    SELECT '5000','COGS','EXPENSE' UNION ALL SELECT '6000','Operating Expense','EXPENSE' UNION ALL
    SELECT '7000','Interest Expense','EXPENSE' UNION ALL SELECT '7100','Interest Income','REVENUE' UNION ALL
    SELECT '7500','FX Gain Loss','EXPENSE'
) t;

-- VERIFY
SELECT 'SWEEP_INSTRUCTION' AS t, COUNT(*) FROM sweep_instruction
UNION ALL SELECT 'LIQUIDITY_POLICY', COUNT(*) FROM liquidity_policy
UNION ALL SELECT 'CREDIT_FACILITY', COUNT(*) FROM credit_facility
UNION ALL SELECT 'INVESTMENT_INSTRUMENT', COUNT(*) FROM investment_instrument
UNION ALL SELECT 'STRESS_SCENARIO', COUNT(*) FROM stress_scenario
UNION ALL SELECT 'FEE_RATE_CARD', COUNT(*) FROM fee_rate_card
UNION ALL SELECT 'GL_ACCOUNT', COUNT(*) FROM gl_account;
