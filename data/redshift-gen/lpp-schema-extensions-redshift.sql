-- =============================================================================
-- LPP data model — EXTENSIONS (Amazon Redshift port)
-- Source of truth: lpp-schema-extensions.sql (Snowflake flavor)
--
-- Sections:
--   B. FX & RATES
--   C. GL / ERP RECONCILIATION
--   D. INVESTMENTS & BENCHMARKS
--   E. DEBT / CREDIT FACILITIES
--   F. HEDGES, FX FORWARDS & EXPOSURES
--   G. CASH CONCENTRATION & SWEEPS
--   H. FORECASTS, LIQUIDITY POLICY & SCENARIOS
--   I. BANK FEES & RATE CARDS
--   J. AR / AP INVOICES (working capital)
--   K. INTERCOMPANY LEG LINKAGE
--   L. TRIBAL KNOWLEDGE & SME FEEDBACK  (Brain layer)
--   L.1 GRAPH-RAG SUPPORT — OpenSearch Serverless hooks (vectors live OUTSIDE Redshift)
--
-- Note: Section A (additive ALTERs) is rolled into lpp-schema-redshift.sql
-- because Redshift's DROP+CREATE pattern makes the columns part of the base DDL.
-- =============================================================================

SET search_path TO lpp;

-- -----------------------------------------------------------------------------
-- B. FX & RATES
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.fx_rate CASCADE;
CREATE TABLE lpp.fx_rate (
    rate_date       DATE   NOT NULL,
    base_currency   VARCHAR(16) NOT NULL,
    quote_currency  VARCHAR(16) NOT NULL,
    rate            NUMERIC(20,10) NOT NULL,
    rate_type       VARCHAR(16) NOT NULL,   -- SPOT | CLOSING | AVG | FORWARD
    source          VARCHAR(64),
    as_of_timestamp TIMESTAMPTZ,
    CONSTRAINT pk_fx_rate PRIMARY KEY (rate_date, base_currency, quote_currency, rate_type)
)
DISTSTYLE ALL
SORTKEY (rate_date);

-- -----------------------------------------------------------------------------
-- C. GL / ERP RECONCILIATION
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.gl_account CASCADE;
CREATE TABLE lpp.gl_account (
    uuid              VARCHAR(64) NOT NULL,
    code              VARCHAR(64) NOT NULL,
    description       VARCHAR(256),
    chart_of_accounts VARCHAR(64),
    company_ref       VARCHAR(64),
    bank_account_ref  VARCHAR(64),
    account_class     VARCHAR(16),     -- ASSET | LIABILITY | EQUITY | REVENUE | EXPENSE
    currency_ref      VARCHAR(16),
    CONSTRAINT pk_gl_account PRIMARY KEY (uuid),
    CONSTRAINT uk_gl_account_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.gl_balance CASCADE;
CREATE TABLE lpp.gl_balance (
    gl_account_ref VARCHAR(64) NOT NULL,
    balance_date   DATE        NOT NULL,
    balance_type   VARCHAR(16) NOT NULL,    -- OPENING | CLOSING | PERIOD
    amount         NUMERIC(28,6) NOT NULL,
    currency_code  VARCHAR(16)   NOT NULL,
    source_system  VARCHAR(32),
    loaded_at      TIMESTAMPTZ,
    CONSTRAINT pk_gl_balance PRIMARY KEY (gl_account_ref, balance_date, balance_type)
)
DISTKEY (gl_account_ref)
SORTKEY (balance_date);

DROP TABLE IF EXISTS lpp.gl_reconciliation CASCADE;
CREATE TABLE lpp.gl_reconciliation (
    uuid              VARCHAR(64) NOT NULL,
    bank_account_ref  VARCHAR(64) NOT NULL,
    gl_account_ref    VARCHAR(64) NOT NULL,
    as_of_date        DATE        NOT NULL,
    bank_balance      NUMERIC(28,6),
    gl_balance        NUMERIC(28,6),
    variance_amount   NUMERIC(28,6),
    variance_currency VARCHAR(16),
    status            VARCHAR(32),
    notes             VARCHAR(MAX),
    CONSTRAINT pk_gl_recon PRIMARY KEY (uuid)
)
DISTKEY (bank_account_ref)
SORTKEY (as_of_date);

-- -----------------------------------------------------------------------------
-- D. INVESTMENTS & BENCHMARKS
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.investment_instrument CASCADE;
CREATE TABLE lpp.investment_instrument (
    uuid            VARCHAR(64) NOT NULL,
    code            VARCHAR(64) NOT NULL,
    instrument_type VARCHAR(32) NOT NULL,   -- MMF | TIME_DEPOSIT | CD | TREASURY | COMMERCIAL_PAPER | REPO | BOND | OTHER
    issuer_name     VARCHAR(256),
    issuer_bank_ref VARCHAR(64),
    currency_ref    VARCHAR(16) NOT NULL,
    coupon_rate     NUMERIC(9,6),
    issue_date      DATE,
    maturity_date   DATE,
    rating          VARCHAR(16),
    CONSTRAINT pk_inv_instr PRIMARY KEY (uuid),
    CONSTRAINT uk_inv_instr_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.investment_position CASCADE;
CREATE TABLE lpp.investment_position (
    uuid              VARCHAR(64) NOT NULL,
    instrument_ref    VARCHAR(64) NOT NULL,
    company_ref       VARCHAR(64) NOT NULL,
    bank_account_ref  VARCHAR(64),
    as_of_date        DATE        NOT NULL,
    face_amount       NUMERIC(28,6),
    market_value      NUMERIC(28,6),
    book_value        NUMERIC(28,6),
    accrued_interest  NUMERIC(28,6),
    currency_code     VARCHAR(16) NOT NULL,
    yield_to_maturity NUMERIC(9,6),
    duration_days     NUMERIC(9,2),
    CONSTRAINT pk_inv_pos PRIMARY KEY (uuid)
)
DISTKEY (instrument_ref)
SORTKEY (as_of_date);

DROP TABLE IF EXISTS lpp.investment_transaction CASCADE;
CREATE TABLE lpp.investment_transaction (
    uuid             VARCHAR(64) NOT NULL,
    instrument_ref   VARCHAR(64) NOT NULL,
    company_ref      VARCHAR(64) NOT NULL,
    bank_account_ref VARCHAR(64),
    transaction_type VARCHAR(32) NOT NULL,
    trade_date       DATE,
    settle_date      DATE,
    amount           NUMERIC(28,6),
    currency_code    VARCHAR(16),
    price            NUMERIC(20,10),
    yield_at_trade   NUMERIC(9,6),
    CONSTRAINT pk_inv_txn PRIMARY KEY (uuid)
)
DISTKEY (instrument_ref)
SORTKEY (trade_date);

DROP TABLE IF EXISTS lpp.benchmark_rate CASCADE;
CREATE TABLE lpp.benchmark_rate (
    benchmark_code VARCHAR(32) NOT NULL,
    rate_date      DATE        NOT NULL,
    tenor          VARCHAR(8),
    rate           NUMERIC(9,6) NOT NULL,
    currency_code  VARCHAR(16),
    source         VARCHAR(64),
    CONSTRAINT pk_benchmark PRIMARY KEY (benchmark_code, rate_date, tenor)
)
DISTSTYLE ALL
SORTKEY (rate_date);

-- -----------------------------------------------------------------------------
-- E. DEBT / CREDIT FACILITIES
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.credit_facility CASCADE;
CREATE TABLE lpp.credit_facility (
    uuid               VARCHAR(64) NOT NULL,
    code               VARCHAR(64) NOT NULL,
    facility_type      VARCHAR(32) NOT NULL,
    company_ref        VARCHAR(64) NOT NULL,
    lender_bank_ref    VARCHAR(64),
    currency_ref       VARCHAR(16) NOT NULL,
    commitment_amount  NUMERIC(28,6),
    start_date         DATE,
    maturity_date      DATE,
    benchmark_code     VARCHAR(32),
    spread_bps         NUMERIC(9,2),
    commitment_fee_bps NUMERIC(9,2),
    status             VARCHAR(16),
    CONSTRAINT pk_cf PRIMARY KEY (uuid),
    CONSTRAINT uk_cf_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.borrowing CASCADE;
CREATE TABLE lpp.borrowing (
    uuid             VARCHAR(64) NOT NULL,
    facility_ref     VARCHAR(64) NOT NULL,
    company_ref      VARCHAR(64) NOT NULL,
    drawdown_date    DATE,
    repayment_date   DATE,
    principal_amount NUMERIC(28,6),
    currency_code    VARCHAR(16),
    all_in_rate      NUMERIC(9,6),
    status           VARCHAR(16),
    CONSTRAINT pk_borrow PRIMARY KEY (uuid)
)
DISTKEY (facility_ref)
SORTKEY (drawdown_date);

DROP TABLE IF EXISTS lpp.interest_accrual CASCADE;
CREATE TABLE lpp.interest_accrual (
    uuid          VARCHAR(64) NOT NULL,
    accrual_date  DATE        NOT NULL,
    source_type   VARCHAR(32) NOT NULL,
    source_uuid   VARCHAR(64) NOT NULL,
    company_ref   VARCHAR(64),
    amount        NUMERIC(28,6) NOT NULL,
    currency_code VARCHAR(16)   NOT NULL,
    direction     VARCHAR(16)   NOT NULL,
    CONSTRAINT pk_int_accrual PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (accrual_date);

-- -----------------------------------------------------------------------------
-- F. HEDGES, FX FORWARDS & EXPOSURES
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.fx_forward CASCADE;
CREATE TABLE lpp.fx_forward (
    uuid                  VARCHAR(64) NOT NULL,
    deal_id               VARCHAR(64) NOT NULL,
    company_ref           VARCHAR(64) NOT NULL,
    counterparty_bank_ref VARCHAR(64),
    trade_date            DATE,
    value_date            DATE,
    buy_currency          VARCHAR(16) NOT NULL,
    buy_amount            NUMERIC(28,6) NOT NULL,
    sell_currency         VARCHAR(16) NOT NULL,
    sell_amount           NUMERIC(28,6) NOT NULL,
    forward_rate          NUMERIC(20,10),
    spot_at_trade         NUMERIC(20,10),
    status                VARCHAR(16),
    CONSTRAINT pk_fx_fwd PRIMARY KEY (uuid),
    CONSTRAINT uk_fx_fwd_dealid UNIQUE (deal_id)
)
DISTSTYLE AUTO
SORTKEY (value_date);

DROP TABLE IF EXISTS lpp.hedge_relationship CASCADE;
CREATE TABLE lpp.hedge_relationship (
    uuid                 VARCHAR(64) NOT NULL,
    code                 VARCHAR(64) NOT NULL,
    company_ref          VARCHAR(64) NOT NULL,
    hedge_type           VARCHAR(32) NOT NULL,
    hedged_item_type     VARCHAR(32),
    hedged_currency      VARCHAR(16) NOT NULL,
    designation_date     DATE,
    dedesignation_date   DATE,
    instrument_type      VARCHAR(32),
    instrument_ref       VARCHAR(64),
    notional_amount      NUMERIC(28,6),
    notional_currency    VARCHAR(16),
    effectiveness_method VARCHAR(32),
    status               VARCHAR(16),
    CONSTRAINT pk_hedge_rel PRIMARY KEY (uuid),
    CONSTRAINT uk_hedge_rel_code UNIQUE (code)
)
DISTSTYLE AUTO;

DROP TABLE IF EXISTS lpp.fx_exposure_forecast CASCADE;
CREATE TABLE lpp.fx_exposure_forecast (
    uuid                  VARCHAR(64) NOT NULL,
    company_ref           VARCHAR(64) NOT NULL,
    forecast_period       DATE        NOT NULL,
    tenor_bucket          VARCHAR(8),
    exposure_currency     VARCHAR(16) NOT NULL,
    functional_currency   VARCHAR(16) NOT NULL,
    gross_exposure_amount NUMERIC(28,6) NOT NULL,
    direction             VARCHAR(8)  NOT NULL,
    source                VARCHAR(32),
    snapshot_date         DATE NOT NULL,
    CONSTRAINT pk_fx_exp PRIMARY KEY (uuid)
)
DISTKEY (company_ref)
SORTKEY (forecast_period);

DROP TABLE IF EXISTS lpp.derivative_mtm CASCADE;
CREATE TABLE lpp.derivative_mtm (
    uuid                  VARCHAR(64) NOT NULL,
    instrument_type       VARCHAR(32) NOT NULL,
    instrument_ref        VARCHAR(64) NOT NULL,
    counterparty_bank_ref VARCHAR(64),
    company_ref           VARCHAR(64),
    valuation_date        DATE NOT NULL,
    mtm_amount            NUMERIC(28,6) NOT NULL,
    mtm_currency          VARCHAR(16) NOT NULL,
    CONSTRAINT pk_der_mtm PRIMARY KEY (instrument_ref, valuation_date)
)
DISTKEY (instrument_ref)
SORTKEY (valuation_date);

DROP TABLE IF EXISTS lpp.counterparty_exposure CASCADE;
CREATE TABLE lpp.counterparty_exposure (
    as_of_date            DATE        NOT NULL,
    counterparty_bank_ref VARCHAR(64) NOT NULL,
    deposits_amount       NUMERIC(28,6),
    investments_amount    NUMERIC(28,6),
    derivative_mtm_amount NUMERIC(28,6),
    total_exposure        NUMERIC(28,6),
    reporting_currency    VARCHAR(16),
    pct_of_total          NUMERIC(9,4),
    CONSTRAINT pk_cp_exp PRIMARY KEY (as_of_date, counterparty_bank_ref)
)
DISTSTYLE ALL
SORTKEY (as_of_date);

-- -----------------------------------------------------------------------------
-- G. CASH CONCENTRATION & SWEEPS
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.sweep_instruction CASCADE;
CREATE TABLE lpp.sweep_instruction (
    uuid               VARCHAR(64) NOT NULL,
    code               VARCHAR(64) NOT NULL,
    source_account_ref VARCHAR(64) NOT NULL,
    target_account_ref VARCHAR(64) NOT NULL,
    sweep_type         VARCHAR(32) NOT NULL,
    direction          VARCHAR(16),
    target_balance     NUMERIC(28,6),
    target_balance_ccy VARCHAR(16),
    threshold_amount   NUMERIC(28,6),
    schedule_cron      VARCHAR(64),
    priority           SMALLINT,
    active             BOOLEAN DEFAULT TRUE,
    effective_from     DATE,
    effective_to       DATE,
    CONSTRAINT pk_sweep_instr PRIMARY KEY (uuid),
    CONSTRAINT uk_sweep_instr_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.sweep_execution CASCADE;
CREATE TABLE lpp.sweep_execution (
    uuid                   VARCHAR(64) NOT NULL,
    instruction_ref        VARCHAR(64) NOT NULL,
    execution_date         DATE NOT NULL,
    source_account_ref     VARCHAR(64) NOT NULL,
    target_account_ref     VARCHAR(64) NOT NULL,
    swept_amount           NUMERIC(28,6) NOT NULL,
    currency_code          VARCHAR(16)   NOT NULL,
    pre_sweep_balance      NUMERIC(28,6),
    post_sweep_balance     NUMERIC(28,6),
    residual_amount        NUMERIC(28,6),
    status                 VARCHAR(16),
    cash_flow_uuid         VARCHAR(64),
    counter_cash_flow_uuid VARCHAR(64),
    CONSTRAINT pk_sweep_exec PRIMARY KEY (uuid)
)
DISTKEY (instruction_ref)
SORTKEY (execution_date);

-- -----------------------------------------------------------------------------
-- H. FORECASTS, LIQUIDITY POLICY & SCENARIOS
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.forecast_snapshot CASCADE;
CREATE TABLE lpp.forecast_snapshot (
    snapshot_id        VARCHAR(64) NOT NULL,
    snapshot_date      DATE NOT NULL,
    horizon_start_date DATE NOT NULL,
    horizon_end_date   DATE NOT NULL,
    granularity        VARCHAR(16) NOT NULL,
    model_version      VARCHAR(64),
    description        VARCHAR(256),
    CONSTRAINT pk_fc_snap PRIMARY KEY (snapshot_id)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.forecast_cash_flow CASCADE;
CREATE TABLE lpp.forecast_cash_flow (
    uuid               VARCHAR(64) NOT NULL,
    snapshot_id        VARCHAR(64) NOT NULL,
    company_ref        VARCHAR(64),
    account_ref        VARCHAR(64),
    forecast_date      DATE NOT NULL,
    flow_category      VARCHAR(32) NOT NULL,
    flow_subcategory   VARCHAR(64),
    direction          VARCHAR(8)  NOT NULL,
    forecast_amount    NUMERIC(28,6) NOT NULL,
    currency_code      VARCHAR(16)   NOT NULL,
    confidence         VARCHAR(8),
    seasonality_factor NUMERIC(9,4),
    CONSTRAINT pk_fc_cf PRIMARY KEY (uuid)
)
DISTKEY (snapshot_id)
SORTKEY (forecast_date);

DROP TABLE IF EXISTS lpp.forecast_vs_actual CASCADE;
CREATE TABLE lpp.forecast_vs_actual (
    company_ref     VARCHAR(64) NOT NULL,
    period_date     DATE        NOT NULL,
    flow_category   VARCHAR(32) NOT NULL,
    forecast_amount NUMERIC(28,6),
    actual_amount   NUMERIC(28,6),
    variance_amount NUMERIC(28,6),
    variance_pct    NUMERIC(9,4),
    snapshot_id     VARCHAR(64),
    currency_code   VARCHAR(16),
    CONSTRAINT pk_fva PRIMARY KEY (company_ref, period_date, flow_category)
)
DISTKEY (company_ref)
SORTKEY (period_date);

DROP TABLE IF EXISTS lpp.liquidity_policy CASCADE;
CREATE TABLE lpp.liquidity_policy (
    uuid               VARCHAR(64) NOT NULL,
    company_ref        VARCHAR(64),
    company_group_ref  VARCHAR(64),
    policy_type        VARCHAR(32) NOT NULL,
    threshold_amount   NUMERIC(28,6),
    threshold_currency VARCHAR(16),
    threshold_pct      NUMERIC(9,4),
    effective_from     DATE,
    effective_to       DATE,
    description        VARCHAR(MAX),
    CONSTRAINT pk_liq_pol PRIMARY KEY (uuid)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.stress_scenario CASCADE;
CREATE TABLE lpp.stress_scenario (
    uuid          VARCHAR(64) NOT NULL,
    code          VARCHAR(64) NOT NULL,
    description   VARCHAR(MAX),
    scenario_type VARCHAR(32),
    parameters    SUPER,
    created_at    TIMESTAMPTZ,
    CONSTRAINT pk_stress PRIMARY KEY (uuid),
    CONSTRAINT uk_stress_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.stress_run_result CASCADE;
CREATE TABLE lpp.stress_run_result (
    uuid               VARCHAR(64) NOT NULL,
    scenario_ref       VARCHAR(64) NOT NULL,
    run_date           TIMESTAMPTZ,
    company_ref        VARCHAR(64) NOT NULL,
    breach_date        DATE,
    min_projected_cash NUMERIC(28,6),
    threshold_amount   NUMERIC(28,6),
    currency_code      VARCHAR(16),
    breach_severity    VARCHAR(16),
    CONSTRAINT pk_stress_run PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (run_date);

-- -----------------------------------------------------------------------------
-- I. BANK FEES & RATE CARDS
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.bank_service_type CASCADE;
CREATE TABLE lpp.bank_service_type (
    code        VARCHAR(64) NOT NULL,
    description VARCHAR(256),
    category    VARCHAR(64),
    CONSTRAINT pk_bst PRIMARY KEY (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.fee_rate_card CASCADE;
CREATE TABLE lpp.fee_rate_card (
    uuid            VARCHAR(64) NOT NULL,
    bank_ref        VARCHAR(64) NOT NULL,
    service_code    VARCHAR(64) NOT NULL,
    company_ref     VARCHAR(64),
    negotiated_rate NUMERIC(28,6) NOT NULL,
    rate_unit       VARCHAR(32),
    currency_code   VARCHAR(16),
    effective_from  DATE NOT NULL,
    effective_to    DATE,
    CONSTRAINT pk_frc PRIMARY KEY (uuid)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.bank_fee CASCADE;
CREATE TABLE lpp.bank_fee (
    uuid             VARCHAR(64) NOT NULL,
    bank_ref         VARCHAR(64) NOT NULL,
    bank_account_ref VARCHAR(64),
    service_code     VARCHAR(64) NOT NULL,
    statement_period DATE,
    charge_date      DATE,
    units            NUMERIC(18,4),
    charged_amount   NUMERIC(28,6) NOT NULL,
    currency_code    VARCHAR(16) NOT NULL,
    cash_flow_ref    VARCHAR(64),
    rate_card_ref    VARCHAR(64),
    expected_amount  NUMERIC(28,6),
    overage_amount   NUMERIC(28,6),
    flagged          BOOLEAN DEFAULT FALSE,
    CONSTRAINT pk_bank_fee PRIMARY KEY (uuid)
)
DISTKEY (bank_ref)
SORTKEY (charge_date);

-- -----------------------------------------------------------------------------
-- J. AR / AP INVOICES (working capital)
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.ar_invoice CASCADE;
CREATE TABLE lpp.ar_invoice (
    uuid           VARCHAR(64) NOT NULL,
    invoice_number VARCHAR(64) NOT NULL,
    company_ref    VARCHAR(64) NOT NULL,
    customer_ref   VARCHAR(64),
    issue_date     DATE NOT NULL,
    due_date       DATE,
    paid_date      DATE,
    invoice_amount NUMERIC(28,6) NOT NULL,
    paid_amount    NUMERIC(28,6),
    open_amount    NUMERIC(28,6),
    currency_code  VARCHAR(16)   NOT NULL,
    status         VARCHAR(16),
    payment_terms  VARCHAR(16),
    CONSTRAINT pk_ar_inv PRIMARY KEY (uuid),
    CONSTRAINT uk_ar_inv_number UNIQUE (company_ref, invoice_number)
)
DISTKEY (company_ref)
SORTKEY (issue_date);

DROP TABLE IF EXISTS lpp.ap_invoice CASCADE;
CREATE TABLE lpp.ap_invoice (
    uuid           VARCHAR(64) NOT NULL,
    invoice_number VARCHAR(64) NOT NULL,
    company_ref    VARCHAR(64) NOT NULL,
    vendor_ref     VARCHAR(64),
    issue_date     DATE NOT NULL,
    due_date       DATE,
    paid_date      DATE,
    invoice_amount NUMERIC(28,6) NOT NULL,
    paid_amount    NUMERIC(28,6),
    open_amount    NUMERIC(28,6),
    currency_code  VARCHAR(16)   NOT NULL,
    status         VARCHAR(16),
    payment_terms  VARCHAR(16),
    CONSTRAINT pk_ap_inv PRIMARY KEY (uuid),
    CONSTRAINT uk_ap_inv_number UNIQUE (company_ref, invoice_number)
)
DISTKEY (company_ref)
SORTKEY (issue_date);

DROP TABLE IF EXISTS lpp.working_capital_metric CASCADE;
CREATE TABLE lpp.working_capital_metric (
    company_ref   VARCHAR(64) NOT NULL,
    period_date   DATE NOT NULL,
    dso_days      NUMERIC(9,2),
    dpo_days      NUMERIC(9,2),
    dio_days      NUMERIC(9,2),
    ccc_days      NUMERIC(9,2),
    ar_balance    NUMERIC(28,6),
    ap_balance    NUMERIC(28,6),
    revenue_ttm   NUMERIC(28,6),
    cogs_ttm      NUMERIC(28,6),
    currency_code VARCHAR(16),
    CONSTRAINT pk_wcm PRIMARY KEY (company_ref, period_date)
)
DISTSTYLE ALL
SORTKEY (period_date);

-- -----------------------------------------------------------------------------
-- K. INTERCOMPANY LEG LINKAGE
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.intercompany_transaction CASCADE;
CREATE TABLE lpp.intercompany_transaction (
    uuid                 VARCHAR(64) NOT NULL,
    reference            VARCHAR(128),
    transaction_date     DATE,
    value_date           DATE,
    amount               NUMERIC(28,6) NOT NULL,
    currency_code        VARCHAR(16) NOT NULL,
    purpose              VARCHAR(32),
    source_company_ref   VARCHAR(64) NOT NULL,
    source_account_ref   VARCHAR(64) NOT NULL,
    source_cash_flow_ref VARCHAR(64),
    target_company_ref   VARCHAR(64) NOT NULL,
    target_account_ref   VARCHAR(64) NOT NULL,
    target_cash_flow_ref VARCHAR(64),
    status               VARCHAR(16),
    CONSTRAINT pk_ic_txn PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (value_date);

-- -----------------------------------------------------------------------------
-- L. TRIBAL KNOWLEDGE & SME FEEDBACK   (Brain layer)
--
-- Source of truth for tribal-knowledge prose and SME-taught entity edges.
-- Vector embeddings and ANN retrieval live in OpenSearch Serverless (see L.1),
-- not in Redshift. Each row that should be retrievable carries an
-- opensearch_doc_id pointer for traceability.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS lpp.tribal_knowledge_fact CASCADE;
CREATE TABLE lpp.tribal_knowledge_fact (
    uuid               VARCHAR(64)  NOT NULL,
    fact_type          VARCHAR(32)  NOT NULL,
    title              VARCHAR(512) NOT NULL,
    narrative          VARCHAR(MAX) NOT NULL,
    effective_from     DATE,
    effective_to       DATE,
    trigger_condition  VARCHAR(MAX),
    captured_by_user   VARCHAR(64)  NOT NULL,
    captured_at        TIMESTAMPTZ  NOT NULL,
    source_session     VARCHAR(64),
    confidence         VARCHAR(8),
    status             VARCHAR(16),
    superseded_by      VARCHAR(64),
    opensearch_doc_id  VARCHAR(64),       -- pointer into TRIBAL_FACT index (see L.1)
    embedding_model    VARCHAR(64),
    embedded_at        TIMESTAMPTZ,
    CONSTRAINT pk_tkf PRIMARY KEY (uuid)
    -- CHECK constraints not supported on this Redshift version
    -- CONSTRAINT ck_tkf_fact_type CHECK (...),
    -- CONSTRAINT ck_tkf_status CHECK (...)
)
DISTSTYLE ALL
SORTKEY (status, effective_to);

DROP TABLE IF EXISTS lpp.tribal_knowledge_entity_link CASCADE;
CREATE TABLE lpp.tribal_knowledge_entity_link (
    fact_uuid   VARCHAR(64) NOT NULL,
    entity_type VARCHAR(32) NOT NULL,
    entity_code VARCHAR(64) NOT NULL,
    role        VARCHAR(32),
    CONSTRAINT pk_tkel PRIMARY KEY (fact_uuid, entity_type, entity_code, role)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.kg_relationship CASCADE;
CREATE TABLE lpp.kg_relationship (
    uuid              VARCHAR(64) NOT NULL,
    relationship_type VARCHAR(64) NOT NULL,
    from_entity_type  VARCHAR(32) NOT NULL,
    from_entity_code  VARCHAR(64) NOT NULL,
    to_entity_type    VARCHAR(32) NOT NULL,
    to_entity_code    VARCHAR(64) NOT NULL,
    properties        SUPER,
    captured_by_user  VARCHAR(64),
    captured_at       TIMESTAMPTZ,
    source_session    VARCHAR(64),
    confidence        VARCHAR(8),
    status            VARCHAR(16),
    CONSTRAINT pk_kg_rel PRIMARY KEY (uuid)
)
DISTSTYLE ALL
SORTKEY (relationship_type);

DROP TABLE IF EXISTS lpp.sme_feedback_session CASCADE;
CREATE TABLE lpp.sme_feedback_session (
    uuid             VARCHAR(64) NOT NULL,
    sme_user         VARCHAR(64) NOT NULL,
    interviewer_user VARCHAR(64),
    started_at       TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    topic            VARCHAR(256),
    transcript_uri   VARCHAR(1024),    -- S3 URI
    CONSTRAINT pk_sme_sess PRIMARY KEY (uuid)
)
DISTSTYLE ALL
SORTKEY (started_at);

DROP TABLE IF EXISTS lpp.brain_evaluation CASCADE;
CREATE TABLE lpp.brain_evaluation (
    uuid              VARCHAR(64) NOT NULL,
    user_code         VARCHAR(64),
    query_text        VARCHAR(MAX),
    evaluated_at      TIMESTAMPTZ,
    facts_applied     SUPER,             -- array of TRIBAL_KNOWLEDGE_FACT.uuid
    edges_traversed   SUPER,             -- array of KG_RELATIONSHIP.uuid
    flagged_conflicts SUPER,
    answer_summary    VARCHAR(MAX),
    seed_facts        SUPER,             -- top-k from OpenSearch
    retrieval_scores  SUPER,
    expansion_hops    SMALLINT,
    CONSTRAINT pk_brain_eval PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (evaluated_at);

DROP TABLE IF EXISTS lpp.sme_transcript_chunk CASCADE;
CREATE TABLE lpp.sme_transcript_chunk (
    uuid              VARCHAR(64) NOT NULL,
    session_uuid      VARCHAR(64) NOT NULL,
    chunk_index       INTEGER     NOT NULL,
    text              VARCHAR(MAX) NOT NULL,
    opensearch_doc_id VARCHAR(64),
    embedding_model   VARCHAR(64),
    embedded_at       TIMESTAMPTZ,
    CONSTRAINT pk_smtc PRIMARY KEY (uuid),
    CONSTRAINT uk_smtc_chunk UNIQUE (session_uuid, chunk_index)
)
DISTKEY (session_uuid)
SORTKEY (session_uuid, chunk_index);

-- -----------------------------------------------------------------------------
-- L.1  GRAPH-RAG SUPPORT — Amazon OpenSearch Serverless integration
--
-- Vector embeddings, ANN search, and lexical-hybrid retrieval are not native to
-- Redshift. The AWS-native pattern keeps prose-of-record in Redshift and
-- delegates retrieval to OpenSearch Serverless:
--
--   Redshift  ──Zero-ETL/Lambda──▶  Bedrock Titan embeddings  ──▶  OpenSearch
--                                                                  Serverless
--                                                                  (vector idx)
--
-- For full index spec see opensearch-brain-index.json. The Brain Retrieval
-- agent calls OpenSearch directly; only opensearch_doc_id round-trips back to
-- Redshift for join-on-write and explainability.
--
-- A materialized view below exposes the "indexable surface" — the projection
-- that the embedding Lambda reads when refreshing the OpenSearch index.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW lpp.v_tribal_fact_indexable AS
SELECT
    f.uuid                                                            AS fact_uuid,
    f.fact_type,
    f.status,
    f.effective_from,
    f.effective_to,
    f.confidence,
    f.captured_at,
    COALESCE(f.title, '')                                            AS title,
    COALESCE(f.narrative, '')                                        AS narrative,
    COALESCE(f.trigger_condition, '')                                AS trigger_condition,
    -- Concatenated text the embedding model reads
    COALESCE(f.title, '') || CHR(10) ||
    COALESCE(f.narrative, '') || CHR(10) ||
    COALESCE(f.trigger_condition, '')                                AS embed_text,
    -- Linked entities, as a structured array for OpenSearch filtering
    (SELECT JSON_PARSE(
        '[' || LISTAGG(
            '{"entity_type":"' || l.entity_type || '","entity_code":"' ||
            l.entity_code || '","role":"' || COALESCE(l.role,'') || '"}',
            ','
        ) || ']')
     FROM lpp.tribal_knowledge_entity_link l
     WHERE l.fact_uuid = f.uuid)                                     AS linked_entities
FROM lpp.tribal_knowledge_fact f
WHERE f.status IN ('ACTIVE','SUPERSEDED');

CREATE OR REPLACE VIEW lpp.v_sme_transcript_chunk_indexable AS
SELECT
    c.uuid               AS chunk_uuid,
    c.session_uuid,
    c.chunk_index,
    c.text               AS embed_text,
    s.topic,
    s.sme_user,
    s.started_at
FROM lpp.sme_transcript_chunk c
JOIN lpp.sme_feedback_session s ON s.uuid = c.session_uuid;
