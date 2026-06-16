-- =============================================================================
-- LPP data model — EXTENSIONS #2 (Amazon Redshift)
--
-- Adds the data surfaces required to answer the full Prompts_Retail_v_0.1 set
-- (209 prompts across 8 persona × domain sheets). The base extension covered
-- treasury cleanly; this file fills the gaps surfaced by the payments and
-- senior-finance prompts.
--
-- Sections:
--   M. CARD ACQUIRING & PAYMENTS OPERATIONS  (Analyst-P/Manager-P/Director-P/Executive-P)
--   N. CORPORATE FINANCIAL METRICS           (Director-F/Executive-F)
--   O. LETTERS OF CREDIT                     (Manager-F)
--   P. PENSION & OCI                         (Director-F/Executive-F)
--   Q. PEER BENCHMARKS                       (Director-F/Executive-F/Executive-P)
--   R. SIGNATORY NORMALIZATION               (Manager-F)
--
-- Conventions follow lpp-schema-redshift.sql:
--   STRING       → VARCHAR(256)  (or VARCHAR(MAX) for narrative/memo)
--   VARIANT      → SUPER
--   TIMESTAMP_TZ → TIMESTAMPTZ
--   NUMBER(p,s)  → NUMERIC(p,s)
--   CREATE OR REPLACE → DROP TABLE IF EXISTS … CASCADE; CREATE TABLE …
-- =============================================================================

SET search_path TO lpp;

-- #############################################################################
-- M. CARD ACQUIRING & PAYMENTS OPERATIONS
--
-- The base schema models bulk-payment FILE → TRANSFER → PAYMENT_TRANSACTION
-- (treasury / supplier payments). The payments prompts assume the
-- card-acquiring stack — acquirers, networks, authorizations, settlements,
-- chargebacks, fee breakdowns, rebates, exceptions, cross-border corridors.
-- This section adds that stack.
-- #############################################################################

-- M.1 Acquirers & networks ----------------------------------------------------

DROP TABLE IF EXISTS lpp.acquirer CASCADE;
CREATE TABLE lpp.acquirer (
    uuid              VARCHAR(64) NOT NULL,
    code              VARCHAR(32) NOT NULL,         -- e.g. CHASE_PAYTECH | FIS | WORLDPAY | ADYEN | STRIPE
    name              VARCHAR(256) NOT NULL,
    bank_ref          VARCHAR(64),                  -- FK → BANK.code (acquiring bank, when affiliated)
    settlement_account_ref VARCHAR(64),             -- FK → BANK_ACCOUNT.code (default settlement target)
    region            VARCHAR(16),                  -- AMER | EMEA | APAC | LATAM | MENA
    is_strategic      BOOLEAN DEFAULT FALSE,
    onboarded_date    DATE,
    CONSTRAINT pk_acquirer PRIMARY KEY (uuid),
    CONSTRAINT uk_acquirer_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.acquirer_contract CASCADE;
CREATE TABLE lpp.acquirer_contract (
    uuid                       VARCHAR(64) NOT NULL,
    acquirer_ref               VARCHAR(32) NOT NULL,   -- FK → ACQUIRER.code
    company_ref                VARCHAR(64),            -- nullable → applies to all entities
    effective_from             DATE NOT NULL,
    effective_to               DATE,
    contract_status            VARCHAR(16),            -- ACTIVE | PENDING | EXPIRED | TERMINATED
    -- Economic terms
    processor_margin_bps       NUMERIC(9,2),
    monthly_minimum_amount     NUMERIC(28,6),
    monthly_minimum_currency   VARCHAR(16),
    settlement_lag_business_days SMALLINT,             -- contracted T+n
    -- Service terms / SLA
    uptime_sla_pct             NUMERIC(6,3),
    auth_response_sla_ms       INTEGER,
    settlement_sla_business_days SMALLINT,
    -- Renewal
    renewal_notice_days        SMALLINT,
    auto_renew                 BOOLEAN DEFAULT FALSE,
    CONSTRAINT pk_acq_contract PRIMARY KEY (uuid)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.card_network CASCADE;
CREATE TABLE lpp.card_network (
    code        VARCHAR(16) NOT NULL,    -- VISA | MASTERCARD | AMEX | DISCOVER | COSTCO_PRIVATE | UNIONPAY | JCB | INTERAC | STAR | NYCE | PULSE
    name        VARCHAR(64),
    network_type VARCHAR(16),            -- CREDIT | DEBIT_SIGN | DEBIT_PIN | CHARGE | PRIVATE_LABEL
    CONSTRAINT pk_card_network PRIMARY KEY (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.card_bin_range CASCADE;
CREATE TABLE lpp.card_bin_range (
    uuid           VARCHAR(64) NOT NULL,
    bin_low        VARCHAR(8)  NOT NULL,
    bin_high       VARCHAR(8)  NOT NULL,
    network_code   VARCHAR(16) NOT NULL,    -- FK → CARD_NETWORK.code
    issuer_name    VARCHAR(256),
    issuer_country VARCHAR(2),
    card_product   VARCHAR(32),             -- CONSUMER_CREDIT | COMMERCIAL_CREDIT | DEBIT | PREPAID | PURCHASING
    CONSTRAINT pk_bin PRIMARY KEY (uuid)
)
DISTSTYLE ALL
SORTKEY (bin_low);

-- M.2 POS / e-commerce gross sale + card authorization -----------------------

DROP TABLE IF EXISTS lpp.pos_transaction CASCADE;
CREATE TABLE lpp.pos_transaction (
    uuid           VARCHAR(64) NOT NULL,
    company_ref    VARCHAR(64) NOT NULL,           -- selling entity
    channel        VARCHAR(32) NOT NULL,           -- IN_WAREHOUSE | ECOMMERCE | MEMBERSHIP_RENEWAL | MOBILE_APP | KIOSK
    location_code  VARCHAR(64),                    -- warehouse / store code
    transaction_ts TIMESTAMPTZ NOT NULL,
    amount         NUMERIC(28,6) NOT NULL,
    currency_code  VARCHAR(16)   NOT NULL,
    payment_method VARCHAR(16),                    -- CARD | CASH | CHECK | MOBILE_WALLET | GIFT_CARD | EBT
    member_id      VARCHAR(64),                    -- Costco-style membership id (nullable)
    register_id    VARCHAR(32),
    CONSTRAINT pk_pos_txn PRIMARY KEY (uuid)
)
DISTKEY (company_ref)
SORTKEY (transaction_ts);

DROP TABLE IF EXISTS lpp.card_authorization CASCADE;
CREATE TABLE lpp.card_authorization (
    uuid                VARCHAR(64) NOT NULL,
    pos_transaction_ref VARCHAR(64),               -- FK → POS_TRANSACTION.uuid (nullable when card-not-present without POS)
    acquirer_ref        VARCHAR(32) NOT NULL,      -- FK → ACQUIRER.code
    network_code        VARCHAR(16) NOT NULL,      -- FK → CARD_NETWORK.code
    bin_first6          VARCHAR(8),                -- issuer BIN for routing/analytics
    auth_request_ts     TIMESTAMPTZ NOT NULL,
    auth_response_ts    TIMESTAMPTZ,
    response_time_ms    INTEGER,
    decision            VARCHAR(16) NOT NULL,      -- APPROVED | DECLINED | TIMEOUT | ERROR
    decline_reason_code VARCHAR(16),               -- 51 (insufficient funds) | 05 (do not honor) | 14 (invalid card) | ...
    amount              NUMERIC(28,6) NOT NULL,
    currency_code       VARCHAR(16)   NOT NULL,
    transaction_size_band VARCHAR(16),             -- LT_25 | 25_100 | 100_500 | 500_5000 | GT_5000
    region              VARCHAR(16),
    auth_3ds_status     VARCHAR(16),               -- NOT_ATTEMPTED | FRICTIONLESS | CHALLENGED | FAILED | EXEMPT
    device_fingerprint  VARCHAR(128),
    cnp_indicator       BOOLEAN,                   -- card-not-present?
    CONSTRAINT pk_card_auth PRIMARY KEY (uuid)
)
DISTKEY (acquirer_ref)
SORTKEY (auth_request_ts);

-- M.3 Settlement (batch → processor → bank deposit) + fee breakdown ----------

DROP TABLE IF EXISTS lpp.card_settlement_batch CASCADE;
CREATE TABLE lpp.card_settlement_batch (
    uuid                    VARCHAR(64) NOT NULL,
    acquirer_ref            VARCHAR(32) NOT NULL,
    network_code            VARCHAR(16) NOT NULL,
    settlement_currency     VARCHAR(16) NOT NULL,
    batch_close_ts          TIMESTAMPTZ NOT NULL,
    processor_settle_ts     TIMESTAMPTZ,
    bank_deposit_ts         TIMESTAMPTZ,
    bank_account_ref        VARCHAR(64),                -- where the net settled
    gross_sales_amount      NUMERIC(28,6),
    refund_amount           NUMERIC(28,6),
    chargeback_amount       NUMERIC(28,6),
    interchange_amount      NUMERIC(28,6),
    network_assessment_amount NUMERIC(28,6),
    processor_margin_amount NUMERIC(28,6),
    other_fees_amount       NUMERIC(28,6),
    net_settlement_amount   NUMERIC(28,6),
    transaction_count       INTEGER,
    sla_met                 BOOLEAN,                    -- batch_close→bank_deposit within contracted SLA?
    CONSTRAINT pk_settle_batch PRIMARY KEY (uuid)
)
DISTKEY (acquirer_ref)
SORTKEY (batch_close_ts);

DROP TABLE IF EXISTS lpp.card_settlement_line CASCADE;
CREATE TABLE lpp.card_settlement_line (
    uuid                 VARCHAR(64) NOT NULL,
    batch_ref            VARCHAR(64) NOT NULL,          -- FK → CARD_SETTLEMENT_BATCH.uuid
    authorization_ref    VARCHAR(64),                   -- FK → CARD_AUTHORIZATION.uuid
    gross_amount         NUMERIC(28,6) NOT NULL,
    -- Fee decomposition (per-transaction)
    interchange_amount   NUMERIC(28,6),
    interchange_bps      NUMERIC(9,2),
    network_assessment_amount NUMERIC(28,6),
    processor_margin_amount   NUMERIC(28,6),
    other_fees_amount    NUMERIC(28,6),
    net_amount           NUMERIC(28,6),
    -- For cross-border / DCC analysis
    issuer_country       VARCHAR(2),
    cross_border         BOOLEAN DEFAULT FALSE,
    dcc_applied          BOOLEAN DEFAULT FALSE,
    fx_rate_applied      NUMERIC(20,10),
    CONSTRAINT pk_settle_line PRIMARY KEY (uuid)
)
DISTKEY (batch_ref);

-- M.4 Chargebacks / disputes / representments --------------------------------

DROP TABLE IF EXISTS lpp.chargeback CASCADE;
CREATE TABLE lpp.chargeback (
    uuid                  VARCHAR(64) NOT NULL,
    authorization_ref     VARCHAR(64),                -- FK → CARD_AUTHORIZATION.uuid
    acquirer_ref          VARCHAR(32) NOT NULL,
    network_code          VARCHAR(16) NOT NULL,
    company_ref           VARCHAR(64),                -- selling entity
    location_code         VARCHAR(64),                -- warehouse if applicable
    reason_code           VARCHAR(16) NOT NULL,       -- network reason code (e.g. 10.4, 13.1, 4855)
    reason_category       VARCHAR(32),                -- FRAUD | NON_RECEIPT | NOT_AS_DESCRIBED | DUPLICATE | TECHNICAL | AUTHORIZATION
    initiated_date        DATE NOT NULL,
    resolved_date         DATE,
    amount                NUMERIC(28,6) NOT NULL,
    currency_code         VARCHAR(16)   NOT NULL,
    status                VARCHAR(16),                -- INITIATED | REPRESENTED | WON | LOST | WITHDRAWN
    representment_attempted BOOLEAN DEFAULT FALSE,
    representment_evidence_uri VARCHAR(1024),
    classification        VARCHAR(16),                -- TRUE_FRAUD | FRIENDLY_FRAUD | MERCHANT_ERROR | UNKNOWN
    CONSTRAINT pk_chargeback PRIMARY KEY (uuid)
)
DISTKEY (acquirer_ref)
SORTKEY (initiated_date);

-- M.5 ACH return data --------------------------------------------------------

DROP TABLE IF EXISTS lpp.ach_return CASCADE;
CREATE TABLE lpp.ach_return (
    uuid               VARCHAR(64) NOT NULL,
    original_transfer_ref VARCHAR(64),                -- FK → TRANSFER.uuid
    company_ref        VARCHAR(64),                   -- originating BU
    business_unit_code VARCHAR(32),
    return_date        DATE NOT NULL,
    return_reason_code VARCHAR(8) NOT NULL,           -- R01..R99 (NACHA)
    return_category    VARCHAR(32),                   -- INSUFFICIENT_FUNDS | INVALID_ACCT | UNAUTHORIZED | STOP_PAY | OTHER
    direction          VARCHAR(8),                    -- DEBIT | CREDIT
    amount             NUMERIC(28,6) NOT NULL,
    currency_code      VARCHAR(16)   NOT NULL,
    bank_account_ref   VARCHAR(64),
    resolved           BOOLEAN DEFAULT FALSE,
    CONSTRAINT pk_ach_return PRIMARY KEY (uuid)
)
DISTKEY (company_ref)
SORTKEY (return_date);

-- M.6 Virtual/commercial card rebates ----------------------------------------

DROP TABLE IF EXISTS lpp.card_rebate_program CASCADE;
CREATE TABLE lpp.card_rebate_program (
    uuid                  VARCHAR(64) NOT NULL,
    code                  VARCHAR(64) NOT NULL,
    issuer_bank_ref       VARCHAR(64) NOT NULL,       -- FK → BANK.code
    company_ref           VARCHAR(64),
    program_type          VARCHAR(16),                -- VIRTUAL_CARD | COMMERCIAL_CARD | PURCHASING_CARD
    rebate_tier_threshold NUMERIC(28,6),              -- annual spend threshold for full rebate
    rebate_bps_at_target  NUMERIC(9,2),               -- contracted rebate at threshold
    effective_from        DATE,
    effective_to          DATE,
    CONSTRAINT pk_card_rebate_prog PRIMARY KEY (uuid),
    CONSTRAINT uk_card_rebate_prog_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.card_rebate_earning CASCADE;
CREATE TABLE lpp.card_rebate_earning (
    uuid               VARCHAR(64) NOT NULL,
    program_ref        VARCHAR(64) NOT NULL,         -- FK → CARD_REBATE_PROGRAM.code
    company_ref        VARCHAR(64),
    period_date        DATE NOT NULL,                -- typically month-end / quarter-end
    spend_category     VARCHAR(32),                  -- INVENTORY | TRAVEL | UTILITIES | LOGISTICS | OTHER
    eligible_spend     NUMERIC(28,6) NOT NULL,
    rebate_earned      NUMERIC(28,6) NOT NULL,
    currency_code      VARCHAR(16)   NOT NULL,
    tier_achieved      VARCHAR(16),                  -- BASE | TIER1 | TIER2 | TIER3
    CONSTRAINT pk_card_rebate_earn PRIMARY KEY (uuid)
)
DISTKEY (program_ref)
SORTKEY (period_date);

-- M.7 Membership fee collections (retailer-specific revenue stream) ----------

DROP TABLE IF EXISTS lpp.membership_fee CASCADE;
CREATE TABLE lpp.membership_fee (
    uuid           VARCHAR(64) NOT NULL,
    member_id      VARCHAR(64) NOT NULL,
    company_ref    VARCHAR(64) NOT NULL,
    channel        VARCHAR(16),                       -- IN_WAREHOUSE | ECOMMERCE | AUTORENEW | MAIL_RENEWAL | NEW_SIGNUP
    membership_tier VARCHAR(32),                      -- GOLD | EXECUTIVE | BUSINESS | …
    issued_date    DATE NOT NULL,
    expiration_date DATE,
    fee_amount     NUMERIC(28,6) NOT NULL,
    currency_code  VARCHAR(16)   NOT NULL,
    payment_method VARCHAR(16),
    status         VARCHAR(16),                       -- COLLECTED | FAILED | REFUNDED | CHARGEBACK
    fraud_loss     NUMERIC(28,6) DEFAULT 0,
    CONSTRAINT pk_membership_fee PRIMARY KEY (uuid)
)
DISTKEY (company_ref)
SORTKEY (issued_date);

-- M.8 Payment-hub exception / repair workflow --------------------------------

DROP TABLE IF EXISTS lpp.payment_exception CASCADE;
CREATE TABLE lpp.payment_exception (
    uuid              VARCHAR(64) NOT NULL,
    file_uuid         VARCHAR(64),                    -- FK → PAYMENT_FILE.file_uuid
    transfer_uuid     VARCHAR(64),                    -- FK → TRANSFER.uuid
    exception_type    VARCHAR(32) NOT NULL,           -- BANK_VALIDATION | FILE_FORMAT | BENEFICIARY_DATA | OFAC_HOLD | DUPLICATE | OTHER
    detected_at       TIMESTAMPTZ,
    resolved_at       TIMESTAMPTZ,
    resolution_time_minutes INTEGER,                  -- materialized for trend queries
    resolved_by_user  VARCHAR(64),
    resolution_action VARCHAR(64),
    repair_touch_count SMALLINT,
    repair_cost_amount NUMERIC(28,6),                 -- modeled cost per touch
    status            VARCHAR(16),                    -- OPEN | RESOLVED | ESCALATED
    CONSTRAINT pk_payment_exception PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (detected_at);

-- M.9 Cross-border payment corridor detail -----------------------------------

DROP TABLE IF EXISTS lpp.cross_border_payment_leg CASCADE;
CREATE TABLE lpp.cross_border_payment_leg (
    uuid                VARCHAR(64) NOT NULL,
    transfer_uuid       VARCHAR(64),                  -- FK → TRANSFER.uuid
    payment_transaction_uuid VARCHAR(64),             -- FK → PAYMENT_TRANSACTION.uuid
    origination_country VARCHAR(2)  NOT NULL,
    destination_country VARCHAR(2)  NOT NULL,
    send_currency       VARCHAR(16) NOT NULL,
    receive_currency    VARCHAR(16) NOT NULL,
    corridor            VARCHAR(16),                  -- e.g. US-CA | US-EU | US-MX | EU-APAC (denormalized for grouping)
    send_amount         NUMERIC(28,6) NOT NULL,
    receive_amount      NUMERIC(28,6),
    fx_rate_applied     NUMERIC(20,10),
    fx_spread_bps       NUMERIC(9,2),                 -- spread vs interbank mid
    lifting_fees        NUMERIC(28,6),
    correspondent_fees  NUMERIC(28,6),
    payment_method      VARCHAR(16),                  -- WIRE | SEPA | RTP | ACH | LOCAL_RAIL
    initiated_at        TIMESTAMPTZ,
    delivered_at        TIMESTAMPTZ,
    CONSTRAINT pk_cross_border_leg PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (initiated_at);

-- M.10 Fraud loss attribution (extends FRAUD_DETECTION_EVENT) ----------------

DROP TABLE IF EXISTS lpp.fraud_loss CASCADE;
CREATE TABLE lpp.fraud_loss (
    uuid                  VARCHAR(64) NOT NULL,
    detection_event_uuid  VARCHAR(64),                -- FK → FRAUD_DETECTION_EVENT.uuid
    authorization_ref     VARCHAR(64),                -- FK → CARD_AUTHORIZATION.uuid
    company_ref           VARCHAR(64),
    channel               VARCHAR(32),                -- IN_WAREHOUSE | ECOMMERCE | MEMBERSHIP_RENEWAL | VENDOR_PAYMENT
    loss_date             DATE NOT NULL,
    loss_amount           NUMERIC(28,6) NOT NULL,
    currency_code         VARCHAR(16)   NOT NULL,
    loss_category         VARCHAR(32),                -- TRUE_FRAUD | FRIENDLY_FRAUD | MERCHANT_ERROR | OPERATIONAL_LOSS | WRITE_OFF
    fraud_vector          VARCHAR(32),                -- BEC | CARD_NOT_PRESENT | ACCOUNT_TAKEOVER | SOCIAL_ENGINEERING | INSIDER
    issuer_country        VARCHAR(2),
    auth_method           VARCHAR(32),                -- 3DS_FRICTIONLESS | 3DS_CHALLENGED | NO_3DS | OTP | NONE
    recovered_amount      NUMERIC(28,6) DEFAULT 0,
    CONSTRAINT pk_fraud_loss PRIMARY KEY (uuid)
)
DISTKEY (company_ref)
SORTKEY (loss_date);

-- M.11 Acquirer SLA / uptime metrics -----------------------------------------

DROP TABLE IF EXISTS lpp.acquirer_sla_metric CASCADE;
CREATE TABLE lpp.acquirer_sla_metric (
    uuid                 VARCHAR(64) NOT NULL,
    acquirer_ref         VARCHAR(32) NOT NULL,
    measurement_date     DATE NOT NULL,
    auth_rate_pct        NUMERIC(6,3),                -- approval rate over auths
    avg_response_time_ms INTEGER,
    settlement_on_time_pct NUMERIC(6,3),
    uptime_pct           NUMERIC(6,3),
    sla_breach_count     SMALLINT DEFAULT 0,
    incident_count       SMALLINT DEFAULT 0,
    CONSTRAINT pk_acq_sla PRIMARY KEY (acquirer_ref, measurement_date)
)
DISTKEY (acquirer_ref)
SORTKEY (measurement_date);

-- M.12 Payment-hub straight-through-processing scorecard ---------------------

DROP TABLE IF EXISTS lpp.payment_hub_throughput CASCADE;
CREATE TABLE lpp.payment_hub_throughput (
    uuid              VARCHAR(64) NOT NULL,
    metric_date       DATE NOT NULL,
    payment_rail      VARCHAR(16) NOT NULL,           -- WIRE | ACH | SEPA_CT | SEPA_DD | RTP | FEDNOW | BOOK | CHECK | VIRTUAL_CARD
    country           VARCHAR(2),
    originating_system VARCHAR(32),
    volume_count      INTEGER,
    value_amount      NUMERIC(28,6),
    success_count     INTEGER,
    rejection_count   INTEGER,
    repair_count      INTEGER,
    stp_rate_pct      NUMERIC(6,3),
    CONSTRAINT pk_phub_throughput PRIMARY KEY (uuid)
)
DISTSTYLE AUTO
SORTKEY (metric_date);

-- #############################################################################
-- N. CORPORATE FINANCIAL METRICS  (P&L / balance sheet roll-ups)
--
-- Director/Executive treasury prompts ask for debt-to-EBITDA, WACC, free cash
-- flow, EPS, leverage, peer comparisons. These come from FP&A or consolidation
-- systems, not from raw GL. Stored here as period-level snapshots.
-- #############################################################################

DROP TABLE IF EXISTS lpp.company_financial_metric CASCADE;
CREATE TABLE lpp.company_financial_metric (
    company_ref           VARCHAR(64) NOT NULL,
    period_date           DATE NOT NULL,              -- typically quarter-end
    period_type           VARCHAR(8)  NOT NULL,       -- Q | TTM | FY | YTD
    reporting_currency    VARCHAR(16) NOT NULL,
    -- P&L
    revenue               NUMERIC(28,6),
    cogs                  NUMERIC(28,6),
    ebitda                NUMERIC(28,6),
    operating_income      NUMERIC(28,6),
    interest_income       NUMERIC(28,6),
    interest_expense      NUMERIC(28,6),
    net_income            NUMERIC(28,6),
    diluted_eps           NUMERIC(12,4),
    -- Cash flow
    cash_from_operations  NUMERIC(28,6),
    capex                 NUMERIC(28,6),
    free_cash_flow        NUMERIC(28,6),
    -- Balance sheet / debt
    total_debt            NUMERIC(28,6),
    short_term_debt       NUMERIC(28,6),
    long_term_debt        NUMERIC(28,6),
    total_equity          NUMERIC(28,6),
    cash_and_equivalents  NUMERIC(28,6),
    short_term_investments NUMERIC(28,6),
    net_debt              NUMERIC(28,6),
    -- Ratios (materialized for ease of querying)
    debt_to_ebitda        NUMERIC(9,4),
    leverage_ratio        NUMERIC(9,4),
    interest_coverage     NUMERIC(9,4),
    wacc_pct              NUMERIC(9,4),
    weighted_avg_cost_of_debt_pct NUMERIC(9,4),
    return_on_capital_pct NUMERIC(9,4),
    -- Equity / shares
    diluted_shares_outstanding NUMERIC(28,2),
    -- Guidance
    fy_ebitda_guidance_low  NUMERIC(28,6),
    fy_ebitda_guidance_high NUMERIC(28,6),
    fy_fcf_target           NUMERIC(28,6),
    CONSTRAINT pk_co_finmetric PRIMARY KEY (company_ref, period_date, period_type)
)
DISTSTYLE ALL
SORTKEY (period_date);

DROP TABLE IF EXISTS lpp.credit_rating CASCADE;
CREATE TABLE lpp.credit_rating (
    uuid           VARCHAR(64) NOT NULL,
    company_ref    VARCHAR(64) NOT NULL,
    agency         VARCHAR(16) NOT NULL,             -- MOODYS | SP | FITCH | DBRS
    rating_grade   VARCHAR(16) NOT NULL,             -- e.g. A2 / A / AA-
    outlook        VARCHAR(16),                      -- POSITIVE | STABLE | NEGATIVE | DEVELOPING
    rating_action  VARCHAR(16),                      -- AFFIRMED | UPGRADED | DOWNGRADED | INITIATED | WITHDRAWN
    as_of_date     DATE NOT NULL,
    is_current     BOOLEAN DEFAULT TRUE,             -- false once superseded
    CONSTRAINT pk_credit_rating PRIMARY KEY (uuid)
)
DISTSTYLE ALL
SORTKEY (as_of_date);

DROP TABLE IF EXISTS lpp.equity_action CASCADE;
CREATE TABLE lpp.equity_action (
    uuid                VARCHAR(64) NOT NULL,
    company_ref         VARCHAR(64) NOT NULL,
    action_type         VARCHAR(16) NOT NULL,        -- BUYBACK | DIVIDEND | STOCK_ISSUE | STOCK_SPLIT | SPECIAL_DIVIDEND
    action_date         DATE NOT NULL,
    settle_date         DATE,
    shares              NUMERIC(28,2),
    price_per_share     NUMERIC(12,4),
    total_amount        NUMERIC(28,6) NOT NULL,
    currency_code       VARCHAR(16)   NOT NULL,
    dividend_per_share  NUMERIC(12,4),
    program_name        VARCHAR(128),
    authorization_remaining NUMERIC(28,6),           -- for buyback programs
    CONSTRAINT pk_equity_action PRIMARY KEY (uuid)
)
DISTSTYLE ALL
SORTKEY (action_date);

DROP TABLE IF EXISTS lpp.capital_allocation_actual CASCADE;
CREATE TABLE lpp.capital_allocation_actual (
    uuid           VARCHAR(64) NOT NULL,
    company_ref    VARCHAR(64) NOT NULL,
    period_date    DATE NOT NULL,                    -- quarter-end
    bucket         VARCHAR(16) NOT NULL,             -- CAPEX | DIVIDENDS | BUYBACKS | M_AND_A | DEBT_PAYDOWN | RND
    amount         NUMERIC(28,6) NOT NULL,
    currency_code  VARCHAR(16)   NOT NULL,
    framework_target_pct NUMERIC(9,4),               -- target share of FCF per stated framework
    CONSTRAINT pk_cap_alloc PRIMARY KEY (company_ref, period_date, bucket)
)
DISTSTYLE ALL
SORTKEY (period_date);

-- #############################################################################
-- O. LETTERS OF CREDIT
-- #############################################################################

DROP TABLE IF EXISTS lpp.letter_of_credit CASCADE;
CREATE TABLE lpp.letter_of_credit (
    uuid             VARCHAR(64) NOT NULL,
    lc_number        VARCHAR(64) NOT NULL,
    issuing_bank_ref VARCHAR(64) NOT NULL,           -- FK → BANK.code
    applicant_company_ref VARCHAR(64) NOT NULL,
    beneficiary_name VARCHAR(256),
    beneficiary_country VARCHAR(2),
    lc_type          VARCHAR(16),                    -- STANDBY | COMMERCIAL | DOCUMENTARY | TRANSFERABLE
    purpose          VARCHAR(64),
    issue_date       DATE NOT NULL,
    expiration_date  DATE,
    face_amount      NUMERIC(28,6) NOT NULL,
    drawn_amount     NUMERIC(28,6) DEFAULT 0,
    currency_code    VARCHAR(16)   NOT NULL,
    status           VARCHAR(16),                    -- OPEN | DRAWN | EXPIRED | CANCELLED
    credit_facility_ref VARCHAR(64),                 -- FK → CREDIT_FACILITY.code (if LC consumes facility capacity)
    fee_bps          NUMERIC(9,2),
    CONSTRAINT pk_lc PRIMARY KEY (uuid),
    CONSTRAINT uk_lc_number UNIQUE (lc_number)
)
DISTSTYLE ALL
SORTKEY (expiration_date);

-- #############################################################################
-- P. PENSION & OCI
-- #############################################################################

DROP TABLE IF EXISTS lpp.pension_plan CASCADE;
CREATE TABLE lpp.pension_plan (
    uuid          VARCHAR(64) NOT NULL,
    code          VARCHAR(64) NOT NULL,
    company_ref   VARCHAR(64) NOT NULL,
    plan_name     VARCHAR(256),
    plan_type     VARCHAR(16),                       -- DB | DC | HYBRID
    country       VARCHAR(2),
    open_to_new_participants BOOLEAN,
    CONSTRAINT pk_pension_plan PRIMARY KEY (uuid),
    CONSTRAINT uk_pension_plan_code UNIQUE (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.pension_valuation CASCADE;
CREATE TABLE lpp.pension_valuation (
    uuid                          VARCHAR(64) NOT NULL,
    plan_ref                      VARCHAR(64) NOT NULL,    -- FK → PENSION_PLAN.code
    as_of_date                    DATE NOT NULL,
    projected_benefit_obligation  NUMERIC(28,6),
    plan_assets_fair_value        NUMERIC(28,6),
    funded_status                 NUMERIC(28,6),           -- assets − PBO
    funded_status_pct             NUMERIC(9,4),
    discount_rate_pct             NUMERIC(9,4),
    expected_return_on_assets_pct NUMERIC(9,4),
    projected_contribution_y1     NUMERIC(28,6),
    projected_contribution_y2     NUMERIC(28,6),
    projected_contribution_y3     NUMERIC(28,6),
    oci_impact                    NUMERIC(28,6),
    currency_code                 VARCHAR(16),
    CONSTRAINT pk_pension_val PRIMARY KEY (plan_ref, as_of_date)
)
DISTSTYLE ALL
SORTKEY (as_of_date);

-- #############################################################################
-- Q. PEER BENCHMARKS  (external market data)
--
-- Sourced from S&P / Bloomberg / Refinitiv feeds in real deployments. Schema
-- here so Director/Executive peer-comparison prompts have a target table to
-- query — the load is out-of-band ETL.
-- #############################################################################

DROP TABLE IF EXISTS lpp.peer_company CASCADE;
CREATE TABLE lpp.peer_company (
    code           VARCHAR(32) NOT NULL,             -- e.g. WMT | TGT | HD | KR | LOW
    name           VARCHAR(256),
    ticker         VARCHAR(16),
    sector         VARCHAR(64),
    peer_group     VARCHAR(64),                      -- BIG_BOX_RETAIL | WAREHOUSE_CLUB | GROCERY | HOME_IMPROVEMENT
    country        VARCHAR(2),
    CONSTRAINT pk_peer_company PRIMARY KEY (code)
)
DISTSTYLE ALL;

DROP TABLE IF EXISTS lpp.peer_company_metric CASCADE;
CREATE TABLE lpp.peer_company_metric (
    peer_code            VARCHAR(32) NOT NULL,
    period_date          DATE NOT NULL,
    period_type          VARCHAR(8) NOT NULL,         -- Q | TTM | FY
    reporting_currency   VARCHAR(16),
    revenue              NUMERIC(28,6),
    ebitda               NUMERIC(28,6),
    free_cash_flow       NUMERIC(28,6),
    net_debt             NUMERIC(28,6),
    leverage_ratio       NUMERIC(9,4),
    debt_to_ebitda       NUMERIC(9,4),
    return_on_capital_pct NUMERIC(9,4),
    dividend_yield_pct   NUMERIC(9,4),
    buyback_yield_pct    NUMERIC(9,4),
    shareholder_return_yield_pct NUMERIC(9,4),
    wacc_pct             NUMERIC(9,4),
    -- Payments-relevant peer metrics
    payments_cost_pct_revenue NUMERIC(9,4),
    fraud_loss_bps        NUMERIC(9,2),
    interchange_pct       NUMERIC(9,4),
    CONSTRAINT pk_peer_metric PRIMARY KEY (peer_code, period_date, period_type)
)
DISTSTYLE ALL
SORTKEY (period_date);

DROP TABLE IF EXISTS lpp.macro_indicator CASCADE;
CREATE TABLE lpp.macro_indicator (
    indicator_code VARCHAR(64) NOT NULL,             -- FED_FUNDS | EUR_USD_VOL | OIL_BRENT | CPI_US | GEOPOL_RISK
    region         VARCHAR(16),
    as_of_date     DATE NOT NULL,
    value          NUMERIC(20,6) NOT NULL,
    unit           VARCHAR(32),
    source         VARCHAR(64),
    CONSTRAINT pk_macro PRIMARY KEY (indicator_code, region, as_of_date)
)
DISTSTYLE ALL
SORTKEY (as_of_date);

-- #############################################################################
-- R. SIGNATORY NORMALIZATION
--
-- The base BANK_ACCOUNT.signatory_users column is a SUPER blob. Project to a
-- relational shape so recertification reports run via plain SQL.
-- #############################################################################

DROP TABLE IF EXISTS lpp.bank_account_signatory CASCADE;
CREATE TABLE lpp.bank_account_signatory (
    uuid               VARCHAR(64) NOT NULL,
    bank_account_ref   VARCHAR(64) NOT NULL,         -- FK → BANK_ACCOUNT.code
    user_ref           VARCHAR(64) NOT NULL,         -- FK → APP_USER.code
    role               VARCHAR(32),                  -- SIGNER_A | SIGNER_B | RELEASER | VIEWER
    authority_limit_amount NUMERIC(28,6),
    authority_limit_currency VARCHAR(16),
    granted_date       DATE,
    last_recertified_date DATE,
    next_recertify_due_date DATE,
    status             VARCHAR(16),                  -- ACTIVE | PENDING | REVOKED
    CONSTRAINT pk_ba_sig PRIMARY KEY (uuid)
)
DISTSTYLE ALL
SORTKEY (next_recertify_due_date);
