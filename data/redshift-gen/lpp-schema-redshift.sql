-- =============================================================================
-- LPP data model — Amazon Redshift port
-- Source of truth: lpp-schema.sql (Snowflake flavor)
--
-- Dialect mappings applied throughout:
--   STRING                → VARCHAR(256)   (or VARCHAR(MAX) for narrative/memo/text)
--   VARIANT               → SUPER
--   TIMESTAMP_TZ          → TIMESTAMPTZ
--   NUMBER(p,s)           → NUMERIC(p,s)
--   CREATE OR REPLACE     → DROP TABLE IF EXISTS … CASCADE; CREATE TABLE …
--   PRIMARY/FOREIGN KEY constraints: declared (informational only — not enforced
--   by Redshift, but used by the planner). Treat as documentation + ETL invariants.
--
-- Distribution / sort keys are added on the high-cardinality fact tables.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS lpp;
SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- 1. CORE DATA (referential backbone)
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.currency CASCADE;
CREATE TABLE lpp.currency (
    uuid               VARCHAR(64)   NOT NULL,
    code               VARCHAR(16)   NOT NULL,
    description        VARCHAR(256),
    number_of_decimals SMALLINT,
    delivery_float     SMALLINT,
    is_reference       BOOLEAN DEFAULT FALSE,
    hide_in_list       BOOLEAN DEFAULT FALSE,
    CONSTRAINT pk_currency PRIMARY KEY (uuid),
    CONSTRAINT uk_currency_code UNIQUE (code)
)
DISTSTYLE ALL
SORTKEY (code);

DROP TABLE IF EXISTS lpp.bank CASCADE;
CREATE TABLE lpp.bank (
    uuid                          VARCHAR(64) NOT NULL,
    code                          VARCHAR(64) NOT NULL,
    interface_code                VARCHAR(64) NOT NULL,
    external_code                 VARCHAR(64),
    description1                  VARCHAR(256),
    description2                  VARCHAR(256),
    bic                           VARCHAR(16),
    lei                           VARCHAR(32),
    url_address                   VARCHAR(512),
    contact                       VARCHAR(256),
    intercompany                  BOOLEAN,
    internal_counterparty         BOOLEAN,
    counter_party_info            BOOLEAN,
    intermediary_info             BOOLEAN,
    net_settlements               BOOLEAN,
    net_debit_and_credit_exposure BOOLEAN,
    cash_exposure_limit_amount    NUMERIC(28,6),
    cash_exposure_limit_currency  VARCHAR(16),
    cash_exposure_limit_pct       NUMERIC(9,4),
    deal_identifier               VARCHAR(64),
    fx_confirmation_method        VARCHAR(64),
    loan_confirmation_method      VARCHAR(64),
    risk_tier_ref                 VARCHAR(64),
    parent_counterparty_ref       VARCHAR(64),
    default_group_ref             VARCHAR(64),
    third_party_ref               VARCHAR(64),
    address                       SUPER,
    user_zones                    SUPER,
    CONSTRAINT pk_bank PRIMARY KEY (uuid),
    CONSTRAINT uk_bank_code UNIQUE (code)
)
DISTSTYLE ALL
SORTKEY (code);

DROP TABLE IF EXISTS lpp.bank_group CASCADE;
CREATE TABLE lpp.bank_group (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_bank_group PRIMARY KEY (uuid),
    CONSTRAINT uk_bank_group_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.bank_group_member CASCADE;
CREATE TABLE lpp.bank_group_member (
    bank_group_code VARCHAR(64) NOT NULL,
    bank_code       VARCHAR(64) NOT NULL,
    CONSTRAINT pk_bgm PRIMARY KEY (bank_group_code, bank_code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.bank_branch CASCADE;
CREATE TABLE lpp.bank_branch (
    uuid                  VARCHAR(64) NOT NULL,
    code                  VARCHAR(64) NOT NULL,
    interface_code        VARCHAR(64) NOT NULL,
    bank_ref              VARCHAR(64) NOT NULL,
    description           VARCHAR(256),
    description2          VARCHAR(256),
    bic                   VARCHAR(16),
    corp_id_code          VARCHAR(64),
    account_location      VARCHAR(64),
    time_zone             VARCHAR(64) NOT NULL,
    cut_off_time          VARCHAR(8),
    calendar_ref          VARCHAR(64) NOT NULL,
    intercompany          BOOLEAN,
    intermediary          BOOLEAN,
    main_country_branch   BOOLEAN,
    responder_code        VARCHAR(64),
    id_of_application     VARCHAR(64),
    service_name          VARCHAR(128),
    interact_service_name VARCHAR(128),
    memo                  VARCHAR(MAX),
    address               SUPER,
    contact               SUPER,
    other_identifier      SUPER,
    user_zone             SUPER,
    country_code          VARCHAR(2),     -- enrichment (ISO-3166)
    region                VARCHAR(16),    -- AMER | EMEA | APAC | LATAM | MENA
    CONSTRAINT pk_branch PRIMARY KEY (uuid),
    CONSTRAINT uk_branch_code UNIQUE (code),
    CONSTRAINT fk_branch_bank FOREIGN KEY (bank_ref) REFERENCES lpp.bank(code)
)
DISTSTYLE ALL
SORTKEY (code);

DROP TABLE IF EXISTS lpp.company CASCADE;
CREATE TABLE lpp.company (
    uuid                       VARCHAR(64) NOT NULL,
    code                       VARCHAR(64) NOT NULL,
    name                       VARCHAR(256),
    country                    VARCHAR(2),
    consolidation_code         VARCHAR(64),
    corp_id_code               VARCHAR(64),
    lei                        VARCHAR(32),
    other_identifier_type      INTEGER,
    other_identifier_value     VARCHAR(64),
    sepa_creditor_identifier   VARCHAR(64),
    txp                        VARCHAR(64),
    tax_ids                    SUPER,
    address                    SUPER,
    contact                    SUPER,
    user_zones                 SUPER,
    CONSTRAINT pk_company PRIMARY KEY (uuid),
    CONSTRAINT uk_company_code UNIQUE (code)
)
DISTSTYLE ALL
SORTKEY (code);

DROP TABLE IF EXISTS lpp.company_group CASCADE;
CREATE TABLE lpp.company_group (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_company_group PRIMARY KEY (uuid),
    CONSTRAINT uk_company_group_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.company_group_member CASCADE;
CREATE TABLE lpp.company_group_member (
    company_group_code VARCHAR(64) NOT NULL,
    company_code       VARCHAR(64) NOT NULL,
    CONSTRAINT pk_cgm PRIMARY KEY (company_group_code, company_code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.bank_account CASCADE;
CREATE TABLE lpp.bank_account (
    uuid                                      VARCHAR(64) NOT NULL,
    code                                      VARCHAR(64) NOT NULL,
    description                               VARCHAR(256),
    description2                              VARCHAR(256),
    account_type                              VARCHAR(32),
    currency_ref                              VARCHAR(16) NOT NULL,
    company_ref                               VARCHAR(64) NOT NULL,
    branch_ref                                VARCHAR(64) NOT NULL,
    default_group_ref                         VARCHAR(64),
    bank_account_id                           SUPER,
    bank_account_ids                          SUPER,
    address                                   SUPER,
    contact                                   SUPER,
    calendar_ref                              VARCHAR(64) NOT NULL,
    time_zone                                 VARCHAR(64) NOT NULL,
    cut_off_time                              VARCHAR(8),
    opening_date                              DATE,
    closing_date                              DATE,
    closed_account                            BOOLEAN DEFAULT FALSE,
    hidden                                    BOOLEAN DEFAULT FALSE,
    non_resident                              BOOLEAN DEFAULT FALSE,
    bank_statement_layout                     VARCHAR(64),
    integrate_end_of_day_statements           BOOLEAN,
    integrate_intraday_statements             BOOLEAN,
    consider_one_day_float_transactions       BOOLEAN,
    consider_two_day_float_transactions       BOOLEAN,
    consider_three_day_float_transactions     BOOLEAN,
    consider_investment_position_transactions BOOLEAN,
    consider_bank_statements_from             DATE,
    zba_generator                             BOOLEAN,
    zba_identifier                            VARCHAR(64),
    generate_zba_flow                         VARCHAR(64),
    settlement_account_ref                    VARCHAR(64),
    counterparty_settlement_account_ref       VARCHAR(64),
    chart_of_accounts_ref                     VARCHAR(64),
    gl_account_ref                            VARCHAR(64),
    internal_account_code                     VARCHAR(64),
    include_in_gl_reconciliation              BOOLEAN,
    initial_accounting_balance                NUMERIC(28,6),
    initial_accounting_balance_ccy            VARCHAR(16),
    initial_accounting_balance_date           DATE,
    interest_bearing                          BOOLEAN,
    centrally_managed                         BOOLEAN,
    owner_name                                VARCHAR(256),
    reconciler_name                           VARCHAR(256),
    account_available_for_payments            BOOLEAN,
    domestic_transfer                         VARCHAR(64),
    international_transfer                    VARCHAR(64),
    maturity_transfer                         VARCHAR(64),
    domestic_direct_debit                     VARCHAR(64),
    international_direct_debit                VARCHAR(64),
    payables_drafts                           VARCHAR(64),
    receivables_drafts                        VARCHAR(64),
    payment_reconciliation_options            SUPER,
    account_payment_instructions              SUPER,
    signatory_users                           SUPER,
    establishments                            SUPER,
    account_category1                         VARCHAR(64),
    account_category2                         VARCHAR(64),
    account_category3                         VARCHAR(64),
    account_category4                         VARCHAR(64),
    account_category5                         VARCHAR(64),
    account_category6                         VARCHAR(64),
    account_category7                         VARCHAR(64),
    account_category8                         VARCHAR(64),
    account_category9                         VARCHAR(64),
    account_category10                        VARCHAR(64),
    free_text1                                VARCHAR(256),
    free_text2                                VARCHAR(256),
    free_text3                                VARCHAR(256),
    free_amount1                              NUMERIC(28,6),
    free_amount2                              NUMERIC(28,6),
    free_amount3                              NUMERIC(28,6),
    memo                                      VARCHAR(MAX),
    user_zone                                 SUPER,
    account_purpose                           VARCHAR(32),    -- OPERATING | COLLECTION | CONCENTRATION | DISBURSEMENT | PAYROLL | TAX | INVESTMENT | OTHER
    min_operating_balance                     NUMERIC(28,6),
    min_operating_balance_ccy                 VARCHAR(16),
    CONSTRAINT pk_bank_account PRIMARY KEY (uuid),
    CONSTRAINT uk_bank_account_code UNIQUE (code),
    CONSTRAINT fk_ba_currency FOREIGN KEY (currency_ref) REFERENCES lpp.currency(code),
    CONSTRAINT fk_ba_company  FOREIGN KEY (company_ref)  REFERENCES lpp.company(code),
    CONSTRAINT fk_ba_branch   FOREIGN KEY (branch_ref)   REFERENCES lpp.bank_branch(code)
)
DISTSTYLE ALL
SORTKEY (code);

DROP TABLE IF EXISTS lpp.bank_account_group CASCADE;
CREATE TABLE lpp.bank_account_group (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_bag PRIMARY KEY (uuid),
    CONSTRAINT uk_bag_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.bank_account_group_member CASCADE;
CREATE TABLE lpp.bank_account_group_member (
    account_group_code VARCHAR(64) NOT NULL,
    bank_account_code  VARCHAR(64) NOT NULL,
    CONSTRAINT pk_bagm PRIMARY KEY (account_group_code, bank_account_code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.third_party CASCADE;
CREATE TABLE lpp.third_party (
    uuid                         VARCHAR(64) NOT NULL,
    code                         VARCHAR(64) NOT NULL,
    third_party_type             VARCHAR(32) NOT NULL,
    name                         VARCHAR(256),
    name2                        VARCHAR(256),
    first_name                   VARCHAR(128),
    last_name                    VARCHAR(128),
    birth_date                   DATE,
    hidden                       BOOLEAN DEFAULT FALSE,
    closure_date                 DATE,
    creditor                     BOOLEAN,
    debtor                       BOOLEAN,
    non_resident                 BOOLEAN,
    corp_id_code                 VARCHAR(64),
    other_identifier             SUPER,
    creditor_tax_id              VARCHAR(64),
    creditor_tax_registration_id VARCHAR(64),
    creditor_tax_type            VARCHAR(64),
    creditor_agent_instruction   VARCHAR(256),
    link_number_ref              VARCHAR(64),
    portfolio_ref                VARCHAR(64),
    folder_ref                   VARCHAR(64),
    limit_currency_ref           VARCHAR(16) DEFAULT 'EUR',
    transaction_entry_limit      NUMERIC(28,6) DEFAULT 0,
    transaction_max_number       INTEGER,
    company_selection            VARCHAR(64),
    used_by_company_ref          VARCHAR(64),
    used_by_companies            SUPER,
    company_ownership_ref        VARCHAR(64),
    address                      SUPER,
    contact                      SUPER,
    memo                         VARCHAR(MAX),
    user_zones                   SUPER,
    CONSTRAINT pk_third_party PRIMARY KEY (uuid),
    CONSTRAINT uk_third_party_code UNIQUE (code)
    -- CHECK constraints not supported on this Redshift version
    -- CONSTRAINT ck_third_party_type CHECK (third_party_type IN ('ORGANIZATION','INDIVIDUAL'))
)
DISTSTYLE ALL
SORTKEY (code);

DROP TABLE IF EXISTS lpp.third_party_category CASCADE;
CREATE TABLE lpp.third_party_category (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_tpc PRIMARY KEY (uuid),
    CONSTRAINT uk_tpc_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.third_party_category_assignment CASCADE;
CREATE TABLE lpp.third_party_category_assignment (
    third_party_code VARCHAR(64) NOT NULL,
    category_code    VARCHAR(64) NOT NULL,
    CONSTRAINT pk_tpca PRIMARY KEY (third_party_code, category_code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.third_party_bank_account CASCADE;
CREATE TABLE lpp.third_party_bank_account (
    uuid             VARCHAR(64) NOT NULL,
    third_party_code VARCHAR(64) NOT NULL,
    branch_ref       VARCHAR(64),
    currency_ref     VARCHAR(16),
    bank_account_id  SUPER,
    is_default       BOOLEAN DEFAULT FALSE,
    CONSTRAINT pk_tpba PRIMARY KEY (uuid),
    CONSTRAINT fk_tpba_tp FOREIGN KEY (third_party_code) REFERENCES lpp.third_party(code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.cash_flow_code CASCADE;
CREATE TABLE lpp.cash_flow_code (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    sign        VARCHAR(8),    -- IN | OUT
    category    VARCHAR(32),   -- OPERATIONS | INVESTING | FINANCING
    CONSTRAINT pk_cfc PRIMARY KEY (uuid),
    CONSTRAINT uk_cfc_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.budget_code CASCADE;
CREATE TABLE lpp.budget_code (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_bc PRIMARY KEY (uuid),
    CONSTRAINT uk_bc_code UNIQUE (code)
)
DISTSTYLE ALL;

-- -----------------------------------------------------------------------------
-- 2. TRANSACTIONAL DATA
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.cash_flow CASCADE;
CREATE TABLE lpp.cash_flow (
    uuid              VARCHAR(64) NOT NULL,
    account_ref       VARCHAR(64) NOT NULL,
    flow_code_ref     VARCHAR(64) NOT NULL,
    budget_code_ref   VARCHAR(64),
    status            VARCHAR(32),
    transaction_date  DATE,
    value_date        DATE,
    update_date_time  TIMESTAMPTZ,
    flow_amount       NUMERIC(28,6) NOT NULL,
    flow_currency     VARCHAR(16)   NOT NULL,
    signed_amount     NUMERIC(28,6),
    account_amount    NUMERIC(28,6),
    account_currency  VARCHAR(16),
    fx_rate           NUMERIC(20,10),
    counterparty_name VARCHAR(256),
    counterparty_ref  VARCHAR(64),
    description       VARCHAR(512),
    reference         VARCHAR(128),
    user_zones        SUPER,
    payment_rail      VARCHAR(16),     -- WIRE | ACH | SEPA_CT | …
    CONSTRAINT pk_cash_flow PRIMARY KEY (uuid),
    CONSTRAINT fk_cf_account FOREIGN KEY (account_ref) REFERENCES lpp.bank_account(code),
    CONSTRAINT fk_cf_code    FOREIGN KEY (flow_code_ref) REFERENCES lpp.cash_flow_code(code)
)
DISTKEY (account_ref)
SORTKEY (value_date, account_ref);

DROP TABLE IF EXISTS lpp.bank_statement_balance CASCADE;
CREATE TABLE lpp.bank_statement_balance (
    uuid           VARCHAR(64),
    account_ref    VARCHAR(64) NOT NULL,
    statement_date DATE        NOT NULL,
    balance_type   VARCHAR(16),
    amount         NUMERIC(28,6) NOT NULL,
    currency_code  VARCHAR(16)   NOT NULL,
    source_file    VARCHAR(256),
    quality_status VARCHAR(16),
    CONSTRAINT pk_bsb PRIMARY KEY (account_ref, statement_date, balance_type),
    CONSTRAINT fk_bsb_account FOREIGN KEY (account_ref) REFERENCES lpp.bank_account(code)
)
DISTKEY (account_ref)
SORTKEY (statement_date, account_ref);

DROP TABLE IF EXISTS lpp.cash_balance CASCADE;
CREATE TABLE lpp.cash_balance (
    account_ref         VARCHAR(64) NOT NULL,
    balance_date        DATE        NOT NULL,
    date_basis          VARCHAR(32) NOT NULL,
    includes_actual     BOOLEAN NOT NULL,
    includes_intraday   BOOLEAN NOT NULL,
    includes_confirmed  BOOLEAN NOT NULL,
    includes_estimated  BOOLEAN NOT NULL,
    amount              NUMERIC(28,6) NOT NULL,
    currency_code       VARCHAR(16)   NOT NULL,
    cash_flow_status    VARCHAR(32),
    CONSTRAINT pk_cash_balance PRIMARY KEY
        (account_ref, balance_date, date_basis, includes_actual, includes_intraday, includes_confirmed, includes_estimated),
    CONSTRAINT fk_cb_account FOREIGN KEY (account_ref) REFERENCES lpp.bank_account(code)
)
DISTKEY (account_ref)
SORTKEY (balance_date, account_ref);

DROP TABLE IF EXISTS lpp.payment_file CASCADE;
CREATE TABLE lpp.payment_file (
    file_uuid      VARCHAR(64) NOT NULL,
    file_name      VARCHAR(256),
    company_ref    VARCHAR(64),
    account_ref    VARCHAR(64),
    status         VARCHAR(32),
    routing_status VARCHAR(32),
    total_count    INTEGER,
    total_amount   NUMERIC(28,6),
    total_currency VARCHAR(16),
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ,
    CONSTRAINT pk_payment_file PRIMARY KEY (file_uuid)
)
DISTSTYLE AUTO
SORTKEY (created_at);

DROP TABLE IF EXISTS lpp.transfer CASCADE;
CREATE TABLE lpp.transfer (
    uuid                   VARCHAR(64) NOT NULL,
    transaction_number     VARCHAR(64),
    reference              VARCHAR(128),
    file_uuid              VARCHAR(64),
    file_name              VARCHAR(256),
    status                 VARCHAR(32),
    next_action            VARCHAR(64),
    ack_status             VARCHAR(32),
    ack_code               VARCHAR(32),
    ack_message            VARCHAR(512),
    last_ack_time          TIMESTAMPTZ,
    remittance_identifier1 VARCHAR(128),
    remittance_identifier2 VARCHAR(128),
    remittance             SUPER,
    remittance2            SUPER,
    repository             SUPER,
    payment_rail           VARCHAR(16),
    amount                 NUMERIC(28,6),
    currency_code          VARCHAR(16),
    value_date             DATE,
    CONSTRAINT pk_transfer PRIMARY KEY (uuid),
    CONSTRAINT fk_transfer_file FOREIGN KEY (file_uuid) REFERENCES lpp.payment_file(file_uuid)
)
DISTKEY (file_uuid)
SORTKEY (value_date);

DROP TABLE IF EXISTS lpp.payment_transaction CASCADE;
CREATE TABLE lpp.payment_transaction (
    uuid                 VARCHAR(64) NOT NULL,
    file_uuid            VARCHAR(64) NOT NULL,
    end_to_end_id        VARCHAR(64),
    transaction_date     DATE,
    execution_date       DATE,
    status               VARCHAR(32),
    reason_code          VARCHAR(32),
    amount               NUMERIC(28,6),
    currency_code        VARCHAR(16),
    issuer_name          VARCHAR(256),
    issuer_account       VARCHAR(64),
    counterparty_name    VARCHAR(256),
    counterparty_account VARCHAR(64),
    reference            VARCHAR(128),
    last_ack_time        TIMESTAMPTZ,
    payment_rail         VARCHAR(16),
    CONSTRAINT pk_pt PRIMARY KEY (uuid),
    CONSTRAINT fk_pt_file FOREIGN KEY (file_uuid) REFERENCES lpp.payment_file(file_uuid)
)
DISTKEY (file_uuid)
SORTKEY (execution_date);

DROP TABLE IF EXISTS lpp.fraud_detection_event CASCADE;
CREATE TABLE lpp.fraud_detection_event (
    uuid          VARCHAR(64) NOT NULL,
    transfer_uuid VARCHAR(64),
    file_uuid     VARCHAR(64),
    decision      VARCHAR(16),
    score         NUMERIC(6,3),
    reason_codes  SUPER,
    raised_at     TIMESTAMPTZ,
    CONSTRAINT pk_fde PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (raised_at);

DROP TABLE IF EXISTS lpp.hedge_dedesignation CASCADE;
CREATE TABLE lpp.hedge_dedesignation (
    uuid               VARCHAR(64) NOT NULL,
    hedge_ref          VARCHAR(64),
    company_ref        VARCHAR(64),
    currency_ref       VARCHAR(16),
    dedesignation_date DATE,
    amount             NUMERIC(28,6),
    reason             VARCHAR(512),
    CONSTRAINT pk_hd PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (dedesignation_date);

DROP TABLE IF EXISTS lpp.wcf_document CASCADE;
CREATE TABLE lpp.wcf_document (
    uuid                    VARCHAR(64) NOT NULL,
    document_number         VARCHAR(64),
    supplier_ref            VARCHAR(64),
    buyer_company           VARCHAR(64),
    status                  VARCHAR(32),
    issue_date              DATE,
    due_date                DATE,
    amount                  NUMERIC(28,6),
    currency_code           VARCHAR(16),
    early_payment_terms_ref VARCHAR(64),
    CONSTRAINT pk_wcf_doc PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (due_date);

-- -----------------------------------------------------------------------------
-- 3. ACCESS CONTROL (cross-cutting)
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.app_user CASCADE;
CREATE TABLE lpp.app_user (
    uuid       VARCHAR(64) NOT NULL,
    code       VARCHAR(64) NOT NULL,
    first_name VARCHAR(128),
    last_name  VARCHAR(128),
    email      VARCHAR(256),
    active     BOOLEAN DEFAULT TRUE,
    CONSTRAINT pk_user PRIMARY KEY (uuid),
    CONSTRAINT uk_user_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.user_group CASCADE;
CREATE TABLE lpp.user_group (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_ug PRIMARY KEY (uuid),
    CONSTRAINT uk_ug_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.user_group_member CASCADE;
CREATE TABLE lpp.user_group_member (
    user_group_code VARCHAR(64) NOT NULL,
    user_code       VARCHAR(64) NOT NULL,
    CONSTRAINT pk_ugm PRIMARY KEY (user_group_code, user_code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.data_permission_profile CASCADE;
CREATE TABLE lpp.data_permission_profile (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    CONSTRAINT pk_dpp PRIMARY KEY (uuid),
    CONSTRAINT uk_dpp_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.user_profile_assignment CASCADE;
CREATE TABLE lpp.user_profile_assignment (
    user_code    VARCHAR(64) NOT NULL,
    profile_code VARCHAR(64) NOT NULL,
    CONSTRAINT pk_upa PRIMARY KEY (user_code, profile_code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.data_permission CASCADE;
CREATE TABLE lpp.data_permission (
    uuid         VARCHAR(64) NOT NULL,
    profile_code VARCHAR(64) NOT NULL,
    entity_type  VARCHAR(32) NOT NULL,
    entity_code  VARCHAR(64) NOT NULL,
    CONSTRAINT pk_dp PRIMARY KEY (uuid),
    CONSTRAINT uk_dp_triple UNIQUE (profile_code, entity_type, entity_code)
)
DISTSTYLE ALL;

-- -----------------------------------------------------------------------------
-- 4. CROSS-CUTTING: audit, mapping, attachments, webhooks
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.audit_trail CASCADE;
CREATE TABLE lpp.audit_trail (
    uuid         VARCHAR(64) NOT NULL,
    entity_type  VARCHAR(32) NOT NULL,
    entity_code  VARCHAR(64) NOT NULL,
    action       VARCHAR(32),
    actor_user   VARCHAR(64),
    occurred_at  TIMESTAMPTZ,
    before_image SUPER,
    after_image  SUPER,
    CONSTRAINT pk_audit PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (occurred_at);

DROP TABLE IF EXISTS lpp.mapping_table CASCADE;
CREATE TABLE lpp.mapping_table (
    uuid        VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    scope       VARCHAR(64),
    CONSTRAINT pk_mt PRIMARY KEY (uuid),
    CONSTRAINT uk_mt_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.mapping_entry CASCADE;
CREATE TABLE lpp.mapping_entry (
    mapping_table_code VARCHAR(64) NOT NULL,
    source_value       VARCHAR(256) NOT NULL,
    target_value       VARCHAR(256) NOT NULL,
    CONSTRAINT pk_me PRIMARY KEY (mapping_table_code, source_value)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.document_attachment CASCADE;
CREATE TABLE lpp.document_attachment (
    uuid         VARCHAR(64) NOT NULL,
    entity_type  VARCHAR(32) NOT NULL,
    entity_code  VARCHAR(64) NOT NULL,
    file_name    VARCHAR(256),
    content_type VARCHAR(64),
    size_bytes   BIGINT,
    storage_uri  VARCHAR(1024),    -- S3 URI
    uploaded_by  VARCHAR(64),
    uploaded_at  TIMESTAMPTZ,
    CONSTRAINT pk_attach PRIMARY KEY (uuid)
);

DROP TABLE IF EXISTS lpp.webhook_event CASCADE;
CREATE TABLE lpp.webhook_event (
    uuid         VARCHAR(64) NOT NULL,
    event_type   VARCHAR(64) NOT NULL,
    entity_type  VARCHAR(32),
    entity_code  VARCHAR(64),
    payload      SUPER,
    received_at  TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    CONSTRAINT pk_wh PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (received_at);
