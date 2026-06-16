-- =============================================================================
-- 01_reference_data.sql (Redshift) — currencies, banks, branches, companies,
-- accounts, third parties, codes, service types
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 1.1 CURRENCY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE currency;
INSERT INTO currency (uuid, code, description, number_of_decimals, delivery_float, is_reference, hide_in_list)
SELECT gen_uuid('CCY_'||code), code, description, decimals, dfloat, is_ref, FALSE
FROM (
    SELECT 'USD' AS code,'US Dollar' AS description,2 AS decimals,1 AS dfloat,TRUE AS is_ref UNION ALL
    SELECT 'EUR','Euro',2,1,FALSE UNION ALL SELECT 'GBP','Pound Sterling',2,1,FALSE UNION ALL
    SELECT 'JPY','Japanese Yen',0,2,FALSE UNION ALL SELECT 'CNY','Chinese Yuan',2,2,FALSE UNION ALL
    SELECT 'HKD','Hong Kong Dollar',2,2,FALSE UNION ALL SELECT 'SGD','Singapore Dollar',2,2,FALSE UNION ALL
    SELECT 'AUD','Australian Dollar',2,2,FALSE UNION ALL SELECT 'KRW','South Korean Won',0,2,FALSE UNION ALL
    SELECT 'INR','Indian Rupee',2,2,FALSE UNION ALL SELECT 'AED','UAE Dirham',2,2,FALSE UNION ALL
    SELECT 'CAD','Canadian Dollar',2,2,FALSE UNION ALL SELECT 'MXN','Mexican Peso',2,2,FALSE UNION ALL
    SELECT 'BRL','Brazilian Real',2,2,FALSE UNION ALL SELECT 'CLP','Chilean Peso',0,2,FALSE UNION ALL
    SELECT 'PLN','Polish Zloty',2,2,FALSE UNION ALL SELECT 'SEK','Swedish Krona',2,2,FALSE UNION ALL
    SELECT 'CHF','Swiss Franc',2,1,FALSE
) t;

-- -----------------------------------------------------------------------------
-- 1.2 BANK
-- -----------------------------------------------------------------------------
TRUNCATE TABLE bank;
INSERT INTO bank (uuid, code, interface_code, description1, bic, intercompany, internal_counterparty,
    cash_exposure_limit_amount, cash_exposure_limit_currency, cash_exposure_limit_pct,
    risk_tier_ref, address)
SELECT gen_uuid('BANK_'||code), code, code, name, bic,
       CASE WHEN code='BANK_IHB' THEN TRUE ELSE FALSE END,
       CASE WHEN code='BANK_IHB' THEN TRUE ELSE FALSE END,
       lim_amt, 'USD', lim_pct, tier,
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() SUPER constructor for NULL/quote safety.
       OBJECT('country', country_code, 'region', region)
FROM (
    SELECT 'BANK_JPM' AS code,'JPMorgan Chase' AS name,'CHASUS33' AS bic,'US' AS country_code,'AMER' AS region,2000000000::NUMERIC AS lim_amt,25.0::NUMERIC AS lim_pct,'TIER_1' AS tier UNION ALL
    SELECT 'BANK_BAC','Bank of America','BOFAUS3N','US','AMER',2000000000,25.0,'TIER_1' UNION ALL
    SELECT 'BANK_RBC','Royal Bank of Canada','ROYCCAT2','CA','AMER',900000000,25.0,'TIER_1' UNION ALL
    SELECT 'BANK_BBVA_MX','BBVA Mexico','BCMRMXMM','MX','LATAM',600000000,12.0,'TIER_2' UNION ALL
    SELECT 'BANK_CITI','Citibank','CITIUS33','US','GLOBAL',2200000000,25.0,'TIER_1' UNION ALL
    SELECT 'BANK_HSBC','HSBC','HBUKGB4B','GB','GLOBAL',2500000000,25.0,'TIER_1' UNION ALL
    SELECT 'BANK_BNP','BNP Paribas','BNPAFRPP','FR','EMEA',2000000000,25.0,'TIER_1' UNION ALL
    SELECT 'BANK_DB','Deutsche Bank','DEUTDEFF','DE','EMEA',900000000,12.0,'TIER_2' UNION ALL
    SELECT 'BANK_NORDEA','Nordea','NDEASESS','SE','EMEA',700000000,12.0,'TIER_2' UNION ALL
    SELECT 'BANK_SCB','Standard Chartered','SCBLSGSG','SG','APAC',900000000,12.0,'TIER_2' UNION ALL
    SELECT 'BANK_MUFG','MUFG Bank','BOTKJPJT','JP','APAC',1500000000,25.0,'TIER_1' UNION ALL
    SELECT 'BANK_ANZ','ANZ','ANZBAU3M','AU','APAC',700000000,12.0,'TIER_2' UNION ALL
    SELECT 'BANK_USR','US Regional Bank','USREUS44','US','AMER',150000000,5.0,'TIER_3' UNION ALL
    SELECT 'BANK_IHB','GR Treasury IHB',NULL,'GB','INTERNAL',NULL,NULL,NULL
) t;

-- -----------------------------------------------------------------------------
-- 1.3 BANK_BRANCH
-- -----------------------------------------------------------------------------
TRUNCATE TABLE bank_branch;
INSERT INTO bank_branch (uuid, code, interface_code, bank_ref, description, bic,
    time_zone, cut_off_time, calendar_ref,
    intercompany, intermediary, main_country_branch,
    country_code, region, address)
SELECT gen_uuid('BR_'||code), code, code, bank_ref, descr, bic,
       tz, cot, 'STANDARD', FALSE, FALSE, main_flag, country_code, region,
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor.
       OBJECT('country', country_code, 'city', city)
FROM (
    SELECT 'BR_JPM_NY' AS code,'BANK_JPM' AS bank_ref,'JPM New York' AS descr,'CHASUS33XXX' AS bic,'America/New_York' AS tz,'16:00' AS cot,'US' AS country_code,'AMER' AS region,'New York' AS city,TRUE AS main_flag UNION ALL
    SELECT 'BR_JPM_LDN','BANK_JPM','JPM London','CHASGB2L','Europe/London','14:30','GB','EMEA','London',FALSE UNION ALL
    SELECT 'BR_JPM_HK','BANK_JPM','JPM Hong Kong','CHASHKHH','Asia/Hong_Kong','16:30','HK','APAC','Hong Kong',FALSE UNION ALL
    SELECT 'BR_BAC_NY','BANK_BAC','BofA New York','BOFAUS3NXXX','America/New_York','16:00','US','AMER','New York',TRUE UNION ALL
    SELECT 'BR_BAC_LA','BANK_BAC','BofA Los Angeles','BOFAUS6L','America/Los_Angeles','15:00','US','AMER','Los Angeles',FALSE UNION ALL
    SELECT 'BR_RBC_TOR','BANK_RBC','RBC Toronto','ROYCCAT2XXX','America/Toronto','16:00','CA','AMER','Toronto',TRUE UNION ALL
    SELECT 'BR_BBVA_MX','BANK_BBVA_MX','BBVA Mexico City','BCMRMXMMXXX','America/Mexico_City','15:00','MX','LATAM','Mexico City',TRUE UNION ALL
    SELECT 'BR_BBVA_SP','BANK_BBVA_MX','BBVA Sao Paulo','BCMRBRSP','America/Sao_Paulo','15:30','BR','LATAM','Sao Paulo',FALSE UNION ALL
    SELECT 'BR_CITI_NY','BANK_CITI','Citi New York','CITIUS33XXX','America/New_York','16:00','US','AMER','New York',TRUE UNION ALL
    SELECT 'BR_CITI_LDN','BANK_CITI','Citi London','CITIGB2L','Europe/London','14:30','GB','EMEA','London',FALSE UNION ALL
    SELECT 'BR_CITI_SG','BANK_CITI','Citi Singapore','CITISGSG','Asia/Singapore','17:00','SG','APAC','Singapore',FALSE UNION ALL
    SELECT 'BR_CITI_DXB','BANK_CITI','Citi Dubai','CITIAEAD','Asia/Dubai','14:00','AE','MENA','Dubai',FALSE UNION ALL
    SELECT 'BR_HSBC_LDN','BANK_HSBC','HSBC London','HBUKGB4BXXX','Europe/London','14:30','GB','EMEA','London',TRUE UNION ALL
    SELECT 'BR_HSBC_HK','BANK_HSBC','HSBC Hong Kong','HSBCHKHH','Asia/Hong_Kong','16:30','HK','APAC','Hong Kong',FALSE UNION ALL
    SELECT 'BR_HSBC_SG','BANK_HSBC','HSBC Singapore','HSBCSGSG','Asia/Singapore','17:00','SG','APAC','Singapore',FALSE UNION ALL
    SELECT 'BR_HSBC_DXB','BANK_HSBC','HSBC Dubai','BBMEAEAD','Asia/Dubai','14:00','AE','MENA','Dubai',FALSE UNION ALL
    SELECT 'BR_BNP_PAR','BANK_BNP','BNP Paris','BNPAFRPPXXX','Europe/Paris','15:00','FR','EMEA','Paris',TRUE UNION ALL
    SELECT 'BR_BNP_AMS','BANK_BNP','BNP Amsterdam','BNPANL2A','Europe/Amsterdam','15:00','NL','EMEA','Amsterdam',FALSE UNION ALL
    SELECT 'BR_BNP_MIL','BANK_BNP','BNP Milan','BNPAITRR','Europe/Rome','15:00','IT','EMEA','Milan',FALSE UNION ALL
    SELECT 'BR_DB_FFM','BANK_DB','Deutsche Bank Frankfurt','DEUTDEFFXXX','Europe/Berlin','15:00','DE','EMEA','Frankfurt',TRUE UNION ALL
    SELECT 'BR_DB_WAW','BANK_DB','Deutsche Bank Warsaw','DEUTPLPX','Europe/Warsaw','15:00','PL','EMEA','Warsaw',FALSE UNION ALL
    SELECT 'BR_NDA_STK','BANK_NORDEA','Nordea Stockholm','NDEASESSXXX','Europe/Stockholm','15:00','SE','EMEA','Stockholm',TRUE UNION ALL
    SELECT 'BR_SCB_SG','BANK_SCB','StanChart Singapore','SCBLSGSGXXX','Asia/Singapore','17:00','SG','APAC','Singapore',TRUE UNION ALL
    SELECT 'BR_SCB_HK','BANK_SCB','StanChart Hong Kong','SCBLHKHH','Asia/Hong_Kong','16:30','HK','APAC','Hong Kong',FALSE UNION ALL
    SELECT 'BR_SCB_DXB','BANK_SCB','StanChart Dubai','SCBLAEAD','Asia/Dubai','14:00','AE','MENA','Dubai',FALSE UNION ALL
    SELECT 'BR_MUFG_TKY','BANK_MUFG','MUFG Tokyo','BOTKJPJTXXX','Asia/Tokyo','15:00','JP','APAC','Tokyo',TRUE UNION ALL
    SELECT 'BR_MUFG_SEL','BANK_MUFG','MUFG Seoul','BOTKKRSE','Asia/Seoul','15:30','KR','APAC','Seoul',FALSE UNION ALL
    SELECT 'BR_ANZ_SYD','BANK_ANZ','ANZ Sydney','ANZBAU3MXXX','Australia/Sydney','16:00','AU','APAC','Sydney',TRUE UNION ALL
    SELECT 'BR_USR_DAL','BANK_USR','US Regional Dallas','USREUS44XXX','America/Chicago','16:00','US','AMER','Dallas',TRUE UNION ALL
    SELECT 'BR_IHB_LDN','BANK_IHB','GR Treasury IHB London',NULL,'Europe/London','14:30','GB','EMEA','London',TRUE
) t;

-- -----------------------------------------------------------------------------
-- 1.4 COMPANY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE company;
INSERT INTO company (uuid, code, name, country, lei, address)
SELECT gen_uuid('CO_'||code), code, name, country, lei,
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor.
       OBJECT('country', country, 'functional_ccy', func_ccy)
FROM (
    SELECT 'GR_HOLDINGS' AS code,'GlobalRetail Holdings plc' AS name,'GB' AS country,'LEI-GRHOLD' AS lei,'GBP' AS func_ccy UNION ALL
    SELECT 'GR_TREASURY','GlobalRetail Treasury Ltd','GB','LEI-GRTREAS','GBP' UNION ALL
    SELECT 'GR_US_INC','GlobalRetail US Inc','US','LEI-GRUSI','USD' UNION ALL
    SELECT 'GR_EU_BV','GlobalRetail Europe BV','NL','LEI-GREUBV','EUR' UNION ALL
    SELECT 'GR_APAC_PTE','GlobalRetail APAC Pte','SG','LEI-GRAPAC','SGD' UNION ALL
    SELECT 'GR_LATAM_SA','GlobalRetail LATAM SA','MX','LEI-GRLATAM','MXN' UNION ALL
    SELECT 'GR_CA','GlobalRetail Canada Ltd','CA','LEI-GRCA','CAD' UNION ALL
    SELECT 'GR_MX','GlobalRetail Mexico SA de CV','MX','LEI-GRMX','MXN' UNION ALL
    SELECT 'GR_BR','GlobalRetail Brasil Ltda','BR','LEI-GRBR','BRL' UNION ALL
    SELECT 'GR_CL','GlobalRetail Chile SpA','CL','LEI-GRCL','CLP' UNION ALL
    SELECT 'GR_VE','GlobalRetail Venezuela CA','VE','LEI-GRVE','USD' UNION ALL
    SELECT 'GR_GB','GlobalRetail UK Ltd','GB','LEI-GRGB','GBP' UNION ALL
    SELECT 'GR_IE','GlobalRetail Ireland DAC','IE','LEI-GRIE','EUR' UNION ALL
    SELECT 'GR_FR','GlobalRetail France SAS','FR','LEI-GRFR','EUR' UNION ALL
    SELECT 'GR_DE','GlobalRetail Deutschland GmbH','DE','LEI-GRDE','EUR' UNION ALL
    SELECT 'GR_IT','GlobalRetail Italia SRL','IT','LEI-GRIT','EUR' UNION ALL
    SELECT 'GR_ES','GlobalRetail Iberia SL','ES','LEI-GRES','EUR' UNION ALL
    SELECT 'GR_PL','GlobalRetail Polska Sp z o.o.','PL','LEI-GRPL','PLN' UNION ALL
    SELECT 'GR_SE','GlobalRetail Sverige AB','SE','LEI-GRSE','SEK' UNION ALL
    SELECT 'GR_AE','GlobalRetail Middle East FZE','AE','LEI-GRAE','AED' UNION ALL
    SELECT 'GR_HK','GlobalRetail Hong Kong Ltd','HK','LEI-GRHK','HKD' UNION ALL
    SELECT 'GR_JP','GlobalRetail Japan KK','JP','LEI-GRJP','JPY' UNION ALL
    SELECT 'GR_AU','GlobalRetail Australia Pty Ltd','AU','LEI-GRAU','AUD' UNION ALL
    SELECT 'GR_KR','GlobalRetail Korea Co Ltd','KR','LEI-GRKR','KRW'
) t;

TRUNCATE TABLE company_group;
INSERT INTO company_group (uuid, code, description) VALUES
    (gen_uuid('CG_GLOBAL'),'GROUP_GLOBAL','All entities — group consolidation'),
    (gen_uuid('CG_AMER'),'GROUP_AMER','Americas'),
    (gen_uuid('CG_EMEA'),'GROUP_EMEA','Europe / Middle East / Africa'),
    (gen_uuid('CG_APAC'),'GROUP_APAC','Asia Pacific'),
    (gen_uuid('CG_LATAM'),'GROUP_LATAM','Latin America'),
    (gen_uuid('CG_OPCOS'),'GROUP_RETAIL_OPCO','Store-operating companies only');

TRUNCATE TABLE company_group_member;
INSERT INTO company_group_member (company_group_code, company_code)
SELECT 'GROUP_GLOBAL', code FROM company
UNION ALL SELECT 'GROUP_AMER', code FROM company WHERE code IN ('GR_US_INC','GR_CA','GR_MX','GR_BR','GR_CL','GR_VE','GR_LATAM_SA')
UNION ALL SELECT 'GROUP_EMEA', code FROM company WHERE code IN ('GR_HOLDINGS','GR_TREASURY','GR_EU_BV','GR_GB','GR_IE','GR_FR','GR_DE','GR_IT','GR_ES','GR_PL','GR_SE','GR_AE')
UNION ALL SELECT 'GROUP_APAC', code FROM company WHERE code IN ('GR_APAC_PTE','GR_HK','GR_JP','GR_AU','GR_KR')
UNION ALL SELECT 'GROUP_LATAM', code FROM company WHERE code IN ('GR_LATAM_SA','GR_MX','GR_BR','GR_CL','GR_VE')
UNION ALL SELECT 'GROUP_RETAIL_OPCO', code FROM company WHERE code LIKE 'GR_%'
   AND code NOT IN ('GR_HOLDINGS','GR_TREASURY','GR_US_INC','GR_EU_BV','GR_APAC_PTE','GR_LATAM_SA');

DROP TABLE IF EXISTS gen_company_region CASCADE;
CREATE TABLE gen_company_region AS
SELECT c.code AS company_code,
       CASE c.country
         WHEN 'US' THEN 'AMER' WHEN 'CA' THEN 'AMER'
         WHEN 'MX' THEN 'LATAM' WHEN 'BR' THEN 'LATAM' WHEN 'CL' THEN 'LATAM' WHEN 'VE' THEN 'LATAM'
         WHEN 'AE' THEN 'MENA'
         WHEN 'JP' THEN 'APAC' WHEN 'HK' THEN 'APAC' WHEN 'AU' THEN 'APAC' WHEN 'KR' THEN 'APAC' WHEN 'SG' THEN 'APAC'
         ELSE 'EMEA'
       END AS region,
       CASE c.code
         WHEN 'GR_HOLDINGS' THEN 'GBP' WHEN 'GR_TREASURY' THEN 'GBP'
         WHEN 'GR_US_INC' THEN 'USD'  WHEN 'GR_EU_BV' THEN 'EUR'
         WHEN 'GR_APAC_PTE' THEN 'SGD' WHEN 'GR_LATAM_SA' THEN 'MXN'
         WHEN 'GR_CA' THEN 'CAD' WHEN 'GR_MX' THEN 'MXN' WHEN 'GR_BR' THEN 'BRL' WHEN 'GR_CL' THEN 'CLP' WHEN 'GR_VE' THEN 'USD'
         WHEN 'GR_GB' THEN 'GBP' WHEN 'GR_IE' THEN 'EUR' WHEN 'GR_FR' THEN 'EUR' WHEN 'GR_DE' THEN 'EUR'
         WHEN 'GR_IT' THEN 'EUR' WHEN 'GR_ES' THEN 'EUR' WHEN 'GR_PL' THEN 'PLN' WHEN 'GR_SE' THEN 'SEK'
         WHEN 'GR_AE' THEN 'AED' WHEN 'GR_HK' THEN 'HKD' WHEN 'GR_JP' THEN 'JPY' WHEN 'GR_AU' THEN 'AUD' WHEN 'GR_KR' THEN 'KRW'
         ELSE 'USD' END AS functional_ccy
FROM company c;

-- -----------------------------------------------------------------------------
-- 1.5 BANK_ACCOUNT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE bank_account;

DROP TABLE IF EXISTS t_opco_bank;
CREATE TEMP TABLE t_opco_bank AS
SELECT * FROM (
    SELECT 'GR_US_INC' AS company_code,'BR_JPM_NY' AS branch_ref,'USD' AS ccy UNION ALL
    SELECT 'GR_CA','BR_RBC_TOR','CAD' UNION ALL
    SELECT 'GR_MX','BR_BBVA_MX','MXN' UNION ALL SELECT 'GR_BR','BR_BBVA_SP','BRL' UNION ALL
    SELECT 'GR_CL','BR_CITI_NY','CLP' UNION ALL SELECT 'GR_VE','BR_CITI_NY','USD' UNION ALL
    SELECT 'GR_GB','BR_HSBC_LDN','GBP' UNION ALL SELECT 'GR_IE','BR_BNP_PAR','EUR' UNION ALL
    SELECT 'GR_FR','BR_BNP_PAR','EUR' UNION ALL SELECT 'GR_DE','BR_DB_FFM','EUR' UNION ALL
    SELECT 'GR_IT','BR_BNP_MIL','EUR' UNION ALL SELECT 'GR_ES','BR_BNP_PAR','EUR' UNION ALL
    SELECT 'GR_PL','BR_DB_WAW','PLN' UNION ALL SELECT 'GR_SE','BR_NDA_STK','SEK' UNION ALL
    SELECT 'GR_AE','BR_HSBC_DXB','AED' UNION ALL SELECT 'GR_HK','BR_HSBC_HK','HKD' UNION ALL
    SELECT 'GR_JP','BR_MUFG_TKY','JPY' UNION ALL SELECT 'GR_AU','BR_ANZ_SYD','AUD' UNION ALL
    SELECT 'GR_KR','BR_MUFG_SEL','KRW'
) x;

-- 4 operational accounts per opco (OPERATING, COLLECTION, PAYROLL, TAX)
INSERT INTO bank_account (
    uuid, code, description, account_type, currency_ref, company_ref, branch_ref,
    calendar_ref, time_zone, opening_date, closing_date, closed_account,
    interest_bearing, centrally_managed, account_purpose,
    min_operating_balance, min_operating_balance_ccy,
    initial_accounting_balance, initial_accounting_balance_ccy, initial_accounting_balance_date,
    bank_account_id,
    integrate_end_of_day_statements, integrate_intraday_statements,
    consider_one_day_float_transactions, account_available_for_payments
)
SELECT
    gen_uuid('ACC_'||o.company_code||'_'||p.purpose||'_1'),
    o.company_code||'_'||p.purpose||'_1',
    o.company_code||' '||p.purpose||' Account',
    'BANK_ACCOUNT', o.ccy, o.company_code, o.branch_ref,
    'STANDARD','UTC', DATE '2018-01-01',
    CASE WHEN o.company_code='GR_VE' THEN DATE '2024-09-30' ELSE NULL END,
    CASE WHEN o.company_code='GR_VE' THEN TRUE ELSE FALSE END,
    CASE WHEN p.purpose IN ('OPERATING','PAYROLL','TAX') THEN FALSE ELSE TRUE END,
    TRUE, p.purpose,
    CASE p.purpose WHEN 'OPERATING' THEN p.min_bal WHEN 'PAYROLL' THEN p.min_bal*0.5 ELSE NULL END,
    o.ccy,
    CASE p.purpose
      WHEN 'OPERATING'  THEN p.min_bal * 2.0
      WHEN 'COLLECTION' THEN 50000
      WHEN 'PAYROLL'    THEN p.min_bal * 0.3
      WHEN 'TAX'        THEN p.min_bal * 0.4
    END,
    o.ccy, DATE '2023-04-30',
    -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor for NULL/quote safety.
    OBJECT('iban', UPPER(SUBSTRING(o.company_code,4,2))||LPAD(CAST(ABS(FNV_HASH(o.company_code||'|'||p.purpose)) AS VARCHAR),18,'0'),
           'bban', LPAD(CAST(ABS(FNV_HASH(o.company_code||'|'||p.purpose)) AS VARCHAR),18,'0')),
    TRUE, TRUE, TRUE,
    CASE WHEN p.purpose IN ('OPERATING','DISBURSEMENT','PAYROLL','TAX') THEN TRUE ELSE FALSE END
FROM t_opco_bank o
CROSS JOIN ( SELECT 'OPERATING' AS purpose, 500000::NUMERIC AS min_bal UNION ALL
             SELECT 'COLLECTION', 100000 UNION ALL
             SELECT 'PAYROLL',    200000 UNION ALL
             SELECT 'TAX',        100000 ) p;

-- Regional HQ accounts
INSERT INTO bank_account (
    uuid, code, description, account_type, currency_ref, company_ref, branch_ref,
    calendar_ref, time_zone, opening_date, closed_account, interest_bearing, centrally_managed,
    account_purpose, min_operating_balance, min_operating_balance_ccy,
    initial_accounting_balance, initial_accounting_balance_ccy, initial_accounting_balance_date,
    bank_account_id, zba_generator,
    integrate_end_of_day_statements, account_available_for_payments
)
SELECT gen_uuid('ACC_'||code), code, descr,
       'BANK_ACCOUNT', ccy, co, br, 'STANDARD','UTC', DATE '2018-01-01',FALSE,TRUE,TRUE,
       p, mb, ccy,
       CASE p WHEN 'CONCENTRATION' THEN COALESCE(mb,5000000) * 5 WHEN 'INVESTMENT' THEN 25000000 ELSE COALESCE(mb,1000000)*2 END,
       ccy, DATE '2023-04-30',
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor.
       OBJECT('iban', UPPER(SUBSTRING(co,4,2))||LPAD(CAST(ABS(FNV_HASH(code)) AS VARCHAR),18,'0')),
       CASE WHEN p='CONCENTRATION' THEN TRUE ELSE FALSE END, TRUE, TRUE
FROM (
    SELECT 'USA_RGNL_CONCENTRATION' AS code,'GR_US_INC' AS co,'BR_JPM_NY' AS br,'USD' AS ccy,'CONCENTRATION' AS p,5000000::NUMERIC AS mb,'USA_RGNL Concentration' AS descr UNION ALL
    SELECT 'USA_RGNL_OPERATING','GR_US_INC','BR_JPM_NY','USD','OPERATING',2000000,'USA_RGNL Operating' UNION ALL
    SELECT 'USA_RGNL_INVESTMENT','GR_US_INC','BR_JPM_NY','USD','INVESTMENT',NULL,'USA_RGNL Investment' UNION ALL
    SELECT 'EUR_RGNL_CONCENTRATION','GR_EU_BV','BR_BNP_AMS','EUR','CONCENTRATION',4000000,'EUR_RGNL Concentration' UNION ALL
    SELECT 'EUR_RGNL_OPERATING','GR_EU_BV','BR_BNP_AMS','EUR','OPERATING',1500000,'EUR_RGNL Operating' UNION ALL
    SELECT 'EUR_RGNL_INVESTMENT','GR_EU_BV','BR_BNP_AMS','EUR','INVESTMENT',NULL,'EUR_RGNL Investment' UNION ALL
    SELECT 'APC_RGNL_CONCENTRATION','GR_APAC_PTE','BR_HSBC_SG','SGD','CONCENTRATION',3000000,'APC_RGNL Concentration' UNION ALL
    SELECT 'APC_RGNL_OPERATING','GR_APAC_PTE','BR_HSBC_SG','SGD','OPERATING',1000000,'APC_RGNL Operating' UNION ALL
    SELECT 'APC_RGNL_INVESTMENT','GR_APAC_PTE','BR_HSBC_SG','SGD','INVESTMENT',NULL,'APC_RGNL Investment' UNION ALL
    SELECT 'LAT_RGNL_CONCENTRATION','GR_LATAM_SA','BR_BBVA_MX','MXN','CONCENTRATION',60000000,'LAT_RGNL Concentration' UNION ALL
    SELECT 'LAT_RGNL_OPERATING','GR_LATAM_SA','BR_BBVA_MX','MXN','OPERATING',20000000,'LAT_RGNL Operating'
) t;

-- IHB master concentration accounts
INSERT INTO bank_account (
    uuid, code, description, account_type, currency_ref, company_ref, branch_ref,
    calendar_ref, time_zone, opening_date, closed_account, interest_bearing, centrally_managed,
    account_purpose, initial_accounting_balance, initial_accounting_balance_ccy, initial_accounting_balance_date,
    bank_account_id, zba_generator, integrate_end_of_day_statements, account_available_for_payments
)
SELECT gen_uuid('IHB_'||ccy||'_'||p), 'IHB_'||ccy||'_'||p,
       'IHB '||p||' '||ccy, 'BANK_ACCOUNT', ccy, 'GR_TREASURY','BR_IHB_LDN',
       'STANDARD','UTC', DATE '2017-01-01',FALSE, TRUE, TRUE, p,
       CASE p WHEN 'CONCENTRATION' THEN 30000000 WHEN 'INVESTMENT' THEN 50000000 WHEN 'DISBURSEMENT' THEN 5000000 ELSE 0 END,
       ccy, DATE '2023-04-30',
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor.
       OBJECT('iban', 'GB'||LPAD(CAST(ABS(FNV_HASH('IHB_'||ccy||'_'||p)) AS VARCHAR),20,'0')),
       TRUE, TRUE, TRUE
FROM (
    SELECT 'USD' AS ccy,'CONCENTRATION' AS p UNION ALL SELECT 'USD','DISBURSEMENT' UNION ALL SELECT 'USD','INVESTMENT' UNION ALL
    SELECT 'EUR','CONCENTRATION' UNION ALL SELECT 'EUR','DISBURSEMENT' UNION ALL SELECT 'EUR','INVESTMENT' UNION ALL
    SELECT 'GBP','CONCENTRATION' UNION ALL SELECT 'GBP','DISBURSEMENT' UNION ALL
    SELECT 'SGD','CONCENTRATION' UNION ALL SELECT 'JPY','CONCENTRATION' UNION ALL
    SELECT 'AUD','CONCENTRATION' UNION ALL SELECT 'CAD','CONCENTRATION' UNION ALL
    SELECT 'MXN','CONCENTRATION' UNION ALL SELECT 'BRL','CONCENTRATION' UNION ALL
    SELECT 'CHF','INVESTMENT'
) t;

-- Holdings investment accounts
INSERT INTO bank_account (
    uuid, code, description, account_type, currency_ref, company_ref, branch_ref,
    calendar_ref, time_zone, opening_date, closed_account, interest_bearing, centrally_managed,
    account_purpose, initial_accounting_balance, initial_accounting_balance_ccy, initial_accounting_balance_date,
    integrate_end_of_day_statements
)
SELECT gen_uuid('HQ_INV_'||ccy), 'HQ_INV_'||ccy, 'Holdings Investment '||ccy,
       'BANK_ACCOUNT', ccy, 'GR_HOLDINGS','BR_IHB_LDN','STANDARD','UTC', DATE '2017-01-01',
       FALSE, TRUE, TRUE, 'INVESTMENT', 100000000, ccy, DATE '2023-04-30', TRUE
FROM (SELECT 'GBP' AS ccy UNION ALL SELECT 'USD' UNION ALL SELECT 'EUR') t;

-- Tier-2 city operating accounts
INSERT INTO bank_account (
    uuid, code, description, account_type, currency_ref, company_ref, branch_ref,
    calendar_ref, time_zone, opening_date, closed_account, interest_bearing, centrally_managed,
    account_purpose, min_operating_balance, min_operating_balance_ccy,
    initial_accounting_balance, initial_accounting_balance_ccy, initial_accounting_balance_date,
    bank_account_id, integrate_end_of_day_statements, account_available_for_payments
)
SELECT gen_uuid('TIER2_'||code), code, descr,
       'BANK_ACCOUNT', ccy, co_code, br_code,
       'STANDARD','UTC', DATE '2019-01-01',FALSE,FALSE,TRUE,
       'OPERATING', 250000, ccy, 600000, ccy, DATE '2023-04-30',
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor.
       OBJECT('iban', UPPER(SUBSTRING(co_code,4,2))||LPAD(CAST(ABS(FNV_HASH(code)) AS VARCHAR),18,'0')),
       TRUE, TRUE
FROM (
    SELECT 'GR_US_INC_OP_2' AS code,'GR US Operating LA' AS descr,'GR_US_INC' AS co_code,'BR_BAC_LA' AS br_code,'USD' AS ccy UNION ALL
    SELECT 'GR_US_INC_OP_3','GR US Operating Citi','GR_US_INC','BR_CITI_NY','USD' UNION ALL
    SELECT 'GR_US_INC_OP_4','GR US Regional Bank','GR_US_INC','BR_USR_DAL','USD' UNION ALL
    SELECT 'GR_GB_OP_2','GR UK Operating Citi London','GR_GB','BR_CITI_LDN','GBP' UNION ALL
    SELECT 'GR_GB_OP_3','GR UK Operating JPM London','GR_GB','BR_JPM_LDN','GBP' UNION ALL
    SELECT 'GR_DE_OP_2','GR DE Operating BNP','GR_DE','BR_BNP_PAR','EUR' UNION ALL
    SELECT 'GR_FR_OP_2','GR FR Operating DB','GR_FR','BR_DB_FFM','EUR' UNION ALL
    SELECT 'GR_JP_OP_2','GR JP Operating HSBC','GR_JP','BR_HSBC_HK','JPY' UNION ALL
    SELECT 'GR_HK_OP_2','GR HK Operating SCB','GR_HK','BR_SCB_HK','HKD' UNION ALL
    SELECT 'GR_AE_OP_2','GR AE Operating SCB','GR_AE','BR_SCB_DXB','AED'
) t;

-- One fraud-driven closed account
UPDATE bank_account SET closing_date= DATE '2024-09-30', closed_account=TRUE WHERE code='GR_AE_OP_2';

-- -----------------------------------------------------------------------------
-- 1.6 CASH_FLOW_CODE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE cash_flow_code;
INSERT INTO cash_flow_code (uuid, code, description, sign, category)
SELECT gen_uuid('CFC_'||code), code, descr, sign, cat FROM (
    SELECT 'RCPT_POS' AS code,'POS retail receipts' AS descr,'IN' AS sign,'OPERATIONS' AS cat UNION ALL
    SELECT 'RCPT_ECOM','E-commerce settlements','IN','OPERATIONS' UNION ALL
    SELECT 'RCPT_AR_WIRE','Wholesale AR wire receipts','IN','OPERATIONS' UNION ALL
    SELECT 'RCPT_AR_ACH','Wholesale AR ACH receipts','IN','OPERATIONS' UNION ALL
    SELECT 'RCPT_AR_SEPA','Wholesale AR SEPA receipts','IN','OPERATIONS' UNION ALL
    SELECT 'DISB_AP_WIRE','AP wire disbursements','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_AP_ACH','AP ACH disbursements','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_AP_SEPA','AP SEPA disbursements','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_AP_RTP','AP RTP/FedNow','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_AP_CHECK','AP check disbursements','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_PAYROLL','Payroll','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_RENT','Rent','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_UTIL','Utilities','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_TAX','Tax payments','OUT','OPERATIONS' UNION ALL
    SELECT 'DISB_CAPEX','Capital expenditure','OUT','INVESTING' UNION ALL
    SELECT 'CARD_INTERCHANGE','Card interchange fee','OUT','OPERATIONS' UNION ALL
    SELECT 'CARD_CHARGEBACK','Card chargeback / refund','OUT','OPERATIONS' UNION ALL
    SELECT 'BANK_FEE','Bank service fee','OUT','OPERATIONS' UNION ALL
    SELECT 'SWEEP_DEBIT','Sweep debit leg','OUT','FINANCING' UNION ALL
    SELECT 'SWEEP_CREDIT','Sweep credit leg','IN','FINANCING' UNION ALL
    SELECT 'IC_FUNDING','Intercompany funding-out','OUT','FINANCING' UNION ALL
    SELECT 'IC_FUNDING_IN','Intercompany funding-in','IN','FINANCING' UNION ALL
    SELECT 'IC_LOAN_DRAW','Intercompany loan drawdown','IN','FINANCING' UNION ALL
    SELECT 'IC_LOAN_REPAY','Intercompany loan repayment','OUT','FINANCING' UNION ALL
    SELECT 'IC_DIVIDEND','Intercompany dividend','OUT','FINANCING' UNION ALL
    SELECT 'IC_ROYALTY','Royalty payment','OUT','OPERATIONS' UNION ALL
    SELECT 'IC_SERVICE_FEE','Intercompany service fee','OUT','OPERATIONS' UNION ALL
    SELECT 'INV_PURCHASE','Investment purchase','OUT','INVESTING' UNION ALL
    SELECT 'INV_MATURITY','Investment maturity','IN','INVESTING' UNION ALL
    SELECT 'INV_INTEREST','Investment coupon/interest','IN','INVESTING' UNION ALL
    SELECT 'LOAN_DRAW','External borrowing draw','IN','FINANCING' UNION ALL
    SELECT 'LOAN_REPAY','External borrowing repay','OUT','FINANCING' UNION ALL
    SELECT 'LOAN_INTEREST','Borrowing interest','OUT','FINANCING' UNION ALL
    SELECT 'FX_SETTLE_BUY','FX deal settlement buy','IN','OPERATIONS' UNION ALL
    SELECT 'FX_SETTLE_SELL','FX deal settlement sell','OUT','OPERATIONS' UNION ALL
    SELECT 'FX_REVAL','FX revaluation','IN','OPERATIONS' UNION ALL
    SELECT 'OTHER_IN','Other receipts','IN','OPERATIONS' UNION ALL
    SELECT 'OTHER_OUT','Other disbursements','OUT','OPERATIONS' UNION ALL
    SELECT 'FRAUD_REVERSAL','Fraud reversal','IN','OPERATIONS' UNION ALL
    SELECT 'OD_INTEREST','Overdraft interest','OUT','FINANCING'
) t;

TRUNCATE TABLE budget_code;
INSERT INTO budget_code (uuid, code, description) VALUES
    (gen_uuid('BC_OPS'),'BC_OPS','Operating budget'),
    (gen_uuid('BC_CAPEX'),'BC_CAPEX','Capital expenditure'),
    (gen_uuid('BC_TAX'),'BC_TAX','Tax & duties'),
    (gen_uuid('BC_TREAS'),'BC_TREAS','Treasury & financing');

-- -----------------------------------------------------------------------------
-- 1.7 THIRD_PARTY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE third_party;
INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
VALUES (gen_uuid('TP_RETAIL_CONSUMER'),'RETAIL_CONSUMER','ORGANIZATION','Aggregated Retail Consumer',TRUE,FALSE);

-- Helper: pick i-th item from a list of N
-- Customers (80)
INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_CUST_'||(n+1)),
       'TP_CUST_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION',
       CASE MOD(n,15)
         WHEN 0 THEN 'Macys' WHEN 1 THEN 'Nordstrom' WHEN 2 THEN 'El Corte Ingles'
         WHEN 3 THEN 'Galeries Lafayette' WHEN 4 THEN 'Selfridges' WHEN 5 THEN 'Karstadt'
         WHEN 6 THEN 'Falabella' WHEN 7 THEN 'Lotte' WHEN 8 THEN 'Isetan' WHEN 9 THEN 'David Jones'
         WHEN 10 THEN 'Hudsons Bay' WHEN 11 THEN 'Liverpool' WHEN 12 THEN 'Magazine Luiza'
         WHEN 13 THEN 'Sogo' ELSE 'Zalora' END
       || ' Partner ' || CAST(n+1 AS VARCHAR),
       TRUE, FALSE
FROM gen_numbers WHERE n < 80;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_APPAREL_'||(n+1)),
       'TP_APPAREL_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION',
       CASE MOD(n,8)
         WHEN 0 THEN 'Shanghai' WHEN 1 THEN 'Dhaka' WHEN 2 THEN 'Ho Chi Minh' WHEN 3 THEN 'Bangalore'
         WHEN 4 THEN 'Istanbul' WHEN 5 THEN 'Jakarta' WHEN 6 THEN 'Cairo' ELSE 'Casablanca' END
       ||' Apparel Mfg '|| CAST(n+1 AS VARCHAR),
       FALSE, TRUE
FROM gen_numbers WHERE n < 80;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_LOGI_'||(n+1)),
       'TP_LOGI_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION',
       CASE MOD(n,8)
         WHEN 0 THEN 'Maersk' WHEN 1 THEN 'DHL' WHEN 2 THEN 'FedEx' WHEN 3 THEN 'UPS'
         WHEN 4 THEN 'Kuehne+Nagel' WHEN 5 THEN 'DB Schenker' WHEN 6 THEN 'XPO' ELSE 'Ceva' END
       ||' Logistics Region '|| CAST(n+1 AS VARCHAR),
       FALSE, TRUE
FROM gen_numbers WHERE n < 60;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_LAND_'||(n+1)),'TP_LAND_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION', 'Landlord Holdings ' || CAST(n+1 AS VARCHAR), FALSE, TRUE
FROM gen_numbers WHERE n < 40;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_UTIL_'||(n+1)),'TP_UTIL_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION', 'Utility Provider ' || CAST(n+1 AS VARCHAR), FALSE, TRUE
FROM gen_numbers WHERE n < 30;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_MKTG_'||(n+1)),'TP_MKTG_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION', 'Marketing Agency ' || CAST(n+1 AS VARCHAR), FALSE, TRUE
FROM gen_numbers WHERE n < 20;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_IT_'||(n+1)),'TP_IT_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION', 'IT/SaaS Vendor ' || CAST(n+1 AS VARCHAR), FALSE, TRUE
FROM gen_numbers WHERE n < 15;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_CAPEX_'||(n+1)),'TP_CAPEX_'||LPAD(CAST(n+1 AS VARCHAR),4,'0'),
       'ORGANIZATION', 'CAPEX Vendor ' || CAST(n+1 AS VARCHAR), FALSE, TRUE
FROM gen_numbers WHERE n < 5;

-- Card processors
INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_PSP_'||name), 'TP_PSP_'||UPPER(name), 'ORGANIZATION', name||' Card Processor', FALSE, TRUE
FROM (SELECT 'ADYEN' AS name UNION ALL SELECT 'STRIPE' UNION ALL SELECT 'WORLDPAY'
      UNION ALL SELECT 'FISERV' UNION ALL SELECT 'SQUARE') t;

INSERT INTO third_party (uuid, code, third_party_type, name, debtor, creditor)
SELECT gen_uuid('TP_TAX_'||code), code, 'ORGANIZATION', name, FALSE, TRUE FROM (
    SELECT 'TP_TAX_HMRC' AS code,'HM Revenue & Customs' AS name UNION ALL
    SELECT 'TP_TAX_IRS','US Internal Revenue Service' UNION ALL
    SELECT 'TP_TAX_BMF','Bundesministerium der Finanzen' UNION ALL
    SELECT 'TP_TAX_DGFIP','Direction generale des Finances publiques' UNION ALL
    SELECT 'TP_TAX_AEAT','Agencia Tributaria Espana' UNION ALL
    SELECT 'TP_TAX_NTA','National Tax Agency Japan' UNION ALL
    SELECT 'TP_TAX_SAT','Servicio Admin Tributaria Mexico' UNION ALL
    SELECT 'TP_TAX_RFB','Receita Federal Brasil'
) t;

UPDATE third_party SET name='FashionCo Iberica SL (Default)' WHERE code='TP_CUST_0042';

-- -----------------------------------------------------------------------------
-- 1.8 BANK_SERVICE_TYPE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE bank_service_type;
INSERT INTO bank_service_type (code, description, category) VALUES
    ('100','Account Maintenance Monthly','ACCOUNT_MAINT'),('150','Statement Delivery','ACCOUNT_MAINT'),
    ('250','Wire Out Domestic','WIRE_OUT'),('251','Wire Out International','WIRE_OUT'),
    ('252','Wire In Domestic','WIRE_IN'),('260','ACH Credit','ACH'),('261','ACH Debit','ACH'),
    ('270','SEPA Credit Transfer','SEPA'),('271','SEPA Direct Debit','SEPA'),('300','RTP Payment','RTP'),
    ('400','Lockbox Item','LOCKBOX'),('410','Remote Deposit Item','LOCKBOX'),
    ('500','FX Spot Transaction','FX'),('501','FX Forward Transaction','FX'),
    ('600','Cash Pickup','CASH_VAULT'),('601','Coin & Currency Order','CASH_VAULT'),
    ('700','Stop Payment','OTHER'),('800','Returned Item','OTHER'),('900','Foreign Cheque Negotiation','OTHER'),
    ('AAA','Liquidity Management Fee','POOL'),('AAB','Notional Pool Fee','POOL'),('AAC','ZBA Service Fee','POOL'),
    ('AAD','Reporting Fee','REPORTING'),('AAE','BAI Statement Fee','REPORTING'),('AAF','Positive Pay Service','FRAUD');

-- VERIFY
SELECT 'CURRENCY' AS t, COUNT(*) FROM currency
UNION ALL SELECT 'BANK', COUNT(*) FROM bank
UNION ALL SELECT 'BANK_BRANCH', COUNT(*) FROM bank_branch
UNION ALL SELECT 'COMPANY', COUNT(*) FROM company
UNION ALL SELECT 'BANK_ACCOUNT', COUNT(*) FROM bank_account
UNION ALL SELECT 'BANK_ACCOUNT_w_OPENING', COUNT(*) FROM bank_account WHERE initial_accounting_balance IS NOT NULL
UNION ALL SELECT 'CASH_FLOW_CODE', COUNT(*) FROM cash_flow_code
UNION ALL SELECT 'THIRD_PARTY', COUNT(*) FROM third_party;
