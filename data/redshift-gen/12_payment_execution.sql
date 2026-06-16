-- =============================================================================
-- 12_payment_execution.sql (Redshift) — PAYMENT_FILE, TRANSFER, PAYMENT_TRANSACTION
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 12.1 PAYMENT_FILE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE payment_file;

INSERT INTO payment_file (
    file_uuid, file_name, company_ref, account_ref,
    status, routing_status, total_count, total_amount, total_currency,
    created_at, updated_at
)
SELECT
    gen_uuid('PF_'||cf.account_ref||'_'||CAST(cf.value_date AS VARCHAR)),
    'pain001-'||cf.account_ref||'-'||TO_CHAR(cf.value_date,'YYYYMMDD')||'.xml',
    ba.company_ref, cf.account_ref,
    CASE WHEN MOD(ABS(FNV_HASH(cf.account_ref||'|'||CAST(cf.value_date AS VARCHAR))),100) < 95 THEN 'SENT'
         WHEN MOD(ABS(FNV_HASH(cf.account_ref||'|'||CAST(cf.value_date AS VARCHAR))),100) < 98 THEN 'RECEIVED'
         ELSE 'NACK' END,
    'COMPLETE',
    COUNT(*),
    ROUND(SUM(ABS(cf.flow_amount)),2),
    cf.account_currency,
    cf.value_date::TIMESTAMPTZ,
    DATEADD(hour,2,cf.value_date::TIMESTAMP)::TIMESTAMPTZ
FROM cash_flow cf
JOIN bank_account ba ON ba.code = cf.account_ref
WHERE cf.flow_code_ref IN ('DISB_AP_WIRE','DISB_AP_ACH','DISB_AP_SEPA','DISB_AP_RTP','DISB_AP_CHECK',
                            'DISB_PAYROLL','DISB_RENT','DISB_TAX','DISB_CAPEX')
GROUP BY cf.account_ref, ba.company_ref, cf.value_date, cf.account_currency;

-- -----------------------------------------------------------------------------
-- 12.2 TRANSFER
-- -----------------------------------------------------------------------------
TRUNCATE TABLE transfer;

INSERT INTO transfer (
    uuid, transaction_number, reference,
    file_uuid, file_name, status, next_action,
    ack_status, ack_code, ack_message, last_ack_time,
    remittance_identifier1, remittance,
    payment_rail, amount, currency_code, value_date
)
SELECT
    gen_uuid('TR_'||cf.uuid),
    'TX-'||LPAD(CAST(ROW_NUMBER() OVER (ORDER BY cf.uuid) AS VARCHAR),10,'0'),
    cf.reference,
    gen_uuid('PF_'||cf.account_ref||'_'||CAST(cf.value_date AS VARCHAR)),
    'pain001-'||cf.account_ref||'-'||TO_CHAR(cf.value_date,'YYYYMMDD')||'.xml',
    CASE WHEN gen_rand('TR_S_'||cf.uuid) < 0.005 THEN 'REJECTED'
         WHEN gen_rand('TR_S_'||cf.uuid) < 0.020 THEN 'NACK'
         ELSE 'ACK' END,
    NULL,
    CASE WHEN gen_rand('TR_S_'||cf.uuid) < 0.005 THEN 'REJECT'
         WHEN gen_rand('TR_S_'||cf.uuid) < 0.020 THEN 'NACK'
         ELSE 'ACSP' END,
    CASE WHEN gen_rand('TR_S_'||cf.uuid) < 0.005 THEN 'AC03'
         WHEN gen_rand('TR_S_'||cf.uuid) < 0.020 THEN 'AM05'
         ELSE NULL END,
    CASE WHEN gen_rand('TR_S_'||cf.uuid) < 0.005 THEN 'Invalid creditor account number'
         WHEN gen_rand('TR_S_'||cf.uuid) < 0.020 THEN 'Duplicate payment'
         ELSE 'Accepted by next agent' END,
    DATEADD(hour,4,cf.value_date::TIMESTAMP)::TIMESTAMPTZ,
    cf.reference,
    -- LINT FIX (#3): replaced string-built JSON_PARSE (incomplete escape — missing backslash escape, NULL-unsafe) with native OBJECT() SUPER constructor.
    OBJECT('description', cf.description),
    cf.payment_rail, ABS(cf.flow_amount), cf.flow_currency, cf.value_date
FROM cash_flow cf
WHERE cf.flow_code_ref IN ('DISB_AP_WIRE','DISB_AP_ACH','DISB_AP_SEPA','DISB_AP_RTP','DISB_AP_CHECK',
                            'DISB_PAYROLL','DISB_RENT','DISB_TAX','DISB_CAPEX');

-- -----------------------------------------------------------------------------
-- 12.3 PAYMENT_TRANSACTION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE payment_transaction;

INSERT INTO payment_transaction (
    uuid, file_uuid, end_to_end_id,
    transaction_date, execution_date, status, reason_code,
    amount, currency_code, payment_rail,
    issuer_name, issuer_account,
    counterparty_name, counterparty_account,
    reference, last_ack_time
)
SELECT
    gen_uuid('PT_'||cf.uuid),
    gen_uuid('PF_'||cf.account_ref||'_'||CAST(cf.value_date AS VARCHAR)),
    'E2E-'||LPAD(CAST(ROW_NUMBER() OVER (ORDER BY cf.uuid) AS VARCHAR),10,'0'),
    cf.transaction_date, cf.value_date,
    CASE WHEN gen_rand('PT_S_'||cf.uuid) < 0.005 THEN 'REJECTED' ELSE 'EXECUTED' END,
    CASE WHEN gen_rand('PT_S_'||cf.uuid) < 0.005 THEN 'AC03' ELSE NULL END,
    ABS(cf.flow_amount), cf.flow_currency, cf.payment_rail,
    ba.company_ref, ba.code,
    cf.counterparty_name, cf.counterparty_ref,
    cf.reference,
    DATEADD(hour,4,cf.value_date::TIMESTAMP)::TIMESTAMPTZ
FROM cash_flow cf
JOIN bank_account ba ON ba.code = cf.account_ref
WHERE cf.flow_code_ref IN ('DISB_AP_WIRE','DISB_AP_ACH','DISB_AP_SEPA','DISB_AP_RTP','DISB_AP_CHECK',
                            'DISB_PAYROLL','DISB_RENT','DISB_TAX','DISB_CAPEX');

-- -----------------------------------------------------------------------------
-- 12.4 Link BEC fraud event
-- -----------------------------------------------------------------------------
UPDATE fraud_detection_event
SET transfer_uuid = (SELECT uuid FROM transfer WHERE reference='BEC-2024-09-14' LIMIT 1),
    file_uuid     = (SELECT file_uuid FROM transfer WHERE reference='BEC-2024-09-14' LIMIT 1)
WHERE uuid = gen_uuid('FRAUD_BEC_2024_09_14');

-- VERIFY
SELECT 'PAYMENT_FILE' AS t, COUNT(*) FROM payment_file
UNION ALL SELECT 'PF_NACK', COUNT(*) FROM payment_file WHERE status='NACK'
UNION ALL SELECT 'TRANSFER', COUNT(*) FROM transfer
UNION ALL SELECT 'TR_NACK', COUNT(*) FROM transfer WHERE status='NACK'
UNION ALL SELECT 'TR_REJECTED', COUNT(*) FROM transfer WHERE status='REJECTED'
UNION ALL SELECT 'PAYMENT_TRANSACTION', COUNT(*) FROM payment_transaction
UNION ALL SELECT 'PT_REJECTED', COUNT(*) FROM payment_transaction WHERE status='REJECTED';
