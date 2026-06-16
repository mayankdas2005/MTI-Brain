-- =============================================================================
-- 17_signatory.sql (Redshift) — Section R of extensions #2.
--
-- Populates bank_account_signatory: 2-4 signatories per active BANK_ACCOUNT
-- with role SIGNER_A / SIGNER_B / RELEASER plus an authority limit.
--
-- Scripted scenario:
--   • next_recertify_due_date staggered across the next 12 months, but
--     ≥45-80 signatories fall due within 90 days of WINDOW_END (2026-05-04).
--     This drives the Manager-F #10 recertification-backlog prompt.
-- =============================================================================
SET search_path TO lpp;

TRUNCATE TABLE bank_account_signatory;

-- We assign 4 roles (SIGNER_A x2, SIGNER_B, RELEASER) to each non-closed
-- bank_account, picking signatories deterministically from the cash-manager
-- pool by region.
INSERT INTO bank_account_signatory (
    uuid, bank_account_ref, user_ref, role, authority_limit_amount, authority_limit_currency,
    granted_date, last_recertified_date, next_recertify_due_date, status
)
SELECT
    gen_uuid('BAS_'||ba.code||'_'||r.role||'_'||r.slot),
    ba.code,
    -- Pick a user by (region, role, slot) hash
    CASE r.role
      WHEN 'SIGNER_A' THEN
        CASE COALESCE(br.region,'AMER')
          WHEN 'AMER' THEN CASE MOD(ABS(FNV_HASH('SA_'||ba.code||r.slot)),3)
                              WHEN 0 THEN 'TREAS_HEAD' WHEN 1 THEN 'CASH_MANAGER_AMER' ELSE 'CFO_GLOBAL' END
          WHEN 'EMEA' THEN CASE MOD(ABS(FNV_HASH('SA_'||ba.code||r.slot)),3)
                              WHEN 0 THEN 'TREAS_HEAD' WHEN 1 THEN 'CASH_MANAGER_EMEA' ELSE 'CONTROLLER_GLOBAL' END
          WHEN 'APAC' THEN CASE MOD(ABS(FNV_HASH('SA_'||ba.code||r.slot)),3)
                              WHEN 0 THEN 'TREAS_HEAD' WHEN 1 THEN 'CASH_MANAGER_APAC' ELSE 'CONTROLLER_GLOBAL' END
          ELSE 'TREAS_HEAD' END
      WHEN 'SIGNER_B' THEN
        CASE COALESCE(br.region,'AMER')
          WHEN 'AMER' THEN 'TREAS_FRONT'
          WHEN 'EMEA' THEN 'TREAS_MIDDLE'
          WHEN 'APAC' THEN 'TREAS_BACK'
          ELSE 'TREAS_FRONT' END
      ELSE 'TREAS_BACK'   -- RELEASER
    END,
    r.role,
    CASE r.role
      WHEN 'SIGNER_A' THEN 50000000
      WHEN 'SIGNER_B' THEN 10000000
      ELSE 5000000 END,
    ba.currency_ref,
    DATE '2022-01-15',
    -- last_recertified ~12-15 months ago, distributed
    DATEADD(day, -MOD(ABS(FNV_HASH('LR_'||ba.code||r.role||r.slot)),120) - 240,
                  DATE '2026-05-04'),
    -- next_recertify_due_date:
    --   80% spread evenly over 12 months ahead of WINDOW_END
    --   20% deliberately due within next 90 days of WINDOW_END
    CASE WHEN MOD(ABS(FNV_HASH('NR_'||ba.code||r.role||r.slot)),100) < 20
         THEN DATEADD(day, MOD(ABS(FNV_HASH('NR_'||ba.code||r.role||r.slot)),90),
                      DATE '2026-05-04')
         ELSE DATEADD(day, 90 + MOD(ABS(FNV_HASH('NR_'||ba.code||r.role||r.slot)),275),
                      DATE '2026-05-04') END,
    'ACTIVE'
FROM bank_account ba
LEFT JOIN bank_branch br ON br.code = ba.branch_ref
LEFT JOIN bank b ON b.code = br.bank_ref
CROSS JOIN (
    SELECT 'SIGNER_A' AS role, 'A1' AS slot UNION ALL
    SELECT 'SIGNER_A', 'A2' UNION ALL
    SELECT 'SIGNER_B', 'B1' UNION ALL
    SELECT 'RELEASER', 'R1'
) r
WHERE NOT ba.closed_account;

-- VERIFY
SELECT 'bank_account_signatory' AS t, COUNT(*) FROM bank_account_signatory;
SELECT 'sig_due_within_90d' AS t,
       COUNT(*) AS rows_due_within_90d
FROM bank_account_signatory
WHERE next_recertify_due_date BETWEEN DATE '2026-05-04' AND DATEADD(day, 90, DATE '2026-05-04');
