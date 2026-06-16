-- =============================================================================
-- 05_invoices.sql (Redshift) — AR_INVOICE, AP_INVOICE, WCF_DOCUMENT
-- AP rows are 12% cross-currency; CNY shutdown reduces AP to APAC suppliers.
-- =============================================================================
SET search_path TO lpp;

-- =============================================================================
-- AR_INVOICE
-- =============================================================================
TRUNCATE TABLE ar_invoice;

-- B2C aggregated daily — drawn from POS receipts already in CASH_FLOW
INSERT INTO ar_invoice (
    uuid, invoice_number, company_ref, customer_ref,
    issue_date, due_date, paid_date,
    invoice_amount, paid_amount, open_amount,
    currency_code, status, payment_terms
)
SELECT
    gen_uuid('AR_B2C_'||ba.company_ref||'_'||CAST(c.calendar_date AS VARCHAR)),
    'AR-B2C-'||ba.company_ref||'-'||TO_CHAR(c.calendar_date,'YYYYMMDD'),
    ba.company_ref, 'RETAIL_CONSUMER',
    c.calendar_date, c.calendar_date, c.calendar_date,
    ROUND(SUM(cf.flow_amount),2),
    ROUND(SUM(cf.flow_amount),2), 0,
    ba.currency_ref, 'PAID', 'NET_0'
FROM cash_flow cf
JOIN bank_account ba ON ba.code = cf.account_ref
JOIN gen_calendar c ON c.calendar_date = cf.transaction_date
WHERE cf.flow_code_ref IN ('RCPT_POS','RCPT_ECOM')
GROUP BY ba.company_ref, ba.currency_ref, c.calendar_date;

-- B2B wholesale invoices
DROP TABLE IF EXISTS t_ar_b2b;
CREATE TEMP TABLE t_ar_b2b AS
WITH base AS (
    SELECT
        n+1 AS seq,
        DATEADD(day, MOD(ABS(FNV_HASH('AR_B2B|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')::DATE AS issue_dt,
        (CASE MOD(ABS(FNV_HASH('AR_B2B_CO|'||CAST(n AS VARCHAR))),10)
           WHEN 0 THEN 'GR_US_INC' WHEN 1 THEN 'GR_GB' WHEN 2 THEN 'GR_FR' WHEN 3 THEN 'GR_DE'
           WHEN 4 THEN 'GR_IT' WHEN 5 THEN 'GR_ES' WHEN 6 THEN 'GR_JP' WHEN 7 THEN 'GR_AU'
           WHEN 8 THEN 'GR_HK' ELSE 'GR_KR' END) AS co,
        'TP_CUST_'||LPAD(CAST((MOD(ABS(FNV_HASH('AR_B2B_CUST|'||CAST(n AS VARCHAR))),80)+1) AS VARCHAR),4,'0') AS cust,
        (CASE MOD(ABS(FNV_HASH('AR_B2B_TERMS|'||CAST(n AS VARCHAR))),3)
           WHEN 0 THEN 'NET_30' WHEN 1 THEN 'NET_60' ELSE 'NET_90' END) AS terms,
        gen_rand('AR_PAY_'||CAST(n AS VARCHAR)) AS pay_r
    FROM gen_numbers WHERE n < 240000
)
SELECT b.*,
       CASE b.terms WHEN 'NET_30' THEN 30 WHEN 'NET_60' THEN 60 ELSE 90 END AS term_days,
       ROUND((EXP(8.5 + 1.4 * gen_normal('AR_'||CAST(b.seq AS VARCHAR)))
              * yoy_growth(b.issue_dt)
              * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt,
       CASE c.country WHEN 'US' THEN 'USD' WHEN 'GB' THEN 'GBP' WHEN 'JP' THEN 'JPY'
                      WHEN 'HK' THEN 'HKD' WHEN 'AU' THEN 'AUD' WHEN 'KR' THEN 'KRW'
                      ELSE 'EUR' END AS ccy
FROM base b JOIN company c ON c.code = b.co;

INSERT INTO ar_invoice (
    uuid, invoice_number, company_ref, customer_ref,
    issue_date, due_date, paid_date,
    invoice_amount, paid_amount, open_amount,
    currency_code, status, payment_terms
)
SELECT
    gen_uuid('AR_B2B_'||CAST(seq AS VARCHAR)), 'AR-B2B-'||LPAD(CAST(seq AS VARCHAR),8,'0'),
    co, cust, issue_dt,
    DATEADD(day, term_days, issue_dt),
    CASE
      WHEN pay_r < 0.75 THEN DATEADD(day, term_days - MOD(ABS(FNV_HASH('AR_OT|'||CAST(seq AS VARCHAR))),6), issue_dt)
      WHEN pay_r < 0.93 THEN DATEADD(day, term_days + (MOD(ABS(FNV_HASH('AR_LT1|'||CAST(seq AS VARCHAR))),30)+1), issue_dt)
      WHEN pay_r < 0.98 THEN DATEADD(day, term_days + (MOD(ABS(FNV_HASH('AR_LT2|'||CAST(seq AS VARCHAR))),60)+31), issue_dt)
      ELSE NULL
    END,
    amt,
    CASE WHEN pay_r < 0.98 THEN amt ELSE 0 END,
    CASE WHEN pay_r < 0.98 THEN 0    ELSE amt END,
    ccy,
    CASE WHEN pay_r < 0.98 THEN 'PAID'
         WHEN pay_r < 0.99 THEN 'OPEN'
         ELSE 'WRITTEN_OFF' END,
    terms
FROM t_ar_b2b;

-- Iberica default scenario
UPDATE ar_invoice
SET status='WRITTEN_OFF', paid_amount=0, open_amount=invoice_amount, paid_date=NULL
WHERE customer_ref='TP_CUST_0042'
  AND issue_date BETWEEN DATE '2024-01-01' AND DATE '2024-03-31';

-- Royalty invoices
INSERT INTO ar_invoice (
    uuid, invoice_number, company_ref, customer_ref,
    issue_date, due_date, paid_date,
    invoice_amount, paid_amount, open_amount,
    currency_code, status, payment_terms
)
WITH royalty AS (
    SELECT n+1 AS seq,
           TO_DATE(
             CAST(2023 + MOD(ABS(FNV_HASH('ROY_Y|'||CAST(n AS VARCHAR))),3) AS VARCHAR) || '-' ||
             LPAD(CAST((CASE MOD(ABS(FNV_HASH('ROY_M|'||CAST(n AS VARCHAR))),4)
                          WHEN 0 THEN 3 WHEN 1 THEN 6 WHEN 2 THEN 9 ELSE 12 END) AS VARCHAR),2,'0') || '-28',
             'YYYY-MM-DD') AS issue_dt,
           (CASE MOD(ABS(FNV_HASH('ROY_CO|'||CAST(n AS VARCHAR))),2)
              WHEN 0 THEN 'GR_HOLDINGS' ELSE 'GR_TREASURY' END) AS co,
           'TP_CUST_'||LPAD(CAST((MOD(ABS(FNV_HASH('ROY_CUST|'||CAST(n AS VARCHAR))),80)+1) AS VARCHAR),4,'0') AS cust
    FROM gen_numbers WHERE n < 12000
)
SELECT gen_uuid('AR_ROY_'||CAST(seq AS VARCHAR)), 'AR-ROY-'||LPAD(CAST(seq AS VARCHAR),8,'0'),
       co, cust, issue_dt, DATEADD(day,60,issue_dt), DATEADD(day,55,issue_dt),
       ROUND((EXP(11 + 0.6 * gen_normal('ROY_'||CAST(seq AS VARCHAR)))
              * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
       ROUND((EXP(11 + 0.6 * gen_normal('ROY_'||CAST(seq AS VARCHAR)))
              * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
       0,'USD','PAID','NET_60'
FROM royalty;

-- AR cash flow legs
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_AR_'||ai.uuid),
    COALESCE(
      (SELECT MIN(b.code) FROM bank_account b
        WHERE b.company_ref = ai.company_ref
          AND b.currency_ref = ai.currency_code
          AND b.account_purpose='COLLECTION'
          AND NOT b.closed_account),
      (SELECT MIN(b.code) FROM bank_account b
        WHERE b.company_ref = ai.company_ref AND b.currency_ref = ai.currency_code
          AND NOT b.closed_account)
    ),
    CASE WHEN ai.currency_code='USD' AND MOD(ABS(FNV_HASH('RAIL|'||ai.uuid)),5)<3 THEN 'RCPT_AR_WIRE'
         WHEN ai.currency_code='USD' THEN 'RCPT_AR_ACH'
         WHEN ai.currency_code IN ('EUR','GBP','SEK','PLN','CHF') THEN 'RCPT_AR_SEPA'
         ELSE 'RCPT_AR_WIRE' END,
    'BC_OPS','CONFIRMED',
    ai.paid_date, ai.paid_date,
    ai.paid_amount, ai.currency_code, ai.paid_amount,
    ai.paid_amount, ai.currency_code, 1.0,
    ai.customer_ref, ai.customer_ref, 'AR receipt for '||ai.invoice_number,
    ai.invoice_number,
    CASE WHEN ai.currency_code='USD' AND MOD(ABS(FNV_HASH('RAIL|'||ai.uuid)),5)<3 THEN 'WIRE'
         WHEN ai.currency_code='USD' THEN 'ACH'
         WHEN ai.currency_code IN ('EUR','GBP','SEK','PLN','CHF') THEN 'SEPA_CT'
         ELSE 'WIRE' END
FROM ar_invoice ai
WHERE ai.status='PAID' AND ai.customer_ref<>'RETAIL_CONSUMER'
  AND ai.paid_date IS NOT NULL;

-- =============================================================================
-- AP_INVOICE
-- =============================================================================
TRUNCATE TABLE ap_invoice;

DROP TABLE IF EXISTS t_ap;
CREATE TEMP TABLE t_ap AS
WITH base AS (
    SELECT
        n+1 AS seq,
        DATEADD(day, MOD(ABS(FNV_HASH('AP_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')::DATE AS issue_dt,
        (CASE MOD(ABS(FNV_HASH('AP_CO|'||CAST(n AS VARCHAR))),18)
           WHEN 0 THEN 'GR_US_INC' WHEN 1 THEN 'GR_GB' WHEN 2 THEN 'GR_FR' WHEN 3 THEN 'GR_DE'
           WHEN 4 THEN 'GR_IT' WHEN 5 THEN 'GR_ES' WHEN 6 THEN 'GR_JP' WHEN 7 THEN 'GR_AU'
           WHEN 8 THEN 'GR_HK' WHEN 9 THEN 'GR_KR' WHEN 10 THEN 'GR_MX' WHEN 11 THEN 'GR_BR'
           WHEN 12 THEN 'GR_CA' WHEN 13 THEN 'GR_AE' WHEN 14 THEN 'GR_PL' WHEN 15 THEN 'GR_SE'
           WHEN 16 THEN 'GR_TREASURY' ELSE 'GR_HOLDINGS' END) AS co,
        (CASE MOD(ABS(FNV_HASH('AP_VPFX|'||CAST(n AS VARCHAR))),7)
           WHEN 0 THEN 'TP_APPAREL_' WHEN 1 THEN 'TP_LOGI_' WHEN 2 THEN 'TP_LAND_'
           WHEN 3 THEN 'TP_UTIL_' WHEN 4 THEN 'TP_MKTG_' WHEN 5 THEN 'TP_IT_'
           ELSE 'TP_CAPEX_' END) AS vendor_pfx,
        MOD(ABS(FNV_HASH('AP_VSEQ|'||CAST(n AS VARCHAR))),79)+1 AS vendor_seq,
        (CASE MOD(ABS(FNV_HASH('AP_TERMS|'||CAST(n AS VARCHAR))),5)
           WHEN 0 THEN 'NET_30' WHEN 1 THEN 'NET_45' WHEN 2 THEN 'NET_60'
           WHEN 3 THEN 'NET_90' ELSE 'EOM_5' END) AS terms,
        gen_rand('AP_PAY_'||CAST(n AS VARCHAR)) AS pay_r,
        gen_rand('AP_FX_'||CAST(n AS VARCHAR)) AS fx_r
    FROM gen_numbers WHERE n < 180000
), enriched AS (
    SELECT b.*,
        CASE b.terms WHEN 'NET_30' THEN 30 WHEN 'NET_45' THEN 45
                     WHEN 'NET_60' THEN 60 WHEN 'NET_90' THEN 90 ELSE 5 END AS term_days,
        ROUND((EXP(8.0 + 1.3 * gen_normal('AP_'||CAST(b.seq AS VARCHAR)))
               * ap_inflation(b.issue_dt)
               * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2) AS amt,
        CASE c.country WHEN 'US' THEN 'USD' WHEN 'GB' THEN 'GBP' WHEN 'JP' THEN 'JPY'
                       WHEN 'HK' THEN 'HKD' WHEN 'AU' THEN 'AUD' WHEN 'KR' THEN 'KRW'
                       WHEN 'MX' THEN 'MXN' WHEN 'BR' THEN 'BRL' WHEN 'CA' THEN 'CAD'
                       WHEN 'AE' THEN 'AED' WHEN 'PL' THEN 'PLN' WHEN 'SE' THEN 'SEK'
                       ELSE 'EUR' END AS func_ccy
    FROM base b JOIN company c ON c.code = b.co
), with_vendor AS (
    SELECT e.*,
        e.vendor_pfx ||
        LPAD(CAST((MOD((e.vendor_seq - 1),
               CASE e.vendor_pfx
                 WHEN 'TP_APPAREL_' THEN 80 WHEN 'TP_LOGI_' THEN 60
                 WHEN 'TP_LAND_'    THEN 40 WHEN 'TP_UTIL_' THEN 30
                 WHEN 'TP_MKTG_'    THEN 20 WHEN 'TP_IT_'   THEN 15
                 ELSE 5 END) + 1) AS VARCHAR), 4, '0') AS vendor_code,
        CASE WHEN e.fx_r < 0.20 THEN
            CASE e.vendor_pfx
              WHEN 'TP_APPAREL_' THEN 'CNY'
              WHEN 'TP_LOGI_'    THEN 'USD'
              WHEN 'TP_CAPEX_'   THEN 'USD'
              WHEN 'TP_IT_'      THEN 'USD'   -- SaaS / cloud typically USD-billed
              WHEN 'TP_MKTG_'    THEN 'USD'   -- ad networks / agencies typically USD
              ELSE e.func_ccy END
            ELSE e.func_ccy END AS invoice_ccy
    FROM enriched e
)
SELECT * FROM with_vendor;

INSERT INTO ap_invoice (
    uuid, invoice_number, company_ref, vendor_ref,
    issue_date, due_date, paid_date,
    invoice_amount, paid_amount, open_amount,
    currency_code, status, payment_terms
)
SELECT
    gen_uuid('AP_'||CAST(seq AS VARCHAR)), 'AP-'||LPAD(CAST(seq AS VARCHAR),8,'0'),
    co, vendor_code, issue_dt,
    DATEADD(day,term_days,issue_dt),
    CASE
      WHEN pay_r < 0.88 THEN DATEADD(day, term_days - MOD(ABS(FNV_HASH('AP_OT|'||CAST(seq AS VARCHAR))),4), issue_dt)
      WHEN pay_r < 0.98 THEN DATEADD(day, term_days + (MOD(ABS(FNV_HASH('AP_LT|'||CAST(seq AS VARCHAR))),15)+1), issue_dt)
      ELSE NULL
    END,
    amt,
    CASE WHEN pay_r < 0.98 THEN amt
         WHEN pay_r < 0.985 THEN ROUND(amt * 0.5,2)
         ELSE 0 END,
    CASE WHEN pay_r < 0.98 THEN 0
         WHEN pay_r < 0.985 THEN ROUND(amt * 0.5,2)
         ELSE amt END,
    invoice_ccy,
    CASE WHEN pay_r < 0.98  THEN 'PAID'
         WHEN pay_r < 0.985 THEN 'PARTIAL'
         WHEN pay_r < 0.99  THEN 'DISPUTED'
         ELSE 'OPEN' END,
    terms
FROM t_ap;

-- AP cash flow legs
INSERT INTO cash_flow (
    uuid, account_ref, flow_code_ref, budget_code_ref, status,
    transaction_date, value_date,
    flow_amount, flow_currency, signed_amount,
    account_amount, account_currency, fx_rate,
    counterparty_name, counterparty_ref, description, reference, payment_rail
)
SELECT
    gen_uuid('CF_AP_'||ap.uuid),
    COALESCE(
      (SELECT MIN(b.code) FROM bank_account b
        WHERE b.company_ref = ap.company_ref AND b.account_purpose='OPERATING'
          AND NOT b.closed_account),
      (SELECT MIN(b.code) FROM bank_account b
        WHERE b.company_ref = ap.company_ref AND NOT b.closed_account)
    ),
    CASE
      WHEN ap.currency_code='USD' AND ap.paid_date >= DATE '2024-01-01'
           AND MOD(ABS(FNV_HASH('RTP|'||ap.uuid)),20)=0 THEN 'DISB_AP_RTP'
      WHEN ap.currency_code='USD' AND MOD(ABS(FNV_HASH('AP_RAIL|'||ap.uuid)),5)<2 THEN 'DISB_AP_WIRE'
      WHEN ap.currency_code='USD' THEN 'DISB_AP_ACH'
      WHEN ap.currency_code IN ('EUR','GBP','SEK','PLN','CHF') THEN 'DISB_AP_SEPA'
      ELSE 'DISB_AP_WIRE'
    END,
    'BC_OPS','CONFIRMED',
    ap.paid_date, add_business_days(ap.paid_date,1),
    -ap.paid_amount, ap.currency_code, -ap.paid_amount,
    -ROUND(ap.paid_amount * COALESCE(fx.rate, 1.0), 2),
    (SELECT functional_ccy FROM gen_company_region WHERE company_code = ap.company_ref),
    COALESCE(fx.rate, 1.0),
    'Vendor', ap.vendor_ref, 'AP payment for '||ap.invoice_number,
    ap.invoice_number,
    CASE
      WHEN ap.currency_code='USD' AND ap.paid_date >= DATE '2024-01-01'
           AND MOD(ABS(FNV_HASH('RTP|'||ap.uuid)),20)=0 THEN 'RTP'
      WHEN ap.currency_code='USD' AND MOD(ABS(FNV_HASH('AP_RAIL|'||ap.uuid)),5)<2 THEN 'WIRE'
      WHEN ap.currency_code='USD' THEN 'ACH'
      WHEN ap.currency_code IN ('EUR','GBP','SEK','PLN','CHF') THEN 'SEPA_CT'
      ELSE 'WIRE'
    END
FROM ap_invoice ap
LEFT JOIN gen_company_region cr ON cr.company_code = ap.company_ref
LEFT JOIN fx_rate fx ON fx.rate_date = ap.paid_date
                     AND fx.base_currency = ap.currency_code
                     AND fx.quote_currency = cr.functional_ccy
                     AND fx.rate_type='SPOT'
WHERE ap.status IN ('PAID','PARTIAL')
  AND ap.paid_date IS NOT NULL
  AND ap.paid_amount > 0
  AND NOT (ap.vendor_ref LIKE 'TP_APPAREL_%'
           AND ap_cny_factor(ap.paid_date) < 1.0
           AND MOD(ABS(FNV_HASH('CNY_SKIP|'||ap.uuid)),5) > 0);

-- =============================================================================
-- WCF_DOCUMENT
-- =============================================================================
TRUNCATE TABLE wcf_document;
INSERT INTO wcf_document (uuid, document_number, supplier_ref, buyer_company, status, issue_date, due_date, amount, currency_code)
SELECT
    gen_uuid('WCF_'||CAST((n+1) AS VARCHAR)),
    'WCF-'||LPAD(CAST(n+1 AS VARCHAR),7,'0'),
    'TP_APPAREL_'||LPAD(CAST((MOD(ABS(FNV_HASH('WCF_S|'||CAST(n AS VARCHAR))),80)+1) AS VARCHAR),4,'0'),
    (CASE MOD(ABS(FNV_HASH('WCF_C|'||CAST(n AS VARCHAR))),4)
       WHEN 0 THEN 'GR_US_INC' WHEN 1 THEN 'GR_EU_BV' WHEN 2 THEN 'GR_APAC_PTE' ELSE 'GR_HOLDINGS' END),
    (CASE MOD(ABS(FNV_HASH('WCF_ST|'||CAST(n AS VARCHAR))),6)
       WHEN 0 THEN 'SUBMITTED' WHEN 1 THEN 'APPROVED' WHEN 2 THEN 'PAID'
       WHEN 3 THEN 'PAID' WHEN 4 THEN 'PAID' ELSE 'CANCELLED' END),
    DATEADD(day, MOD(ABS(FNV_HASH('WCF_DT|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01'),
    DATEADD(day, MOD(ABS(FNV_HASH('WCF_DUE|'||CAST(n AS VARCHAR))),31)+60,
            DATEADD(day, MOD(ABS(FNV_HASH('WCF_DT2|'||CAST(n AS VARCHAR))),1101), DATE '2023-05-01')),
    ROUND((EXP(9 + 1.0 * gen_normal('WCF_'||CAST(n AS VARCHAR)))
           * (SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR'))::NUMERIC, 2),
    'USD'
FROM gen_numbers WHERE n < 2500;

-- VERIFY
SELECT 'AR_INVOICE' AS t, COUNT(*) FROM ar_invoice
UNION ALL SELECT 'AR_PAID', COUNT(*) FROM ar_invoice WHERE status='PAID'
UNION ALL SELECT 'AR_WRITTEN_OFF', COUNT(*) FROM ar_invoice WHERE status='WRITTEN_OFF'
UNION ALL SELECT 'AP_INVOICE', COUNT(*) FROM ap_invoice
UNION ALL SELECT 'AP_PAID', COUNT(*) FROM ap_invoice WHERE status='PAID'
UNION ALL SELECT 'AP_CROSS_CCY', COUNT(*) FROM ap_invoice ap JOIN gen_company_region cr ON cr.company_code=ap.company_ref
                  WHERE ap.currency_code <> cr.functional_ccy
UNION ALL SELECT 'WCF_DOCUMENT', COUNT(*) FROM wcf_document
UNION ALL SELECT 'CASH_FLOW after 05', COUNT(*) FROM cash_flow;
