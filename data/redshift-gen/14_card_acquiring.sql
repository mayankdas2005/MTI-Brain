-- =============================================================================
-- 14_card_acquiring.sql (Redshift) — Section M of extensions #2.
--
-- Populates the card-acquiring / payments-operations stack:
--   acquirer, acquirer_contract, card_network, card_bin_range,
--   pos_transaction, card_authorization,
--   card_settlement_batch, card_settlement_line,
--   chargeback, ach_return,
--   card_rebate_program, card_rebate_earning,
--   membership_fee, payment_exception,
--   cross_border_payment_leg, fraud_loss,
--   acquirer_sla_metric, payment_hub_throughput.
--
-- VOLUMES (controlled by SCALE_FACTOR; first-cut targets in parens at SF=1.0):
--   pos_transaction       ~  800,000   (full-scale target 5-10M; capped here for speed)
--   card_authorization    ~  800,000   (1:1 with POS)
--   card_settlement_batch ~    8,000
--   card_settlement_line  ~  760,000   (≈ approved auths)
--   chargeback            ~   11,000   (~1.5% of approved auths)
--   ach_return            ~    2,500   (~0.4% of TRANSFER)
--   membership_fee        ~  300,000   (first-cut; full target 3-5M)
--   payment_exception     ~   12,000   (~2% of TRANSFER)
--   cross_border_leg      ~   60,000   (~10% of TRANSFER)
--   fraud_loss            ~      400
--   acquirer_sla_metric   ~    6,000   (8 acquirers × ~780 days)
--   payment_hub_throughput~    5,000
--
-- Scripted scenarios (drive demo prompts):
--   S1. 2024-09-15..2024-09-19 outage on CHASE_PAYTECH: response_time spikes to
--       >2000ms, auth_rate drops to ~92%, settlement_on_time_pct dips on the
--       affected SLA rows.        (Analyst-P #12, Manager-P #12)
--   S2. 2025-Q1 (Jan-Mar) settlement-late cluster on CHASE_PAYTECH/Visa batches:
--       bank_deposit_ts pushed to T+3 instead of T+1 on ~40 batches.
--   S3. 2025-Q3 fee-overage cluster on FIS_WORLDPAY: processor_margin_amount
--       runs 30% over expected on Jul-Sep batches.   (Analyst-P #16)
--   S4. 2025-Q2 chargeback ratio spike on 4 warehouses (WH_US_0007,
--       WH_US_0013, WH_US_0019, WH_US_0023) pushing > 1% Visa monitoring
--       threshold.                                    (Analyst-P #19)
--   S5. 12 R10 (unauthorized) ACH returns in 2025-H1 from GR_US_INC payroll BU
--       (Manager-P #19 fraud trend).
--   S6. Q4-2024 rebate miss on virtual-card JPM program (Manager-P #18).
--   S7. 2025-Q2 fraud-loss spike on ECOMMERCE membership renewals with
--       NO_3DS auth (Analyst-P #28).
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 14.1 ACQUIRER (8 rows, fixed)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE acquirer;
INSERT INTO acquirer (uuid, code, name, bank_ref, settlement_account_ref, region, is_strategic, onboarded_date)
SELECT gen_uuid('ACQ_'||code), code, name, bank_ref, settlement_account_ref, region, is_strat, onboard
FROM (
    SELECT 'CHASE_PAYTECH' AS code,'JPMorgan Chase Merchant Services' AS name,'BANK_JPM' AS bank_ref,'USA_RGNL_OPERATING' AS settlement_account_ref,'AMER' AS region,TRUE AS is_strat, DATE '2018-01-15' AS onboard UNION ALL
    SELECT 'FIS_WORLDPAY','FIS Worldpay','BANK_USR','GR_US_INC_OP_4','AMER',TRUE, DATE '2019-04-01' UNION ALL
    SELECT 'ADYEN','Adyen NV',NULL,'EUR_RGNL_OPERATING','EMEA',TRUE, DATE '2020-06-01' UNION ALL
    SELECT 'STRIPE','Stripe Payments',NULL,'USA_RGNL_OPERATING','AMER',FALSE, DATE '2021-09-15' UNION ALL
    SELECT 'FISERV','Fiserv (First Data)',NULL,'USA_RGNL_OPERATING','AMER',FALSE, DATE '2017-03-01' UNION ALL
    SELECT 'ELAVON','Elavon (US Bancorp)',NULL,'USA_RGNL_OPERATING','AMER',FALSE, DATE '2019-11-01' UNION ALL
    SELECT 'BANK_OF_AMERICA_MS','Bank of America Merchant Services','BANK_BAC','USA_RGNL_OPERATING','AMER',TRUE, DATE '2016-05-01' UNION ALL
    SELECT 'GLOBAL_PAYMENTS','Global Payments Inc',NULL,'EUR_RGNL_OPERATING','EMEA',FALSE, DATE '2020-02-15'
) t;

-- -----------------------------------------------------------------------------
-- 14.2 CARD_NETWORK
-- -----------------------------------------------------------------------------
TRUNCATE TABLE card_network;
INSERT INTO card_network (code, name, network_type) VALUES
    ('VISA','Visa','CREDIT'),
    ('MASTERCARD','Mastercard','CREDIT'),
    ('AMEX','American Express','CHARGE'),
    ('DISCOVER','Discover','CREDIT'),
    ('COSTCO_PRIVATE','Costco Private Label','PRIVATE_LABEL'),
    ('UNIONPAY','UnionPay','CREDIT'),
    ('JCB','JCB','CREDIT'),
    ('INTERAC','Interac','DEBIT_PIN'),
    ('STAR','STAR Debit','DEBIT_PIN'),
    ('NYCE','NYCE Debit','DEBIT_PIN'),
    ('PULSE','PULSE Debit','DEBIT_PIN');

-- -----------------------------------------------------------------------------
-- 14.3 CARD_BIN_RANGE (small reference set, ~25 rows)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE card_bin_range;
INSERT INTO card_bin_range (uuid, bin_low, bin_high, network_code, issuer_name, issuer_country, card_product)
SELECT gen_uuid('BIN_'||bin_low), bin_low, bin_high, network_code, issuer_name, issuer_country, card_product
FROM (
    SELECT '400000' AS bin_low,'409999' AS bin_high,'VISA' AS network_code,'Chase Issuer' AS issuer_name,'US' AS issuer_country,'CONSUMER_CREDIT' AS card_product UNION ALL
    SELECT '410000','419999','VISA','BofA Issuer','US','CONSUMER_CREDIT' UNION ALL
    SELECT '420000','429999','VISA','Wells Fargo Issuer','US','COMMERCIAL_CREDIT' UNION ALL
    SELECT '430000','439999','VISA','Capital One','US','CONSUMER_CREDIT' UNION ALL
    SELECT '440000','449999','VISA','Citi UK','GB','CONSUMER_CREDIT' UNION ALL
    SELECT '450000','459999','VISA','BNP Paribas','FR','CONSUMER_CREDIT' UNION ALL
    SELECT '460000','469999','VISA','RBC Canada','CA','CONSUMER_CREDIT' UNION ALL
    SELECT '470000','479999','VISA','BBVA Mexico','MX','CONSUMER_CREDIT' UNION ALL
    SELECT '510000','519999','MASTERCARD','Chase MC','US','CONSUMER_CREDIT' UNION ALL
    SELECT '520000','529999','MASTERCARD','HSBC UK','GB','CONSUMER_CREDIT' UNION ALL
    SELECT '530000','539999','MASTERCARD','Deutsche Bank','DE','CONSUMER_CREDIT' UNION ALL
    SELECT '540000','549999','MASTERCARD','MUFG Japan','JP','CONSUMER_CREDIT' UNION ALL
    SELECT '550000','559999','MASTERCARD','ANZ Australia','AU','CONSUMER_CREDIT' UNION ALL
    SELECT '370000','379999','AMEX','American Express US','US','CHARGE' UNION ALL
    SELECT '340000','349999','AMEX','Amex Business','US','CHARGE' UNION ALL
    SELECT '601100','601199','DISCOVER','Discover Bank','US','CONSUMER_CREDIT' UNION ALL
    SELECT '622100','622199','DISCOVER','Discover Commercial','US','COMMERCIAL_CREDIT' UNION ALL
    SELECT '620000','629999','UNIONPAY','ICBC','CN','CONSUMER_CREDIT' UNION ALL
    SELECT '352800','358999','JCB','JCB Japan','JP','CONSUMER_CREDIT' UNION ALL
    SELECT '450000','450099','INTERAC','RBC Debit','CA','DEBIT' UNION ALL
    SELECT '600100','600199','COSTCO_PRIVATE','Costco Private Label','US','PRIVATE_LABEL' UNION ALL
    SELECT '600200','600299','COSTCO_PRIVATE','Costco Business PL','US','PURCHASING'
) t;

-- -----------------------------------------------------------------------------
-- 14.4 ACQUIRER_CONTRACT  (one per acquirer; a few region-specific overrides)
-- One contract on CHASE_PAYTECH expires within 12 months of WINDOW_END.
-- -----------------------------------------------------------------------------
TRUNCATE TABLE acquirer_contract;
INSERT INTO acquirer_contract (
    uuid, acquirer_ref, company_ref, effective_from, effective_to, contract_status,
    processor_margin_bps, monthly_minimum_amount, monthly_minimum_currency,
    settlement_lag_business_days, uptime_sla_pct, auth_response_sla_ms,
    settlement_sla_business_days, renewal_notice_days, auto_renew
)
SELECT gen_uuid('ACQC_'||code), code, co, eff_from, eff_to, status,
       margin_bps, mn_amt, mn_ccy, lag_bd, uptime, sla_ms, sla_bd, notice, auto_ren
FROM (
    SELECT 'CHASE_PAYTECH' AS code, 'GR_US_INC' AS co, DATE '2022-01-01' AS eff_from, DATE '2026-12-31' AS eff_to,'ACTIVE' AS status,
           10.0::NUMERIC AS margin_bps, 50000::NUMERIC AS mn_amt,'USD' AS mn_ccy, 1::SMALLINT AS lag_bd, 99.95::NUMERIC AS uptime,
           500::INTEGER AS sla_ms, 1::SMALLINT AS sla_bd, 90::SMALLINT AS notice, FALSE AS auto_ren UNION ALL
    SELECT 'FIS_WORLDPAY','GR_US_INC', DATE '2021-04-01', DATE '2027-03-31','ACTIVE',12.0,30000,'USD',2,99.90,650,2,60,TRUE UNION ALL
    SELECT 'ADYEN','GR_EU_BV', DATE '2022-06-01', DATE '2027-05-31','ACTIVE',9.0,25000,'EUR',1,99.95,450,1,90,TRUE UNION ALL
    SELECT 'ADYEN','GR_APAC_PTE', DATE '2023-01-15', DATE '2028-01-14','ACTIVE',11.0,15000,'SGD',2,99.93,500,2,60,TRUE UNION ALL
    SELECT 'STRIPE','GR_US_INC', DATE '2022-09-15', DATE '2027-09-14','ACTIVE',13.0,10000,'USD',2,99.85,700,2,30,TRUE UNION ALL
    SELECT 'STRIPE','GR_GB', DATE '2023-03-01', DATE '2028-02-28','ACTIVE',13.5,8000,'GBP',2,99.85,700,2,30,TRUE UNION ALL
    SELECT 'FISERV','GR_US_INC', DATE '2020-03-01', DATE '2026-02-28','ACTIVE',11.0,40000,'USD',2,99.90,600,2,90,FALSE UNION ALL
    SELECT 'ELAVON','GR_US_INC', DATE '2021-11-01', DATE '2026-10-31','ACTIVE',12.5,20000,'USD',2,99.88,650,2,60,TRUE UNION ALL
    SELECT 'BANK_OF_AMERICA_MS','GR_US_INC', DATE '2022-05-01', DATE '2027-04-30','ACTIVE',10.5,45000,'USD',1,99.94,500,1,90,FALSE UNION ALL
    SELECT 'BANK_OF_AMERICA_MS','GR_CA', DATE '2022-05-01', DATE '2027-04-30','ACTIVE',11.5,20000,'CAD',2,99.92,550,2,90,FALSE UNION ALL
    SELECT 'GLOBAL_PAYMENTS','GR_EU_BV', DATE '2021-02-15', DATE '2026-08-15','ACTIVE',14.0,18000,'EUR',2,99.85,750,2,60,TRUE UNION ALL
    SELECT 'GLOBAL_PAYMENTS','GR_APAC_PTE', DATE '2022-07-01', DATE '2027-06-30','ACTIVE',13.5,12000,'SGD',2,99.85,750,2,60,TRUE
) t;

-- -----------------------------------------------------------------------------
-- 14.5 Helper temp tables: warehouses, eligible companies, network mix
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS t_warehouse;
CREATE TEMP TABLE t_warehouse AS
WITH companies AS (
    SELECT company_code, region FROM gen_company_region
    WHERE company_code IN ('GR_US_INC','GR_CA','GR_MX','GR_GB','GR_DE','GR_FR','GR_JP','GR_HK','GR_AU','GR_KR')
)
SELECT c.company_code, c.region,
       'WH_'||UPPER(SUBSTRING(c.company_code,4,2))||'_'||LPAD(CAST(n.n+1 AS VARCHAR),4,'0') AS location_code
FROM companies c
JOIN gen_numbers n ON n.n < CASE c.company_code
                              WHEN 'GR_US_INC' THEN 50  -- 50 US warehouses
                              WHEN 'GR_CA' THEN 8
                              WHEN 'GR_MX' THEN 5
                              WHEN 'GR_GB' THEN 12
                              WHEN 'GR_DE' THEN 8
                              WHEN 'GR_FR' THEN 6
                              WHEN 'GR_JP' THEN 10
                              WHEN 'GR_HK' THEN 3
                              WHEN 'GR_AU' THEN 6
                              WHEN 'GR_KR' THEN 4
                              ELSE 0 END;

-- Default acquirer per (company, channel) — used for routing
DROP TABLE IF EXISTS t_acq_route;
CREATE TEMP TABLE t_acq_route AS
SELECT * FROM (
    SELECT 'GR_US_INC' AS company_code,'IN_WAREHOUSE' AS channel,'CHASE_PAYTECH' AS acquirer UNION ALL
    SELECT 'GR_US_INC','ECOMMERCE','STRIPE' UNION ALL
    SELECT 'GR_US_INC','MEMBERSHIP_RENEWAL','BANK_OF_AMERICA_MS' UNION ALL
    SELECT 'GR_US_INC','MOBILE_APP','STRIPE' UNION ALL
    SELECT 'GR_US_INC','KIOSK','FIS_WORLDPAY' UNION ALL
    SELECT 'GR_CA','IN_WAREHOUSE','BANK_OF_AMERICA_MS' UNION ALL
    SELECT 'GR_CA','ECOMMERCE','STRIPE' UNION ALL
    SELECT 'GR_CA','MEMBERSHIP_RENEWAL','BANK_OF_AMERICA_MS' UNION ALL
    SELECT 'GR_CA','MOBILE_APP','STRIPE' UNION ALL SELECT 'GR_CA','KIOSK','FISERV' UNION ALL
    SELECT 'GR_MX','IN_WAREHOUSE','FISERV' UNION ALL SELECT 'GR_MX','ECOMMERCE','STRIPE' UNION ALL
    SELECT 'GR_MX','MEMBERSHIP_RENEWAL','FISERV' UNION ALL SELECT 'GR_MX','MOBILE_APP','STRIPE' UNION ALL SELECT 'GR_MX','KIOSK','FISERV' UNION ALL
    SELECT 'GR_GB','IN_WAREHOUSE','ADYEN' UNION ALL SELECT 'GR_GB','ECOMMERCE','STRIPE' UNION ALL
    SELECT 'GR_GB','MEMBERSHIP_RENEWAL','ADYEN' UNION ALL SELECT 'GR_GB','MOBILE_APP','STRIPE' UNION ALL SELECT 'GR_GB','KIOSK','GLOBAL_PAYMENTS' UNION ALL
    SELECT 'GR_DE','IN_WAREHOUSE','ADYEN' UNION ALL SELECT 'GR_DE','ECOMMERCE','ADYEN' UNION ALL
    SELECT 'GR_DE','MEMBERSHIP_RENEWAL','ADYEN' UNION ALL SELECT 'GR_DE','MOBILE_APP','ADYEN' UNION ALL SELECT 'GR_DE','KIOSK','GLOBAL_PAYMENTS' UNION ALL
    SELECT 'GR_FR','IN_WAREHOUSE','ADYEN' UNION ALL SELECT 'GR_FR','ECOMMERCE','ADYEN' UNION ALL
    SELECT 'GR_FR','MEMBERSHIP_RENEWAL','ADYEN' UNION ALL SELECT 'GR_FR','MOBILE_APP','ADYEN' UNION ALL SELECT 'GR_FR','KIOSK','GLOBAL_PAYMENTS' UNION ALL
    SELECT 'GR_JP','IN_WAREHOUSE','GLOBAL_PAYMENTS' UNION ALL SELECT 'GR_JP','ECOMMERCE','ADYEN' UNION ALL
    SELECT 'GR_JP','MEMBERSHIP_RENEWAL','GLOBAL_PAYMENTS' UNION ALL SELECT 'GR_JP','MOBILE_APP','ADYEN' UNION ALL SELECT 'GR_JP','KIOSK','GLOBAL_PAYMENTS' UNION ALL
    SELECT 'GR_HK','IN_WAREHOUSE','ADYEN' UNION ALL SELECT 'GR_HK','ECOMMERCE','ADYEN' UNION ALL
    SELECT 'GR_HK','MEMBERSHIP_RENEWAL','ADYEN' UNION ALL SELECT 'GR_HK','MOBILE_APP','ADYEN' UNION ALL SELECT 'GR_HK','KIOSK','GLOBAL_PAYMENTS' UNION ALL
    SELECT 'GR_AU','IN_WAREHOUSE','ADYEN' UNION ALL SELECT 'GR_AU','ECOMMERCE','ADYEN' UNION ALL
    SELECT 'GR_AU','MEMBERSHIP_RENEWAL','ADYEN' UNION ALL SELECT 'GR_AU','MOBILE_APP','ADYEN' UNION ALL SELECT 'GR_AU','KIOSK','GLOBAL_PAYMENTS' UNION ALL
    SELECT 'GR_KR','IN_WAREHOUSE','GLOBAL_PAYMENTS' UNION ALL SELECT 'GR_KR','ECOMMERCE','ADYEN' UNION ALL
    SELECT 'GR_KR','MEMBERSHIP_RENEWAL','GLOBAL_PAYMENTS' UNION ALL SELECT 'GR_KR','MOBILE_APP','ADYEN' UNION ALL SELECT 'GR_KR','KIOSK','GLOBAL_PAYMENTS'
) x;

-- -----------------------------------------------------------------------------
-- 14.6 POS_TRANSACTION
-- Volume: ~800k rows (scaled). One row per day-warehouse-shift, ~20-50/day.
-- -----------------------------------------------------------------------------
TRUNCATE TABLE pos_transaction;

DROP TABLE IF EXISTS t_pos_raw;
CREATE TEMP TABLE t_pos_raw AS
WITH base AS (
    SELECT n.n AS rn
    FROM gen_numbers n
    WHERE n.n < CAST(800000 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR') AS INTEGER)
),
calendar AS (
    SELECT calendar_date, ROW_NUMBER() OVER (ORDER BY calendar_date) - 1 AS day_idx
    FROM gen_calendar
),
n_days AS ( SELECT COUNT(*) AS d FROM calendar ),
wh_indexed AS (
    SELECT w.*, ROW_NUMBER() OVER (ORDER BY company_code, location_code) - 1 AS wh_idx
    FROM t_warehouse w
),
n_wh AS ( SELECT COUNT(*) AS w FROM wh_indexed )
SELECT
    b.rn,
    cal.calendar_date AS txn_date,
    wh.company_code, wh.region, wh.location_code,
    -- Channel mix: 70% IN_WAREHOUSE, 22% ECOMMERCE, 4% MEMBERSHIP_RENEWAL, 3% MOBILE_APP, 1% KIOSK
    CASE WHEN MOD(ABS(FNV_HASH('POS_CH_'||CAST(b.rn AS VARCHAR))),100) < 70 THEN 'IN_WAREHOUSE'
         WHEN MOD(ABS(FNV_HASH('POS_CH_'||CAST(b.rn AS VARCHAR))),100) < 92 THEN 'ECOMMERCE'
         WHEN MOD(ABS(FNV_HASH('POS_CH_'||CAST(b.rn AS VARCHAR))),100) < 96 THEN 'MEMBERSHIP_RENEWAL'
         WHEN MOD(ABS(FNV_HASH('POS_CH_'||CAST(b.rn AS VARCHAR))),100) < 99 THEN 'MOBILE_APP'
         ELSE 'KIOSK' END AS channel,
    gen_rand('POS_AMT_'||CAST(b.rn AS VARCHAR)) AS amt_rand,
    gen_rand('POS_TIME_'||CAST(b.rn AS VARCHAR)) AS time_rand
FROM base b,
     n_days,
     n_wh,
     calendar cal,
     wh_indexed wh
WHERE cal.day_idx = MOD(ABS(FNV_HASH('POS_DAY_'||CAST(b.rn AS VARCHAR))), n_days.d)
  AND wh.wh_idx  = MOD(ABS(FNV_HASH('POS_WH_'||CAST(b.rn AS VARCHAR))), n_wh.w);

INSERT INTO pos_transaction (
    uuid, company_ref, channel, location_code, transaction_ts, amount, currency_code,
    payment_method, member_id, register_id
)
SELECT
    gen_uuid('POS_'||CAST(p.rn AS VARCHAR)),
    p.company_code,
    p.channel,
    CASE WHEN p.channel='ECOMMERCE' THEN NULL ELSE p.location_code END,
    (p.txn_date + (p.time_rand * 14 + 8) * INTERVAL '1 hour')::TIMESTAMPTZ,
    -- Amount distribution: skewed; most $20-$500, tail to $5000
    ROUND(CASE
      WHEN p.amt_rand < 0.40 THEN 15 + p.amt_rand * 100
      WHEN p.amt_rand < 0.80 THEN 50 + (p.amt_rand-0.40) * 1000
      WHEN p.amt_rand < 0.97 THEN 250 + (p.amt_rand-0.80) * 3000
      ELSE 1000 + (p.amt_rand-0.97) * 50000
    END * region_seasonality(p.txn_date, p.region) * yoy_growth(p.txn_date), 2),
    CASE p.region WHEN 'AMER' THEN
            CASE p.company_code WHEN 'GR_US_INC' THEN 'USD' WHEN 'GR_CA' THEN 'CAD' ELSE 'MXN' END
         WHEN 'EMEA' THEN
            CASE p.company_code WHEN 'GR_GB' THEN 'GBP' ELSE 'EUR' END
         WHEN 'APAC' THEN
            CASE p.company_code WHEN 'GR_JP' THEN 'JPY' WHEN 'GR_HK' THEN 'HKD'
                                WHEN 'GR_AU' THEN 'AUD' WHEN 'GR_KR' THEN 'KRW' ELSE 'USD' END
         ELSE 'USD' END,
    CASE WHEN MOD(ABS(FNV_HASH('PM_'||CAST(p.rn AS VARCHAR))),100) < 78 THEN 'CARD'
         WHEN MOD(ABS(FNV_HASH('PM_'||CAST(p.rn AS VARCHAR))),100) < 88 THEN 'CASH'
         WHEN MOD(ABS(FNV_HASH('PM_'||CAST(p.rn AS VARCHAR))),100) < 93 THEN 'CHECK'
         WHEN MOD(ABS(FNV_HASH('PM_'||CAST(p.rn AS VARCHAR))),100) < 98 THEN 'MOBILE_WALLET'
         ELSE 'GIFT_CARD' END,
    -- Members on ~85% of txns (warehouse-club model)
    CASE WHEN MOD(ABS(FNV_HASH('MEM_'||CAST(p.rn AS VARCHAR))),100) < 85
         THEN 'MBR-'||LPAD(CAST(MOD(ABS(FNV_HASH('M2_'||CAST(p.rn AS VARCHAR))),250000)+1 AS VARCHAR),9,'0')
         ELSE NULL END,
    'REG-'||LPAD(CAST(MOD(ABS(FNV_HASH('R_'||CAST(p.rn AS VARCHAR))),24)+1 AS VARCHAR),2,'0')
FROM t_pos_raw p;

-- -----------------------------------------------------------------------------
-- 14.7 CARD_AUTHORIZATION
-- Only generate auths for CARD/MOBILE_WALLET payment methods (~83% of POS)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE card_authorization;

INSERT INTO card_authorization (
    uuid, pos_transaction_ref, acquirer_ref, network_code, bin_first6,
    auth_request_ts, auth_response_ts, response_time_ms,
    decision, decline_reason_code, amount, currency_code,
    transaction_size_band, region, auth_3ds_status, device_fingerprint, cnp_indicator
)
SELECT
    gen_uuid('AUTH_'||p.uuid),
    p.uuid,
    ar.acquirer,
    -- Network mix: 35% VISA, 30% MC, 12% AMEX, 10% DISCOVER, 8% COSTCO_PRIVATE, 5% others
    CASE WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 35 THEN 'VISA'
         WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 65 THEN 'MASTERCARD'
         WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 77 THEN 'AMEX'
         WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 87 THEN 'DISCOVER'
         WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 95 THEN 'COSTCO_PRIVATE'
         WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 97 THEN 'UNIONPAY'
         WHEN MOD(ABS(FNV_HASH('NET_'||p.uuid)),100) < 98 THEN 'JCB'
         ELSE 'INTERAC' END,
    LPAD(CAST(MOD(ABS(FNV_HASH('BIN_'||p.uuid)),900000)+100000 AS VARCHAR),6,'0'),
    p.transaction_ts,
    DATEADD(millisecond,
        CASE
          -- Scripted outage S1: 2024-09-15..09-19 on CHASE_PAYTECH → spike to 2000-3500ms
          WHEN ar.acquirer = 'CHASE_PAYTECH' AND p.transaction_ts::DATE BETWEEN DATE '2024-09-15' AND DATE '2024-09-19'
            THEN 2000 + CAST(gen_rand('RT_OUT_'||p.uuid) * 1500 AS INTEGER)
          ELSE 180 + CAST(gen_rand('RT_'||p.uuid) * 620 AS INTEGER)
        END, p.transaction_ts::TIMESTAMP)::TIMESTAMPTZ,
    CASE
      WHEN ar.acquirer = 'CHASE_PAYTECH' AND p.transaction_ts::DATE BETWEEN DATE '2024-09-15' AND DATE '2024-09-19'
        THEN 2000 + CAST(gen_rand('RT_OUT_'||p.uuid) * 1500 AS INTEGER)
      ELSE 180 + CAST(gen_rand('RT_'||p.uuid) * 620 AS INTEGER) END,
    -- Decision mix: 95% APPROVED, 4% DECLINED, 0.7% TIMEOUT, 0.3% ERROR
    -- During the outage approval drops to 92%
    CASE
      WHEN ar.acquirer='CHASE_PAYTECH' AND p.transaction_ts::DATE BETWEEN DATE '2024-09-15' AND DATE '2024-09-19' THEN
        CASE WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) < 920 THEN 'APPROVED'
             WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) < 970 THEN 'DECLINED'
             WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) < 995 THEN 'TIMEOUT'
             ELSE 'ERROR' END
      ELSE
        CASE WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) < 950 THEN 'APPROVED'
             WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) < 990 THEN 'DECLINED'
             WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) < 997 THEN 'TIMEOUT'
             ELSE 'ERROR' END
    END,
    CASE WHEN MOD(ABS(FNV_HASH('DEC_'||p.uuid)),1000) BETWEEN 950 AND 989
         THEN CASE MOD(ABS(FNV_HASH('DRC_'||p.uuid)),5)
                WHEN 0 THEN '51' WHEN 1 THEN '05' WHEN 2 THEN '14' WHEN 3 THEN '54' ELSE '57' END
         ELSE NULL END,
    p.amount, p.currency_code,
    CASE WHEN p.amount < 25 THEN 'LT_25'
         WHEN p.amount < 100 THEN '25_100'
         WHEN p.amount < 500 THEN '100_500'
         WHEN p.amount < 5000 THEN '500_5000'
         ELSE 'GT_5000' END,
    cr.region,
    CASE WHEN p.channel = 'IN_WAREHOUSE' THEN 'NOT_ATTEMPTED'
         WHEN p.channel = 'ECOMMERCE' AND p.amount < 100 THEN 'FRICTIONLESS'
         WHEN p.channel = 'ECOMMERCE' AND p.amount >= 100 THEN
           CASE WHEN MOD(ABS(FNV_HASH('TDS_'||p.uuid)),100) < 85 THEN 'CHALLENGED'
                ELSE 'FAILED' END
         WHEN p.channel = 'MEMBERSHIP_RENEWAL' THEN
           CASE WHEN MOD(ABS(FNV_HASH('TDS_'||p.uuid)),100) < 60 THEN 'FRICTIONLESS'
                WHEN MOD(ABS(FNV_HASH('TDS_'||p.uuid)),100) < 90 THEN 'CHALLENGED'
                ELSE 'FAILED' END
         ELSE 'NOT_ATTEMPTED' END,
    'DEV-'||SUBSTRING(SHA2(p.uuid,256),1,16),
    CASE WHEN p.channel IN ('ECOMMERCE','MEMBERSHIP_RENEWAL','MOBILE_APP') THEN TRUE ELSE FALSE END
FROM pos_transaction p
JOIN t_acq_route ar ON ar.company_code = p.company_ref AND ar.channel = p.channel
JOIN gen_company_region cr ON cr.company_code = p.company_ref
WHERE p.payment_method IN ('CARD','MOBILE_WALLET');

-- -----------------------------------------------------------------------------
-- 14.8 CARD_SETTLEMENT_BATCH  (one per acquirer × network × ccy × business day)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE card_settlement_batch;

DROP TABLE IF EXISTS t_batch_agg;
CREATE TEMP TABLE t_batch_agg AS
SELECT
    ca.acquirer_ref, ca.network_code, ca.currency_code,
    ca.auth_response_ts::DATE AS batch_date,
    COUNT(*) AS txn_count,
    SUM(ca.amount) AS gross_sales
FROM card_authorization ca
WHERE ca.decision = 'APPROVED'
GROUP BY ca.acquirer_ref, ca.network_code, ca.currency_code, ca.auth_response_ts::DATE;

INSERT INTO card_settlement_batch (
    uuid, acquirer_ref, network_code, settlement_currency,
    batch_close_ts, processor_settle_ts, bank_deposit_ts, bank_account_ref,
    gross_sales_amount, refund_amount, chargeback_amount,
    interchange_amount, network_assessment_amount, processor_margin_amount, other_fees_amount,
    net_settlement_amount, transaction_count, sla_met
)
WITH rounded AS (
    -- Round each component independently, then derive net as the exact difference
    -- so V35 (gross - sum(components) - net = 0) holds to the cent.
    SELECT
        b.*,
        ROUND(b.gross_sales, 2) AS gross_r,
        ROUND(b.gross_sales * 0.012, 2) AS refund_r,
        ROUND(b.gross_sales * 0.003, 2) AS chargeback_r,
        ROUND(b.gross_sales * CASE b.network_code
                WHEN 'VISA' THEN 0.0155 WHEN 'MASTERCARD' THEN 0.0160
                WHEN 'AMEX' THEN 0.0245 WHEN 'DISCOVER' THEN 0.0165
                WHEN 'COSTCO_PRIVATE' THEN 0.0040
                ELSE 0.0180 END, 2) AS interchange_r,
        ROUND(b.gross_sales * 0.0014, 2) AS assess_r,
        ROUND(b.gross_sales *
            CASE WHEN b.acquirer_ref='FIS_WORLDPAY'
                      AND b.batch_date BETWEEN DATE '2025-07-01' AND DATE '2025-09-30'
                 THEN 0.0012 * 1.30
                 ELSE 0.0012
            END, 2) AS margin_r,
        ROUND(b.gross_sales * 0.0003, 2) AS other_r
    FROM t_batch_agg b
)
SELECT
    gen_uuid('SB_'||r.acquirer_ref||'_'||r.network_code||'_'||r.currency_code||'_'||TO_CHAR(r.batch_date,'YYYYMMDD')),
    r.acquirer_ref, r.network_code, r.currency_code,
    (r.batch_date + INTERVAL '22 hour')::TIMESTAMPTZ,
    (r.batch_date + INTERVAL '23 hour')::TIMESTAMPTZ,
    (add_business_days(r.batch_date,
        CASE
          WHEN r.acquirer_ref='CHASE_PAYTECH' AND r.network_code='VISA'
               AND r.batch_date BETWEEN DATE '2025-01-15' AND DATE '2025-03-15'
               AND MOD(ABS(FNV_HASH('LATE_'||TO_CHAR(r.batch_date,'YYYYMMDD'))),10) < 5
            THEN 3
          WHEN r.acquirer_ref='CHASE_PAYTECH' THEN 1
          WHEN r.acquirer_ref='BANK_OF_AMERICA_MS' THEN 1
          ELSE 2
        END
    ) + INTERVAL '8 hour')::TIMESTAMPTZ,
    (SELECT settlement_account_ref FROM acquirer WHERE code = r.acquirer_ref),
    r.gross_r,
    r.refund_r,
    r.chargeback_r,
    r.interchange_r,
    r.assess_r,
    r.margin_r,
    r.other_r,
    -- Net = exact difference of already-rounded components (balances to the cent)
    r.gross_r - r.refund_r - r.chargeback_r - r.interchange_r - r.assess_r - r.margin_r - r.other_r,
    r.txn_count,
    NOT (r.acquirer_ref='CHASE_PAYTECH' AND r.network_code='VISA'
         AND r.batch_date BETWEEN DATE '2025-01-15' AND DATE '2025-03-15'
         AND MOD(ABS(FNV_HASH('LATE_'||TO_CHAR(r.batch_date,'YYYYMMDD'))),10) < 5)
FROM rounded r;

-- -----------------------------------------------------------------------------
-- 14.9 CARD_SETTLEMENT_LINE — one per approved authorization
-- -----------------------------------------------------------------------------
TRUNCATE TABLE card_settlement_line;

INSERT INTO card_settlement_line (
    uuid, batch_ref, authorization_ref, gross_amount,
    interchange_amount, interchange_bps,
    network_assessment_amount, processor_margin_amount, other_fees_amount, net_amount,
    issuer_country, cross_border, dcc_applied, fx_rate_applied
)
SELECT
    gen_uuid('SL_'||ca.uuid),
    gen_uuid('SB_'||ca.acquirer_ref||'_'||ca.network_code||'_'||ca.currency_code||'_'||TO_CHAR(ca.auth_response_ts::DATE,'YYYYMMDD')),
    ca.uuid,
    ROUND(ca.amount, 2),
    ROUND(ca.amount * CASE ca.network_code WHEN 'VISA' THEN 0.0155 WHEN 'MASTERCARD' THEN 0.0160
                                            WHEN 'AMEX' THEN 0.0245 WHEN 'DISCOVER' THEN 0.0165
                                            WHEN 'COSTCO_PRIVATE' THEN 0.0040 ELSE 0.0180 END, 4),
    CASE ca.network_code WHEN 'VISA' THEN 155 WHEN 'MASTERCARD' THEN 160
                          WHEN 'AMEX' THEN 245 WHEN 'DISCOVER' THEN 165
                          WHEN 'COSTCO_PRIVATE' THEN 40 ELSE 180 END,
    ROUND(ca.amount * 0.0014, 4),
    ROUND(ca.amount * CASE WHEN ca.acquirer_ref='FIS_WORLDPAY'
                                AND ca.auth_response_ts::DATE BETWEEN DATE '2025-07-01' AND DATE '2025-09-30'
                            THEN 0.00156 ELSE 0.0012 END, 4),
    ROUND(ca.amount * 0.0003, 4),
    ROUND(ca.amount * (1
        - CASE ca.network_code WHEN 'VISA' THEN 0.0155 WHEN 'MASTERCARD' THEN 0.0160
                                WHEN 'AMEX' THEN 0.0245 WHEN 'DISCOVER' THEN 0.0165
                                WHEN 'COSTCO_PRIVATE' THEN 0.0040 ELSE 0.0180 END
        - 0.0014
        - CASE WHEN ca.acquirer_ref='FIS_WORLDPAY'
                    AND ca.auth_response_ts::DATE BETWEEN DATE '2025-07-01' AND DATE '2025-09-30'
               THEN 0.00156 ELSE 0.0012 END
        - 0.0003), 4),
    CASE MOD(ABS(FNV_HASH('ICC_'||ca.uuid)),20)
      WHEN 0 THEN 'CA' WHEN 1 THEN 'MX' WHEN 2 THEN 'GB' WHEN 3 THEN 'FR'
      WHEN 4 THEN 'DE' WHEN 5 THEN 'JP' WHEN 6 THEN 'CN' WHEN 7 THEN 'AU'
      ELSE 'US' END,
    CASE WHEN MOD(ABS(FNV_HASH('CB_'||ca.uuid)),100) < 10 THEN TRUE ELSE FALSE END,
    CASE WHEN MOD(ABS(FNV_HASH('DCC_'||ca.uuid)),100) < 3 THEN TRUE ELSE FALSE END,
    CASE WHEN MOD(ABS(FNV_HASH('CB_'||ca.uuid)),100) < 10
         THEN 0.95 + gen_rand('FX_'||ca.uuid) * 0.10 ELSE NULL END
FROM card_authorization ca
WHERE ca.decision = 'APPROVED';

-- -----------------------------------------------------------------------------
-- 14.10 CHARGEBACK (~1.5% of approved auths, with Q2-2025 cluster on 4 wh)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE chargeback;

INSERT INTO chargeback (
    uuid, authorization_ref, acquirer_ref, network_code, company_ref, location_code,
    reason_code, reason_category, initiated_date, resolved_date, amount, currency_code,
    status, representment_attempted, representment_evidence_uri, classification
)
SELECT
    gen_uuid('CB_'||ca.uuid),
    ca.uuid, ca.acquirer_ref, ca.network_code, p.company_ref, p.location_code,
    CASE MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100)
      WHEN 0 THEN '10.4' WHEN 1 THEN '10.4' WHEN 2 THEN '10.4'  -- FRAUD
      ELSE
        CASE WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 35 THEN '10.4'
             WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 60 THEN '13.1'
             WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 80 THEN '13.3'
             WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 90 THEN '12.1'
             ELSE '4855' END
    END,
    CASE WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 35 THEN 'FRAUD'
         WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 60 THEN 'NON_RECEIPT'
         WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 80 THEN 'NOT_AS_DESCRIBED'
         WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 90 THEN 'DUPLICATE'
         ELSE 'TECHNICAL' END,
    DATEADD(day, 15 + CAST(gen_rand('CBD_'||ca.uuid) * 45 AS INTEGER), ca.auth_response_ts::DATE),
    DATEADD(day, 60 + CAST(gen_rand('CBR_'||ca.uuid) * 90 AS INTEGER), ca.auth_response_ts::DATE),
    ca.amount, ca.currency_code,
    CASE WHEN MOD(ABS(FNV_HASH('CBS_'||ca.uuid)),100) < 55 THEN 'WON'
         WHEN MOD(ABS(FNV_HASH('CBS_'||ca.uuid)),100) < 80 THEN 'LOST'
         WHEN MOD(ABS(FNV_HASH('CBS_'||ca.uuid)),100) < 95 THEN 'REPRESENTED'
         ELSE 'INITIATED' END,
    CASE WHEN MOD(ABS(FNV_HASH('CBS_'||ca.uuid)),100) < 95 THEN TRUE ELSE FALSE END,
    's3://lpp-evidence/cb/'||SUBSTRING(SHA2(ca.uuid,256),1,12)||'.pdf',
    CASE WHEN MOD(ABS(FNV_HASH('RC_'||ca.uuid)),100) < 35 THEN
           CASE WHEN MOD(ABS(FNV_HASH('CLS_'||ca.uuid)),100) < 60 THEN 'TRUE_FRAUD'
                ELSE 'FRIENDLY_FRAUD' END
         ELSE 'MERCHANT_ERROR' END
FROM card_authorization ca
JOIN pos_transaction p ON p.uuid = ca.pos_transaction_ref
WHERE ca.decision = 'APPROVED'
  AND (
        -- Baseline ~1.4%
        MOD(ABS(FNV_HASH('CB_PICK_'||ca.uuid)),10000) < 140
     OR -- Q2-2025 spike on 4 specific warehouses to push >1% Visa threshold
        ( p.transaction_ts::DATE BETWEEN DATE '2025-04-01' AND DATE '2025-06-30'
          AND p.location_code IN ('WH_US_0007','WH_US_0013','WH_US_0019','WH_US_0023')
          AND ca.network_code='VISA'
          AND MOD(ABS(FNV_HASH('CB_SPIKE_'||ca.uuid)),100) < 12 )
      );

-- -----------------------------------------------------------------------------
-- 14.11 ACH_RETURN (~0.4% of TRANSFER rows + scripted 12 R10s)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE ach_return;

INSERT INTO ach_return (
    uuid, original_transfer_ref, company_ref, business_unit_code, return_date,
    return_reason_code, return_category, direction, amount, currency_code,
    bank_account_ref, resolved
)
SELECT
    gen_uuid('ACHR_'||t.uuid),
    t.uuid,
    ba.company_ref,
    'BU_'||UPPER(SUBSTRING(ba.company_ref,4,5)),
    DATEADD(day, 2 + CAST(gen_rand('AR_D_'||t.uuid) * 5 AS INTEGER), t.value_date),
    CASE MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100)
      WHEN 0 THEN 'R01' WHEN 1 THEN 'R01' WHEN 2 THEN 'R01' WHEN 3 THEN 'R01'
      ELSE
        CASE WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 40 THEN 'R01'
             WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 55 THEN 'R02'
             WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 65 THEN 'R03'
             WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 75 THEN 'R10'
             WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 80 THEN 'R29'
             WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 90 THEN 'R07'
             ELSE 'R09' END
    END,
    CASE WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 40 THEN 'INSUFFICIENT_FUNDS'
         WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 55 THEN 'INVALID_ACCT'
         WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 65 THEN 'INVALID_ACCT'
         WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 75 THEN 'UNAUTHORIZED'
         WHEN MOD(ABS(FNV_HASH('AR_RC_'||t.uuid)),100) < 80 THEN 'UNAUTHORIZED'
         ELSE 'OTHER' END,
    'DEBIT',
    t.amount, t.currency_code,
    (SELECT account_ref FROM payment_file WHERE file_uuid = t.file_uuid LIMIT 1),
    TRUE
FROM transfer t
JOIN payment_file pf ON pf.file_uuid = t.file_uuid
JOIN bank_account ba ON ba.code = pf.account_ref
WHERE t.payment_rail IN ('ACH','SEPA_CT','SEPA_DD','FEDNOW')
  AND MOD(ABS(FNV_HASH('ACHR_PICK_'||t.uuid)),10000) < 40;

-- Scripted: 12 R10 unauthorized returns on GR_US_INC payroll BU in 2025-H1
INSERT INTO ach_return (
    uuid, original_transfer_ref, company_ref, business_unit_code, return_date,
    return_reason_code, return_category, direction, amount, currency_code,
    bank_account_ref, resolved
)
SELECT
    gen_uuid('ACHR_SCRIPT_R10_'||CAST(n.n AS VARCHAR)),
    NULL,
    'GR_US_INC',
    'BU_USINC_PAYROLL',
    DATEADD(day, n.n * 12, DATE '2025-01-15'),
    'R10','UNAUTHORIZED','DEBIT',
    ROUND(2500 + gen_rand('R10_'||CAST(n.n AS VARCHAR)) * 7500, 2),'USD',
    'GR_US_INC_PAYROLL_1', FALSE
FROM gen_numbers n WHERE n.n < 12;

-- -----------------------------------------------------------------------------
-- 14.12 CARD_REBATE_PROGRAM + EARNINGS
-- -----------------------------------------------------------------------------
TRUNCATE TABLE card_rebate_program;
INSERT INTO card_rebate_program (
    uuid, code, issuer_bank_ref, company_ref, program_type,
    rebate_tier_threshold, rebate_bps_at_target, effective_from, effective_to
) VALUES
    (gen_uuid('REB_JPM_VCARD'),'REB_JPM_VCARD','BANK_JPM','GR_HOLDINGS','VIRTUAL_CARD',  500000000, 150, DATE '2023-01-01', DATE '2027-12-31'),
    (gen_uuid('REB_CITI_COMM'),'REB_CITI_COMM','BANK_CITI','GR_HOLDINGS','COMMERCIAL_CARD', 200000000, 110, DATE '2023-01-01', DATE '2027-12-31'),
    (gen_uuid('REB_AMEX_BUS'),'REB_AMEX_BUS','BANK_BAC','GR_US_INC','PURCHASING_CARD',     80000000,  95, DATE '2023-07-01', DATE '2027-06-30'),
    (gen_uuid('REB_BNP_PCARD'),'REB_BNP_PCARD','BANK_BNP','GR_EU_BV','PURCHASING_CARD',    50000000,  85, DATE '2023-06-01', DATE '2027-05-31');

TRUNCATE TABLE card_rebate_earning;
-- Scripted S6: Q4-2024 on REB_JPM_VCARD virtual-card program is dialed down
-- 35% (and bps drop) so the program misses its annual rebate_tier_threshold.
INSERT INTO card_rebate_earning (
    uuid, program_ref, company_ref, period_date, spend_category,
    eligible_spend, rebate_earned, currency_code, tier_achieved
)
SELECT
    gen_uuid('REBEARN_'||spend_base.prog_code||'_'||TO_CHAR(q.qend,'YYYYMMDD')||'_'||spend_base.cat_code),
    spend_base.prog_code,
    prog.company_ref,
    q.qend,
    spend_base.cat_code,
    ROUND(
      CASE WHEN spend_base.prog_code='REB_JPM_VCARD' AND q.qend=DATE '2024-12-31'
           THEN spend_base.spend * 0.65
           ELSE spend_base.spend END
      * (0.9 + gen_rand('SP_'||spend_base.prog_code||spend_base.cat_code||TO_CHAR(q.qend,'YYYYMMDD'))*0.2)
    , 2),
    ROUND(
      ( CASE WHEN spend_base.prog_code='REB_JPM_VCARD' AND q.qend=DATE '2024-12-31'
             THEN spend_base.spend * 0.65 ELSE spend_base.spend END
        * (0.9 + gen_rand('SP_'||spend_base.prog_code||spend_base.cat_code||TO_CHAR(q.qend,'YYYYMMDD'))*0.2) )
      * (CASE WHEN spend_base.prog_code='REB_JPM_VCARD' AND q.qend=DATE '2024-12-31' THEN 70 ELSE spend_base.bps END)
      / 10000.0
    , 2),
    prog.currency,
    CASE WHEN spend_base.prog_code='REB_JPM_VCARD' AND q.qend=DATE '2024-12-31' THEN 'BASE' ELSE spend_base.tier END
FROM (
    SELECT 'REB_JPM_VCARD' AS prog_code,'INVENTORY' AS cat_code, 75000000::NUMERIC AS spend, 150 AS bps, 'TIER2' AS tier UNION ALL
    SELECT 'REB_JPM_VCARD','TRAVEL',    12000000, 100, 'TIER1' UNION ALL
    SELECT 'REB_JPM_VCARD','UTILITIES',  8000000,  75, 'BASE' UNION ALL
    SELECT 'REB_JPM_VCARD','LOGISTICS', 22000000, 130, 'TIER1' UNION ALL
    SELECT 'REB_JPM_VCARD','OTHER',     10000000,  75, 'BASE' UNION ALL
    SELECT 'REB_CITI_COMM','INVENTORY', 28000000, 110, 'TIER2' UNION ALL
    SELECT 'REB_CITI_COMM','TRAVEL',     6000000,  90, 'TIER1' UNION ALL
    SELECT 'REB_CITI_COMM','UTILITIES',  3000000,  70, 'BASE' UNION ALL
    SELECT 'REB_CITI_COMM','LOGISTICS', 10000000, 100, 'TIER1' UNION ALL
    SELECT 'REB_CITI_COMM','OTHER',      4000000,  70, 'BASE' UNION ALL
    SELECT 'REB_AMEX_BUS','INVENTORY',  12000000,  95, 'TIER1' UNION ALL
    SELECT 'REB_AMEX_BUS','TRAVEL',      3500000,  80, 'BASE' UNION ALL
    SELECT 'REB_AMEX_BUS','UTILITIES',   1500000,  60, 'BASE' UNION ALL
    SELECT 'REB_AMEX_BUS','LOGISTICS',   5000000,  85, 'TIER1' UNION ALL
    SELECT 'REB_AMEX_BUS','OTHER',       2000000,  60, 'BASE' UNION ALL
    SELECT 'REB_BNP_PCARD','INVENTORY',  7500000,  85, 'TIER1' UNION ALL
    SELECT 'REB_BNP_PCARD','TRAVEL',     2000000,  70, 'BASE' UNION ALL
    SELECT 'REB_BNP_PCARD','UTILITIES',  1000000,  55, 'BASE' UNION ALL
    SELECT 'REB_BNP_PCARD','LOGISTICS',  3000000,  75, 'BASE' UNION ALL
    SELECT 'REB_BNP_PCARD','OTHER',      1500000,  55, 'BASE'
) spend_base
JOIN (
    SELECT 'REB_JPM_VCARD' AS code,'GR_HOLDINGS' AS company_ref,'USD' AS currency UNION ALL
    SELECT 'REB_CITI_COMM','GR_HOLDINGS','USD' UNION ALL
    SELECT 'REB_AMEX_BUS','GR_US_INC','USD' UNION ALL
    SELECT 'REB_BNP_PCARD','GR_EU_BV','EUR'
) prog ON prog.code = spend_base.prog_code
CROSS JOIN (
    SELECT DATE '2023-09-30' AS qend UNION ALL SELECT DATE '2023-12-31' UNION ALL
    SELECT DATE '2024-03-31' UNION ALL SELECT DATE '2024-06-30' UNION ALL
    SELECT DATE '2024-09-30' UNION ALL SELECT DATE '2024-12-31' UNION ALL
    SELECT DATE '2025-03-31' UNION ALL SELECT DATE '2025-06-30' UNION ALL
    SELECT DATE '2025-09-30' UNION ALL SELECT DATE '2025-12-31' UNION ALL
    SELECT DATE '2026-03-31'
) q;

-- -----------------------------------------------------------------------------
-- 14.13 MEMBERSHIP_FEE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE membership_fee;

DROP TABLE IF EXISTS t_member_raw;
CREATE TEMP TABLE t_member_raw AS
WITH base AS (
    SELECT n.n AS rn FROM gen_numbers n
    WHERE n.n < CAST(300000 * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR') AS INTEGER)
),
n_days AS ( SELECT COUNT(*) AS d FROM gen_calendar ),
calendar AS (
    SELECT calendar_date, ROW_NUMBER() OVER (ORDER BY calendar_date) - 1 AS day_idx FROM gen_calendar
)
SELECT b.rn, cal.calendar_date AS issued_date
FROM base b, n_days, calendar cal
WHERE cal.day_idx = MOD(ABS(FNV_HASH('MD_'||CAST(b.rn AS VARCHAR))), n_days.d);

INSERT INTO membership_fee (
    uuid, member_id, company_ref, channel, membership_tier, issued_date, expiration_date,
    fee_amount, currency_code, payment_method, status, fraud_loss
)
SELECT
    gen_uuid('MEM_'||CAST(m.rn AS VARCHAR)),
    'MBR-'||LPAD(CAST(MOD(ABS(FNV_HASH('MID_'||CAST(m.rn AS VARCHAR))),250000)+1 AS VARCHAR),9,'0'),
    CASE MOD(ABS(FNV_HASH('MCO_'||CAST(m.rn AS VARCHAR))),10)
      WHEN 0 THEN 'GR_CA' WHEN 1 THEN 'GR_MX' WHEN 2 THEN 'GR_GB'
      WHEN 3 THEN 'GR_JP' WHEN 4 THEN 'GR_AU' ELSE 'GR_US_INC' END,
    CASE MOD(ABS(FNV_HASH('MCH_'||CAST(m.rn AS VARCHAR))),100)
      WHEN 0 THEN 'NEW_SIGNUP' WHEN 1 THEN 'NEW_SIGNUP'
      ELSE CASE WHEN MOD(ABS(FNV_HASH('MCH_'||CAST(m.rn AS VARCHAR))),100) < 60 THEN 'IN_WAREHOUSE'
                WHEN MOD(ABS(FNV_HASH('MCH_'||CAST(m.rn AS VARCHAR))),100) < 80 THEN 'AUTORENEW'
                WHEN MOD(ABS(FNV_HASH('MCH_'||CAST(m.rn AS VARCHAR))),100) < 92 THEN 'ECOMMERCE'
                ELSE 'MAIL_RENEWAL' END
    END,
    CASE WHEN MOD(ABS(FNV_HASH('MTR_'||CAST(m.rn AS VARCHAR))),100) < 60 THEN 'GOLD'
         WHEN MOD(ABS(FNV_HASH('MTR_'||CAST(m.rn AS VARCHAR))),100) < 95 THEN 'EXECUTIVE'
         ELSE 'BUSINESS' END,
    m.issued_date,
    DATEADD(year, 1, m.issued_date),
    CASE WHEN MOD(ABS(FNV_HASH('MTR_'||CAST(m.rn AS VARCHAR))),100) < 60 THEN 65
         WHEN MOD(ABS(FNV_HASH('MTR_'||CAST(m.rn AS VARCHAR))),100) < 95 THEN 130
         ELSE 120 END,
    'USD',
    'CARD',
    CASE
      WHEN MOD(ABS(FNV_HASH('MS_'||CAST(m.rn AS VARCHAR))),1000) < 980 THEN 'COLLECTED'
      WHEN MOD(ABS(FNV_HASH('MS_'||CAST(m.rn AS VARCHAR))),1000) < 995 THEN 'FAILED'
      WHEN MOD(ABS(FNV_HASH('MS_'||CAST(m.rn AS VARCHAR))),1000) < 998 THEN 'CHARGEBACK'
      ELSE 'REFUNDED' END,
    -- Scripted S7: 2025-Q2 fraud-loss spike on ECOMMERCE renewals
    CASE WHEN m.issued_date BETWEEN DATE '2025-04-01' AND DATE '2025-06-30'
              AND MOD(ABS(FNV_HASH('MCH_'||CAST(m.rn AS VARCHAR))),100) BETWEEN 80 AND 91  -- ECOMMERCE
              AND MOD(ABS(FNV_HASH('MFR_'||CAST(m.rn AS VARCHAR))),100) < 4
         THEN CASE WHEN MOD(ABS(FNV_HASH('MTR_'||CAST(m.rn AS VARCHAR))),100) < 60 THEN 65 ELSE 130 END
         ELSE 0 END
FROM t_member_raw m;

-- -----------------------------------------------------------------------------
-- 14.14 PAYMENT_EXCEPTION  (~2% of TRANSFER rows)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE payment_exception;

INSERT INTO payment_exception (
    uuid, file_uuid, transfer_uuid, exception_type, detected_at, resolved_at,
    resolution_time_minutes, resolved_by_user, resolution_action,
    repair_touch_count, repair_cost_amount, status
)
SELECT
    gen_uuid('PE_'||t.uuid),
    t.file_uuid,
    t.uuid,
    CASE MOD(ABS(FNV_HASH('PE_'||t.uuid)),100)
      WHEN 0 THEN 'OFAC_HOLD' WHEN 1 THEN 'OFAC_HOLD' WHEN 2 THEN 'OFAC_HOLD' WHEN 3 THEN 'OFAC_HOLD' WHEN 4 THEN 'OFAC_HOLD'
      ELSE CASE WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 35 THEN 'BANK_VALIDATION'
                WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 60 THEN 'FILE_FORMAT'
                WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 80 THEN 'BENEFICIARY_DATA'
                WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 95 THEN 'DUPLICATE'
                ELSE 'OTHER' END
    END,
    (t.value_date + INTERVAL '10 hour')::TIMESTAMPTZ,
    (t.value_date + (CASE
        WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 5  THEN INTERVAL '120 minute'
        WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 35 THEN INTERVAL '45 minute'
        WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 60 THEN INTERVAL '60 minute'
        WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 80 THEN INTERVAL '90 minute'
        ELSE INTERVAL '30 minute' END) + INTERVAL '10 hour')::TIMESTAMPTZ,
    CASE
      WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 5  THEN 120
      WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 35 THEN 45
      WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 60 THEN 60
      WHEN MOD(ABS(FNV_HASH('PE_'||t.uuid)),100) < 80 THEN 90
      ELSE 30 END,
    CASE MOD(ABS(FNV_HASH('PEU_'||t.uuid)),5)
      WHEN 0 THEN 'TREAS_BACK' WHEN 1 THEN 'AP_LEAD_GLOBAL'
      WHEN 2 THEN 'CASH_MANAGER_AMER' WHEN 3 THEN 'CASH_MANAGER_EMEA' ELSE 'CASH_MANAGER_APAC' END,
    'AUTO_REPAIR',
    1 + MOD(ABS(FNV_HASH('PER_'||t.uuid)),3),
    ROUND(25 * (1 + MOD(ABS(FNV_HASH('PER_'||t.uuid)),3)), 2),
    'RESOLVED'
FROM transfer t
WHERE MOD(ABS(FNV_HASH('PE_PICK_'||t.uuid)),100) < 2;

-- -----------------------------------------------------------------------------
-- 14.15 CROSS_BORDER_PAYMENT_LEG (~10% of TRANSFER rows)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE cross_border_payment_leg;

INSERT INTO cross_border_payment_leg (
    uuid, transfer_uuid, payment_transaction_uuid, origination_country, destination_country,
    send_currency, receive_currency, corridor, send_amount, receive_amount,
    fx_rate_applied, fx_spread_bps, lifting_fees, correspondent_fees,
    payment_method, initiated_at, delivered_at
)
SELECT
    gen_uuid('XB_'||t.uuid),
    t.uuid, NULL,
    -- Origin from payment_file account_ref → company → country
    COALESCE(SUBSTRING(ba.company_ref,4,2),'US'),
    CASE MOD(ABS(FNV_HASH('XBD_'||t.uuid)),10)
      WHEN 0 THEN 'CA' WHEN 1 THEN 'MX' WHEN 2 THEN 'GB' WHEN 3 THEN 'DE'
      WHEN 4 THEN 'FR' WHEN 5 THEN 'JP' WHEN 6 THEN 'HK' WHEN 7 THEN 'SG'
      WHEN 8 THEN 'AU' ELSE 'IN' END,
    t.currency_code,
    CASE MOD(ABS(FNV_HASH('XBD_'||t.uuid)),10)
      WHEN 0 THEN 'CAD' WHEN 1 THEN 'MXN' WHEN 2 THEN 'GBP' WHEN 3 THEN 'EUR'
      WHEN 4 THEN 'EUR' WHEN 5 THEN 'JPY' WHEN 6 THEN 'HKD' WHEN 7 THEN 'SGD'
      WHEN 8 THEN 'AUD' ELSE 'INR' END,
    CASE MOD(ABS(FNV_HASH('XBD_'||t.uuid)),10)
      WHEN 0 THEN 'US-CA' WHEN 1 THEN 'US-MX' WHEN 2 THEN 'US-EU'
      WHEN 3 THEN 'US-EU' WHEN 4 THEN 'US-EU' WHEN 5 THEN 'EU-APAC'
      WHEN 6 THEN 'EU-APAC' WHEN 7 THEN 'EU-APAC' ELSE 'US-APAC' END,
    t.amount,
    ROUND(t.amount * (0.95 + gen_rand('XR_'||t.uuid) * 0.10), 2),
    ROUND(0.95 + gen_rand('XR_'||t.uuid) * 0.10, 6),
    25 + CAST(gen_rand('XSP_'||t.uuid) * 55 AS INTEGER),
    ROUND(15 + gen_rand('XLF_'||t.uuid) * 35, 2),
    ROUND(20 + gen_rand('XCF_'||t.uuid) * 40, 2),
    t.payment_rail,
    t.value_date::TIMESTAMPTZ,
    DATEADD(hour, 24 + CAST(gen_rand('XDH_'||t.uuid) * 48 AS INTEGER), t.value_date::TIMESTAMP)::TIMESTAMPTZ
FROM transfer t
JOIN payment_file pf ON pf.file_uuid = t.file_uuid
JOIN bank_account ba ON ba.code = pf.account_ref
WHERE MOD(ABS(FNV_HASH('XB_PICK_'||t.uuid)),100) < 10;

-- -----------------------------------------------------------------------------
-- 14.16 FRAUD_LOSS — concentrated on CNP + NO_3DS membership renewals 2025-Q2
-- -----------------------------------------------------------------------------
TRUNCATE TABLE fraud_loss;

INSERT INTO fraud_loss (
    uuid, detection_event_uuid, authorization_ref, company_ref, channel,
    loss_date, loss_amount, currency_code, loss_category, fraud_vector,
    issuer_country, auth_method, recovered_amount
)
SELECT
    gen_uuid('FL_'||ca.uuid),
    NULL, ca.uuid, p.company_ref, p.channel,
    ca.auth_response_ts::DATE,
    ca.amount, ca.currency_code,
    CASE WHEN ca.cnp_indicator AND ca.auth_3ds_status='FAILED' THEN 'TRUE_FRAUD'
         ELSE 'FRIENDLY_FRAUD' END,
    CASE WHEN ca.cnp_indicator THEN 'CARD_NOT_PRESENT'
         WHEN p.channel='ECOMMERCE' THEN 'ACCOUNT_TAKEOVER'
         ELSE 'SOCIAL_ENGINEERING' END,
    'US',
    CASE WHEN ca.auth_3ds_status='FAILED' THEN 'NO_3DS'
         WHEN ca.auth_3ds_status='CHALLENGED' THEN '3DS_CHALLENGED'
         WHEN ca.auth_3ds_status='FRICTIONLESS' THEN '3DS_FRICTIONLESS'
         ELSE 'NONE' END,
    ROUND(ca.amount * 0.30, 2)
FROM card_authorization ca
JOIN pos_transaction p ON p.uuid = ca.pos_transaction_ref
WHERE ca.decision='APPROVED'
  AND (
       -- Baseline ~0.05% of approved auths
       MOD(ABS(FNV_HASH('FL_PICK_'||ca.uuid)),10000) < 5
    OR -- Scripted spike: 2025-Q2 ECOMMERCE membership renewals with NO_3DS
       ( p.channel='MEMBERSHIP_RENEWAL'
         AND p.transaction_ts::DATE BETWEEN DATE '2025-04-01' AND DATE '2025-06-30'
         AND ca.auth_3ds_status='FAILED'
         AND MOD(ABS(FNV_HASH('FL_SPIKE_'||ca.uuid)),100) < 30 )
      );

-- -----------------------------------------------------------------------------
-- 14.17 ACQUIRER_SLA_METRIC (one per acquirer per business day)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE acquirer_sla_metric;

INSERT INTO acquirer_sla_metric (
    uuid, acquirer_ref, measurement_date, auth_rate_pct, avg_response_time_ms,
    settlement_on_time_pct, uptime_pct, sla_breach_count, incident_count
)
SELECT
    gen_uuid('SLA_'||a.code||'_'||TO_CHAR(c.calendar_date,'YYYYMMDD')),
    a.code, c.calendar_date,
    CASE WHEN a.code='CHASE_PAYTECH' AND c.calendar_date BETWEEN DATE '2024-09-15' AND DATE '2024-09-19'
         THEN ROUND(92 + gen_rand('SLA_AR_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 1.5, 3)
         ELSE ROUND(96 + gen_rand('SLA_AR_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 3.0, 3) END,
    CASE WHEN a.code='CHASE_PAYTECH' AND c.calendar_date BETWEEN DATE '2024-09-15' AND DATE '2024-09-19'
         THEN 2200 + CAST(gen_rand('SLA_RT_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 800 AS INTEGER)
         ELSE 350 + CAST(gen_rand('SLA_RT_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 300 AS INTEGER) END,
    CASE WHEN a.code='CHASE_PAYTECH' AND c.calendar_date BETWEEN DATE '2025-01-15' AND DATE '2025-03-15'
         THEN ROUND(94 + gen_rand('SLA_ST_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 4, 3)
         ELSE ROUND(98 + gen_rand('SLA_ST_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 1.5, 3) END,
    CASE WHEN a.code='CHASE_PAYTECH' AND c.calendar_date BETWEEN DATE '2024-09-15' AND DATE '2024-09-19'
         THEN ROUND(95 + gen_rand('SLA_UP_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 2, 3)
         ELSE ROUND(99.8 + gen_rand('SLA_UP_'||a.code||CAST(c.calendar_date AS VARCHAR)) * 0.18, 3) END,
    CASE WHEN a.code='CHASE_PAYTECH' AND c.calendar_date BETWEEN DATE '2024-09-15' AND DATE '2024-09-19' THEN 3 ELSE 0 END,
    CASE WHEN a.code='CHASE_PAYTECH' AND c.calendar_date BETWEEN DATE '2024-09-15' AND DATE '2024-09-19' THEN 1 ELSE 0 END
FROM acquirer a
CROSS JOIN v_business_days c;

-- -----------------------------------------------------------------------------
-- 14.18 PAYMENT_HUB_THROUGHPUT (one per rail × business day)
-- -----------------------------------------------------------------------------
TRUNCATE TABLE payment_hub_throughput;

DROP TABLE IF EXISTS t_rail_daily;
CREATE TEMP TABLE t_rail_daily AS
SELECT t.payment_rail, t.value_date AS metric_date,
       COUNT(*) AS volume_count,
       SUM(t.amount) AS value_amount,
       SUM(CASE WHEN t.status='ACK' THEN 1 ELSE 0 END) AS success_count,
       SUM(CASE WHEN t.status='REJECTED' THEN 1 ELSE 0 END) AS rejection_count,
       SUM(CASE WHEN t.status='NACK' THEN 1 ELSE 0 END) AS repair_count
FROM transfer t
WHERE t.value_date IS NOT NULL
GROUP BY t.payment_rail, t.value_date;

INSERT INTO payment_hub_throughput (
    uuid, metric_date, payment_rail, country, originating_system,
    volume_count, value_amount, success_count, rejection_count, repair_count, stp_rate_pct
)
SELECT
    gen_uuid('PHUB_'||COALESCE(payment_rail,'NULL')||'_'||TO_CHAR(metric_date,'YYYYMMDD')),
    metric_date, COALESCE(payment_rail,'OTHER'),
    'US',
    'KYRIBA_PAYMENT_HUB',
    volume_count, ROUND(value_amount,2), success_count, rejection_count, repair_count,
    ROUND(100.0 * success_count / NULLIF(volume_count,0), 3)
FROM t_rail_daily
WHERE payment_rail IS NOT NULL;

-- VERIFY
SELECT 'acquirer' AS t, COUNT(*) FROM acquirer
UNION ALL SELECT 'acquirer_contract', COUNT(*) FROM acquirer_contract
UNION ALL SELECT 'card_network', COUNT(*) FROM card_network
UNION ALL SELECT 'card_bin_range', COUNT(*) FROM card_bin_range
UNION ALL SELECT 'pos_transaction', COUNT(*) FROM pos_transaction
UNION ALL SELECT 'card_authorization', COUNT(*) FROM card_authorization
UNION ALL SELECT 'card_settlement_batch', COUNT(*) FROM card_settlement_batch
UNION ALL SELECT 'card_settlement_line', COUNT(*) FROM card_settlement_line
UNION ALL SELECT 'chargeback', COUNT(*) FROM chargeback
UNION ALL SELECT 'ach_return', COUNT(*) FROM ach_return
UNION ALL SELECT 'card_rebate_program', COUNT(*) FROM card_rebate_program
UNION ALL SELECT 'card_rebate_earning', COUNT(*) FROM card_rebate_earning
UNION ALL SELECT 'membership_fee', COUNT(*) FROM membership_fee
UNION ALL SELECT 'payment_exception', COUNT(*) FROM payment_exception
UNION ALL SELECT 'cross_border_payment_leg', COUNT(*) FROM cross_border_payment_leg
UNION ALL SELECT 'fraud_loss', COUNT(*) FROM fraud_loss
UNION ALL SELECT 'acquirer_sla_metric', COUNT(*) FROM acquirer_sla_metric
UNION ALL SELECT 'payment_hub_throughput', COUNT(*) FROM payment_hub_throughput;
