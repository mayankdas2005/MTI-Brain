-- =============================================================================
-- 13_access_audit.sql (Redshift) — APP_USER, USER_GROUP, DATA_PERMISSION_PROFILE,
-- DATA_PERMISSION, USER_PROFILE_ASSIGNMENT, AUDIT_TRAIL, MAPPING_TABLE, MAPPING_ENTRY,
-- DOCUMENT_ATTACHMENT, WEBHOOK_EVENT
-- =============================================================================
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 13.1 APP_USER
-- -----------------------------------------------------------------------------
TRUNCATE TABLE app_user;
INSERT INTO app_user (uuid, code, first_name, last_name, email, active)
SELECT gen_uuid('U_'||code), code, first_name, last_name, email, TRUE FROM (
    SELECT 'CFO_GLOBAL' AS code,'Sarah' AS first_name,'Chen' AS last_name,'sarah.chen@globalretail.com' AS email UNION ALL
    SELECT 'TREAS_HEAD','Michael','Andersson','michael.andersson@globalretail.com' UNION ALL
    SELECT 'TREAS_FRONT','Diana','Patel','diana.patel@globalretail.com' UNION ALL
    SELECT 'TREAS_MIDDLE','Liu','Wei','liu.wei@globalretail.com' UNION ALL
    SELECT 'TREAS_BACK','Carlos','Mendoza','carlos.mendoza@globalretail.com' UNION ALL
    SELECT 'FX_TRADER','James','O''Brien','james.obrien@globalretail.com' UNION ALL
    SELECT 'CASH_MANAGER_AMER','Emma','Williams','emma.williams@globalretail.com' UNION ALL
    SELECT 'CASH_MANAGER_EMEA','Niels','Hoffmann','niels.hoffmann@globalretail.com' UNION ALL
    SELECT 'CASH_MANAGER_APAC','Yuki','Tanaka','yuki.tanaka@globalretail.com' UNION ALL
    SELECT 'AP_LEAD_GLOBAL','Rajesh','Sharma','rajesh.sharma@globalretail.com' UNION ALL
    SELECT 'AR_LEAD_GLOBAL','Sofia','Lopez','sofia.lopez@globalretail.com' UNION ALL
    SELECT 'CONTROLLER_GLOBAL','David','Cohen','david.cohen@globalretail.com' UNION ALL
    SELECT 'IT_TREAS_ADMIN','Anna','Kowalski','anna.kowalski@globalretail.com' UNION ALL
    SELECT 'AUDIT_LEAD','Roberto','Silva','roberto.silva@globalretail.com' UNION ALL
    SELECT 'SECURITY_LEAD','Fatima','Al-Hassan','fatima.alhassan@globalretail.com'
) t;

-- -----------------------------------------------------------------------------
-- 13.2 USER_GROUP / membership
-- -----------------------------------------------------------------------------
TRUNCATE TABLE user_group;
INSERT INTO user_group (uuid, code, description) VALUES
    (gen_uuid('UG_TREASURY'),'UG_TREASURY','Global treasury operations'),
    (gen_uuid('UG_AP'),'UG_AP','Accounts Payable'),
    (gen_uuid('UG_AR'),'UG_AR','Accounts Receivable'),
    (gen_uuid('UG_CONTROL'),'UG_CONTROL','Financial controllership'),
    (gen_uuid('UG_AUDIT'),'UG_AUDIT','Internal audit'),
    (gen_uuid('UG_ADMIN'),'UG_ADMIN','Platform administration');

TRUNCATE TABLE user_group_member;
INSERT INTO user_group_member (user_group_code, user_code) VALUES
    ('UG_TREASURY','TREAS_HEAD'),('UG_TREASURY','TREAS_FRONT'),('UG_TREASURY','TREAS_MIDDLE'),
    ('UG_TREASURY','TREAS_BACK'),('UG_TREASURY','FX_TRADER'),
    ('UG_TREASURY','CASH_MANAGER_AMER'),('UG_TREASURY','CASH_MANAGER_EMEA'),('UG_TREASURY','CASH_MANAGER_APAC'),
    ('UG_AP','AP_LEAD_GLOBAL'),('UG_AR','AR_LEAD_GLOBAL'),
    ('UG_CONTROL','CONTROLLER_GLOBAL'),('UG_CONTROL','CFO_GLOBAL'),
    ('UG_AUDIT','AUDIT_LEAD'),('UG_AUDIT','SECURITY_LEAD'),
    ('UG_ADMIN','IT_TREAS_ADMIN');

-- -----------------------------------------------------------------------------
-- 13.3 DATA_PERMISSION_PROFILE
-- -----------------------------------------------------------------------------
TRUNCATE TABLE data_permission_profile;
INSERT INTO data_permission_profile (uuid, code, description) VALUES
    (gen_uuid('PROF_GLOBAL_FULL'),'PROF_GLOBAL_FULL','Full read/write across all entities'),
    (gen_uuid('PROF_AMER_RW'),'PROF_AMER_RW','Americas read/write'),
    (gen_uuid('PROF_EMEA_RW'),'PROF_EMEA_RW','EMEA read/write'),
    (gen_uuid('PROF_APAC_RW'),'PROF_APAC_RW','APAC read/write'),
    (gen_uuid('PROF_GLOBAL_READ'),'PROF_GLOBAL_READ','Read-only across all entities (audit/control)'),
    (gen_uuid('PROF_AP_OPS'),'PROF_AP_OPS','AP operations across opcos'),
    (gen_uuid('PROF_AR_OPS'),'PROF_AR_OPS','AR operations across opcos');

TRUNCATE TABLE user_profile_assignment;
INSERT INTO user_profile_assignment (user_code, profile_code) VALUES
    ('CFO_GLOBAL','PROF_GLOBAL_FULL'),('TREAS_HEAD','PROF_GLOBAL_FULL'),
    ('TREAS_FRONT','PROF_GLOBAL_FULL'),('TREAS_MIDDLE','PROF_GLOBAL_READ'),
    ('TREAS_BACK','PROF_GLOBAL_FULL'),('FX_TRADER','PROF_GLOBAL_FULL'),
    ('CASH_MANAGER_AMER','PROF_AMER_RW'),('CASH_MANAGER_EMEA','PROF_EMEA_RW'),
    ('CASH_MANAGER_APAC','PROF_APAC_RW'),
    ('AP_LEAD_GLOBAL','PROF_AP_OPS'),('AR_LEAD_GLOBAL','PROF_AR_OPS'),
    ('CONTROLLER_GLOBAL','PROF_GLOBAL_READ'),('AUDIT_LEAD','PROF_GLOBAL_READ'),
    ('SECURITY_LEAD','PROF_GLOBAL_READ'),('IT_TREAS_ADMIN','PROF_GLOBAL_FULL');

-- -----------------------------------------------------------------------------
-- 13.4 DATA_PERMISSION
-- -----------------------------------------------------------------------------
TRUNCATE TABLE data_permission;

INSERT INTO data_permission (uuid, profile_code, entity_type, entity_code)
SELECT gen_uuid('DP_'||p.code||'_CO_'||c.code), p.code, 'COMPANY', c.code
FROM data_permission_profile p
CROSS JOIN company c
WHERE p.code IN ('PROF_GLOBAL_FULL','PROF_GLOBAL_READ','PROF_AP_OPS','PROF_AR_OPS');

INSERT INTO data_permission (uuid, profile_code, entity_type, entity_code)
SELECT gen_uuid('DP_'||p.code||'_BA_'||ba.code), p.code, 'BANK_ACCOUNT', ba.code
FROM data_permission_profile p
CROSS JOIN bank_account ba
WHERE p.code IN ('PROF_GLOBAL_FULL','PROF_GLOBAL_READ');

INSERT INTO data_permission (uuid, profile_code, entity_type, entity_code)
SELECT gen_uuid('DP_'||pr.profile_code||'_CO_'||cr.company_code),
       pr.profile_code, 'COMPANY', cr.company_code
FROM ( SELECT 'PROF_AMER_RW' AS profile_code,'AMER' AS region UNION ALL
       SELECT 'PROF_EMEA_RW','EMEA' UNION ALL
       SELECT 'PROF_EMEA_RW','MENA' UNION ALL
       SELECT 'PROF_APAC_RW','APAC' UNION ALL
       SELECT 'PROF_AMER_RW','LATAM' ) pr
JOIN gen_company_region cr ON cr.region = pr.region;

-- -----------------------------------------------------------------------------
-- 13.5 MAPPING_TABLE / MAPPING_ENTRY
-- -----------------------------------------------------------------------------
TRUNCATE TABLE mapping_table;
INSERT INTO mapping_table (uuid, code, description, scope) VALUES
    (gen_uuid('MT_RAIL_AFP'),'RAIL_TO_AFP','Payment rail → AFP service code','GLOBAL'),
    (gen_uuid('MT_FLOW_GL'),'FLOW_TO_GL','Cash flow code → GL account class','GLOBAL'),
    (gen_uuid('MT_BANK_BIC'),'BANK_TO_BIC','Internal bank code → BIC','GLOBAL'),
    (gen_uuid('MT_CCY_FUNCTIONAL'),'CO_TO_FUNCTIONAL_CCY','Company → functional currency','GLOBAL');

TRUNCATE TABLE mapping_entry;
INSERT INTO mapping_entry (mapping_table_code, source_value, target_value) VALUES
    ('RAIL_TO_AFP','WIRE','251'),('RAIL_TO_AFP','ACH','260'),('RAIL_TO_AFP','SEPA_CT','270'),
    ('RAIL_TO_AFP','SEPA_DD','271'),('RAIL_TO_AFP','RTP','300'),('RAIL_TO_AFP','FEDNOW','300'),
    ('RAIL_TO_AFP','CHECK','800'),('RAIL_TO_AFP','BOOK','100'),
    ('FLOW_TO_GL','RCPT_POS','4000'),('FLOW_TO_GL','RCPT_ECOM','4000'),('FLOW_TO_GL','RCPT_AR_WIRE','1200'),
    ('FLOW_TO_GL','DISB_AP_WIRE','2100'),('FLOW_TO_GL','DISB_PAYROLL','6000'),('FLOW_TO_GL','DISB_TAX','6000'),
    ('FLOW_TO_GL','DISB_CAPEX','1500'),('FLOW_TO_GL','LOAN_INTEREST','7000'),('FLOW_TO_GL','INV_INTEREST','7100'),
    ('FLOW_TO_GL','BANK_FEE','6000');

INSERT INTO mapping_entry (mapping_table_code, source_value, target_value)
SELECT 'BANK_TO_BIC', code, COALESCE(bic,'') FROM bank WHERE bic IS NOT NULL;

INSERT INTO mapping_entry (mapping_table_code, source_value, target_value)
SELECT 'CO_TO_FUNCTIONAL_CCY', company_code, functional_ccy FROM gen_company_region;

-- -----------------------------------------------------------------------------
-- 13.6 AUDIT_TRAIL
-- -----------------------------------------------------------------------------
TRUNCATE TABLE audit_trail;

INSERT INTO audit_trail (uuid, entity_type, entity_code, action, actor_user, occurred_at, before_image, after_image)
SELECT gen_uuid('AT_'||code), 'BANK_ACCOUNT', code, 'CREATE',
       'IT_TREAS_ADMIN', opening_date::TIMESTAMPTZ, NULL,
       -- LINT FIX (#3): replaced string-built JSON_PARSE with native OBJECT() constructor.
       OBJECT('code', code, 'company', company_ref, 'currency', currency_ref)
FROM bank_account;

INSERT INTO audit_trail (uuid, entity_type, entity_code, action, actor_user, occurred_at, before_image, after_image)
SELECT gen_uuid('AT_CLOSE_'||code), 'BANK_ACCOUNT', code, 'UPDATE',
       'TREAS_HEAD', closing_date::TIMESTAMPTZ,
       -- LINT FIX (#3): replaced literal-string JSON_PARSE with OBJECT() constructor (typed booleans).
       OBJECT('closed_account', FALSE),
       OBJECT('closed_account', TRUE, 'reason', 'Market exit / fraud-driven closure')
FROM bank_account WHERE closed_account;

INSERT INTO audit_trail (uuid, entity_type, entity_code, action, actor_user, occurred_at, before_image, after_image)
VALUES
    (gen_uuid('AT_BEC_DETECT'),'TRANSFER','BEC-2024-09-14','APPROVE','SECURITY_LEAD', TIMESTAMP '2024-09-14 11:42:00'::TIMESTAMPTZ,
        -- LINT FIX (#3): OBJECT() constructor instead of JSON_PARSE on string literals.
        OBJECT('status','PENDING'),
        OBJECT('status','BLOCKED','reason','BEC suspected')),
    (gen_uuid('AT_BEC_RECOVER'),'TRANSFER','BEC-2024-09-14','UPDATE','TREAS_HEAD', TIMESTAMP '2024-10-12 14:00:00'::TIMESTAMPTZ,
        OBJECT('recovery_pct', 0.0),
        OBJECT('recovery_pct', 0.65, 'amount_recovered', 312000));

INSERT INTO audit_trail (uuid, entity_type, entity_code, action, actor_user, occurred_at, before_image, after_image)
SELECT gen_uuid('AT_DEDES_'||hd.uuid), 'HEDGE_RELATIONSHIP', hd.hedge_ref, 'UPDATE',
       'TREAS_FRONT', hd.dedesignation_date::TIMESTAMPTZ,
       -- LINT FIX (#3): native OBJECT() avoids the incomplete string-concat JSON build (REPLACE missed backslash escapes and NULL-handling).
       OBJECT('status','ACTIVE'),
       OBJECT('status','DEDESIGNATED','reason', hd.reason)
FROM hedge_dedesignation hd;

INSERT INTO audit_trail (uuid, entity_type, entity_code, action, actor_user, occurred_at, before_image, after_image)
VALUES
    (gen_uuid('AT_LIQPOL_RAISE'),'LIQUIDITY_POLICY','LP_GROUP_MIN_LIQ_V2','APPROVE','CFO_GLOBAL',
     TIMESTAMP '2024-07-01 09:00:00'::TIMESTAMPTZ,
     -- LINT FIX (#3): OBJECT() constructor instead of JSON_PARSE.
     OBJECT('threshold_amount', 350000000),
     OBJECT('threshold_amount', 400000000, 'rationale', 'Higher buffer for FX volatility scenarios'));

-- -----------------------------------------------------------------------------
-- 13.7 DOCUMENT_ATTACHMENT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE document_attachment;
INSERT INTO document_attachment (uuid, entity_type, entity_code, file_name, content_type, size_bytes, storage_uri, uploaded_by, uploaded_at)
VALUES
    (gen_uuid('DOC_BEC_FORENSIC'),'TRANSFER','BEC-2024-09-14','BEC_forensic_report.pdf','application/pdf',
        2843910,'s3://lpp-attachments/incidents/2024/09/14/forensic.pdf','SECURITY_LEAD', TIMESTAMP '2024-09-30 16:30:00'::TIMESTAMPTZ),
    (gen_uuid('DOC_DB_RECON'),'GL_RECONCILIATION','GR_DE_OPERATING_1','BANK_DB_recon_investigation.xlsx',
        'application/vnd.ms-excel',184320,'s3://lpp-attachments/recon/db/q3-q4-2024.xlsx','CONTROLLER_GLOBAL', TIMESTAMP '2024-12-15 10:00:00'::TIMESTAMPTZ),
    (gen_uuid('DOC_IBERICA_WO'),'AR_INVOICE','TP_CUST_0042','Iberica_writeoff_authorization.pdf','application/pdf',
        421500,'s3://lpp-attachments/ar/2024/q1/iberica.pdf','CFO_GLOBAL', TIMESTAMP '2024-04-15 09:00:00'::TIMESTAMPTZ),
    (gen_uuid('DOC_LIQPOL_2024'),'LIQUIDITY_POLICY','LP_GROUP_MIN_LIQ_V2','board_resolution_2024_07.pdf','application/pdf',
        612400,'s3://lpp-attachments/policy/2024/board_resolution.pdf','CFO_GLOBAL', TIMESTAMP '2024-07-01 09:00:00'::TIMESTAMPTZ);

-- -----------------------------------------------------------------------------
-- 13.8 WEBHOOK_EVENT
-- -----------------------------------------------------------------------------
TRUNCATE TABLE webhook_event;

INSERT INTO webhook_event (uuid, event_type, entity_type, entity_code, payload, received_at, processed_at)
WITH pf_ranked AS (
    SELECT file_uuid, ROW_NUMBER() OVER (ORDER BY FNV_HASH(file_uuid)) AS rn
    FROM payment_file
)
SELECT gen_uuid('WH_'||CAST(n+1 AS VARCHAR)),
       (CASE MOD(n,5)
          WHEN 0 THEN 'BATCH_STATUS_CHANGED' WHEN 1 THEN 'ROUTING_STATUS_CHANGED'
          WHEN 2 THEN 'EXECUTION_FINISHED' WHEN 3 THEN 'DOCUMENT_APPROVED'
          ELSE 'ENTITY_CHANGED' END),
       'PAYMENT_FILE',
       -- LINT FIX (#6/correlated): correlated subquery containing a window function is not supported in Redshift.
       -- Replaced with a JOIN against a pre-ranked CTE `pf_ranked` (built below in the FROM clause).
       pf_ranked.file_uuid,
       -- LINT FIX (#3): OBJECT() avoids string-concat JSON build (clean typing for the seq integer).
       OBJECT('seq', n, 'timestamp', TO_CHAR(CURRENT_TIMESTAMP,'YYYY-MM-DD"T"HH24:MI:SS')),
       DATEADD(second, n, TIMESTAMP '2024-01-01 00:00:00')::TIMESTAMPTZ,
       DATEADD(second, n+5, TIMESTAMP '2024-01-01 00:00:00')::TIMESTAMPTZ
FROM gen_numbers
JOIN pf_ranked ON pf_ranked.rn = MOD(n,100)+1
WHERE n < 500;

-- VERIFY
SELECT 'APP_USER' AS t, COUNT(*) FROM app_user
UNION ALL SELECT 'USER_GROUP_MEMBER', COUNT(*) FROM user_group_member
UNION ALL SELECT 'DATA_PERMISSION_PROFILE', COUNT(*) FROM data_permission_profile
UNION ALL SELECT 'DATA_PERMISSION', COUNT(*) FROM data_permission
UNION ALL SELECT 'USER_PROFILE_ASSIGNMENT', COUNT(*) FROM user_profile_assignment
UNION ALL SELECT 'MAPPING_ENTRY', COUNT(*) FROM mapping_entry
UNION ALL SELECT 'AUDIT_TRAIL', COUNT(*) FROM audit_trail
UNION ALL SELECT 'DOCUMENT_ATTACHMENT', COUNT(*) FROM document_attachment
UNION ALL SELECT 'WEBHOOK_EVENT', COUNT(*) FROM webhook_event;
