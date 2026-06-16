# gen-redshift — Redshift port of the LPP synthetic-data generator

This directory contains the Amazon Redshift port of the 15 generator scripts
in `gen/` (Snowflake). The data-generation **logic, volumes, seasonality,
scripted events, and validation expectations are preserved**; only the SQL
dialect has been translated.

## Execution order

The scripts must be run in numeric order, after the schema DDL has been applied:

```
lpp-schema-redshift.sql               -- creates lpp.* core tables
lpp-schema-extensions-redshift.sql    -- creates Brain layer / extension tables

gen-redshift/00_setup.sql             -- gen_control, gen_numbers, UDFs, calendar
gen-redshift/01_reference_data.sql    -- ccy, banks, branches, companies, accounts
gen-redshift/02_rates.sql             -- FX_RATE, BENCHMARK_RATE
gen-redshift/03_static_config.sql     -- sweep instructions, policies, instruments
gen-redshift/04_cash_flows.sql        -- operational cash flows
gen-redshift/05_invoices.sql          -- AR/AP/WCF + linked cash flows
gen-redshift/06_intercompany_sweeps.sql
gen-redshift/07_investments_debt.sql
gen-redshift/08_hedges_fx.sql
gen-redshift/09_fees_forecasts.sql
gen-redshift/10_recompute_balances.sql
gen-redshift/11_metrics_recon.sql
gen-redshift/12_payment_execution.sql
gen-redshift/13_access_audit.sql
gen-redshift/14_card_acquiring.sql    -- extensions-2 § M (acquirers, POS, auths, settlement, chargebacks, ACH returns, rebates, membership fees, exceptions, cross-border, fraud loss, SLA, payment-hub)
gen-redshift/15_corporate_finance.sql -- extensions-2 § N/O/P (company financial metrics, credit ratings, equity actions, capital allocation, letters of credit, pension valuations)
gen-redshift/16_external_benchmarks.sql -- extensions-2 § Q (peer companies & metrics, macro indicators)
gen-redshift/17_signatory.sql         -- extensions-2 § R (bank account signatories, recertification due dates)
gen-redshift/99_validate.sql          -- QA checks (now V1-V45)
```

Scripts 14-17 require the schema in `lpp-schema-extensions-2-redshift.sql` to
be applied first. They also depend on data populated by scripts 01-13
(companies, bank_accounts, transfer, payment_file, app_user, credit_facility).

Each script begins with `SET search_path TO lpp;`.

## Setting SCALE_FACTOR / SEED / window

Snowflake session variables (`SET SCALE_FACTOR = ...`) have no clean Redshift
equivalent, so all run-time tunables live in the `gen_control` table created by
`00_setup.sql`. Defaults:

| key             | default      |
| --------------- | ------------ |
| `SCALE_FACTOR`  | `1.0`        |
| `SEED`          | `42`         |
| `WINDOW_START`  | `2023-05-01` |
| `WINDOW_END`    | `2026-05-04` |

To run at smaller scale (recommended for first-pass / dev), update before kicking
off `01_*` onward:

```sql
UPDATE lpp.gen_control SET value = '0.1' WHERE key = 'SCALE_FACTOR';
```

All scripts read it via `(SELECT value::FLOAT FROM gen_control WHERE key='SCALE_FACTOR')`.

## Dialect substitutions (and why)

| Snowflake                          | Redshift translation                                                 |
| ---------------------------------- | -------------------------------------------------------------------- |
| `STRING`                           | `VARCHAR(256)` (or `VARCHAR(MAX)` for memos in schema DDL)           |
| `VARIANT`                          | `SUPER`                                                              |
| `TIMESTAMP_TZ`                     | `TIMESTAMPTZ`                                                        |
| `NUMBER(p,s)`                      | `NUMERIC(p,s)`                                                       |
| `CREATE OR REPLACE TABLE T (...)`  | `DROP TABLE IF EXISTS T CASCADE; CREATE TABLE T (...)`               |
| `CREATE OR REPLACE FUNCTION ...`   | Same syntax + `LANGUAGE sql IMMUTABLE` — Redshift SQL UDFs only      |
| `HASH(x)` / `HASH(a,b)`            | `FNV_HASH(x::varchar)` — for multi-arg, we concatenate `a||'|'||b`   |
| `SHA2(x)` (256-bit, 64 hex chars)  | `SHA2(x, 256)` — native in current Redshift versions; returns the same 64-hex-char digest. `gen_uuid()` output is **bit-identical** to the Snowflake build given the same `SEED` + key. |
| `UNIFORM(lo, hi, RANDOM(seed))`    | Deterministic: `MOD(ABS(FNV_HASH(seed)), hi-lo+1) + lo`              |
| `RANDOM(seed)`                     | Replaced inline by `FNV_HASH(seed)` where used as a seed             |
| `IFF(c,a,b)`                       | `CASE WHEN c THEN a ELSE b END`                                      |
| `COUNT_IF(c)`                      | `COUNT(CASE WHEN c THEN 1 END)`                                      |
| `DAYOFWEEK(d)`                     | `EXTRACT(DOW FROM d)` (Sun=0..Sat=6, same as Snowflake default)      |
| `YEAR(d)/MONTH(d)/DAY(d)`          | `EXTRACT(YEAR FROM d)` / `EXTRACT(MONTH FROM d)` / `EXTRACT(DAY FROM d)` |
| `WEEKISO(d)`                       | `EXTRACT(WEEK FROM d)` (Redshift defaults to ISO week)               |
| `DATEDIFF('day', a, b)`            | `DATEDIFF(day, a, b)` (unit unquoted)                                |
| `DATEADD('day', n, d)`             | `DATEADD(day, n, d)`                                                 |
| `DATE_TRUNC('month', d)`           | `DATE_TRUNC('month', d)` (unchanged)                                 |
| `LAST_DAY(d)`                      | `LAST_DAY(d)` (unchanged)                                            |
| `DATE_FROM_PARTS(y, m, d)`         | `TO_DATE(y||'-'||m||'-'||d, 'YYYY-MM-DD')` (string-built)            |
| Session variables `$VAR`           | Rows in `gen_control`, read via scalar subquery                       |
| `TABLE(GENERATOR(ROWCOUNT=>N))` + `SEQ4()` | `gen_numbers` table (0..100,000) pre-populated in `00_setup.sql`; `SELECT FROM gen_numbers WHERE n < N` and use `n` as the row index |
| `ARRAY_CONSTRUCT(a,b,...)[i]`      | Inline `CASE MOD(...) WHEN 0 THEN ... END` ladders. We did **not** use Redshift SUPER `ARRAY()` subscripting because the array element type would be SUPER and complicate downstream casts. |
| `OBJECT_CONSTRUCT(k,v,...)`        | `JSON_PARSE('{"k":"' || v || '"}')` — built by string concatenation. Where the inner value contains quotes (e.g. dedesignation reasons), we apply `REPLACE(..., '"', '\"')`. |
| `LISTAGG(x, ',')`                  | Unchanged (Redshift supports it)                                     |
| `QUALIFY ROW_NUMBER() OVER ...`    | Wrap in subquery + outer `WHERE rn=1`                                |
| `LIMIT 1` in scalar subqueries     | Supported in Redshift                                                |
| `MERGE INTO ...`                   | Not used in this port; the original generators did not need it      |
| `IFNULL`                           | `COALESCE` (`NVL` also works)                                        |
| `RANDOM()` / `UNIFORM(...)`        | Replaced by deterministic `FNV_HASH`-based expressions everywhere    |
| `EXISTS(...)` in SELECT list       | Supported in Redshift                                                |
| `CREATE OR REPLACE TEMP TABLE`     | `DROP TABLE IF EXISTS x; CREATE TEMP TABLE x AS ...`                 |
| Snowflake `VALUES (...) AS t(c1,c2)` row constructors | Rewritten as `SELECT ... UNION ALL SELECT ...` subqueries that carry the column aliases via the first row's `AS` clause |

## Non-trivial decisions and TODOs

1. **Determinism**. UUIDs match Snowflake bit-for-bit thanks to native `SHA2`.
   `FNV_HASH` (replacing Snowflake `HASH`) is a different hash family, so the
   *row-by-row bucket assignment* (which exact `TP_LAND_NNNN` a given rent
   payment routes to, etc.) will differ from the Snowflake build — but is
   fully deterministic on Redshift for a fixed `SEED`, and the distributional
   shape (volumes, seasonality, currency mix) is preserved. All scripted
   scenarios (BEC fraud, Iberica default, Carnival/CNY sweep failures, BANK_DB
   fee overage cluster, USA_RGNL outage, BRL FX gap, CDOR cessation) are
   date-literal-driven and therefore unaffected.

2. **`gen_numbers` table**. Pre-populated 0..1,000,000 rows in `00_setup.sql`
   via a 6-digit cross-join. This covers every generator in the pipeline —
   including the B2B AR invoice generator (240,000 rows at `SCALE_FACTOR=1.0`)
   — with comfortable headroom. Earlier port revisions sized this at 100,001
   and capped AR volumes; fixed.

3. **`ARRAY_CONSTRUCT(...)[index]` → CASE ladders**. Original picks like
   `ARRAY_CONSTRUCT('USD','EUR','GBP',...)[MOD(...)]` are translated to large
   `CASE MOD(...) WHEN 0 THEN 'USD' WHEN 1 THEN 'EUR' ... END` expressions. The
   ordering of values is preserved 1:1 so the modular bucketing keeps the same
   distribution shape.

4. **`OBJECT_CONSTRUCT` → JSON_PARSE strings**. SUPER values are built via
   string concatenation then `JSON_PARSE`. Any user-provided narrative string
   that may contain `"` (e.g. dedesignation reasons) is escaped with
   `REPLACE(x, '"', '\"')`. **TODO**: a UDF wrapper would be cleaner; left
   inline for transparency.

5. **`SHA2` in `gen_uuid`**. Redshift's native `SHA2(x, 256)` is used directly;
   no SHA-1 workaround needed. UUID output matches Snowflake exactly given the
   same SEED + key.

6. **Bank-fee back-link**. The Snowflake `UPDATE BANK_FEE bf SET cash_flow_ref =
   (SELECT uuid FROM CASH_FLOW cf WHERE cf.reference = bf.uuid LIMIT 1)` was
   rewritten using Redshift's `UPDATE ... FROM` joined-update syntax in
   `09_fees_forecasts.sql`.

7. **`UPDATE ... FROM table` syntax**. Redshift requires the target table to be
   referenced unqualified in the `SET` clause and any cross-table predicates go
   into the `FROM`/`WHERE`. Used in `07_investments_debt.sql` for the coupon
   update.

8. **`LIMIT 1` in scalar subqueries**. Used in several places (AR/AP funding
   account lookup, BEC `transfer_uuid` link, GL recon fallback). Redshift
   supports this; the subquery must still return ≤1 row at runtime.

9. **`WEBHOOK_EVENT.entity_code` deterministic file selection**. Original used
   `(SELECT file_uuid FROM PAYMENT_FILE ORDER BY HASH(file_uuid) LIMIT 1 OFFSET MOD(SEQ4(),100))`.
   Redshift port wraps the payment-file set in a `ROW_NUMBER() OVER (ORDER BY
   FNV_HASH(file_uuid))` and matches `rn = MOD(n,100)+1`. Semantics: same idea
   of "deterministic round-robin over a stable random ordering."

10. **Snowflake-specific schema columns absent from Redshift schema**. None
    were dropped. If the schema ports diverge from `lpp-schema.sql` for a
    column not present below, add a note here. The translation was reviewed
    against `lpp-schema-redshift.sql` and `lpp-schema-extensions-redshift.sql`
    and all referenced columns exist.

11. **Performance**. Several heavyweight steps now lean on the `gen_numbers`
    cross-join + `FNV_HASH` modulus pattern. On Redshift, this should
    distribute well but you may want to `ANALYZE` after each script for
    optimal planning. Distribution/sort keys for the data tables themselves
    are defined in `lpp-schema-redshift.sql` and are not touched here.

## Validation expectations

`99_validate.sql` mirrors the original. Expected outcomes:

- V1–V13: **0 violations**
- V14: `gap_violations=0` (3 BRL spot rows deleted as expected)
- V15: ≥35 Iberica writeoffs
- V16: exactly 2 BEC fraud rows
- V17: >0 BANK_DB exposure rows in breach window
- V18: >0 scripted sweep failures
- V19: ≥10% AP cross-currency
- V20: 0 unlinked PAID B2B AR invoices
- V21: small non-zero (CNY shutdown deliberately suppresses some AP legs)
- V22, V23: 0
- V24, V25: >0
- V26: large (APAC distribution-center H2 2024 overrun)
- V27: 0 RTP rows before 2024-01-01, >0 after
- V28: >0 PAYMENT_FILE / TRANSFER
- V29: 30 distinct accounts with INTRADAY balances
- V30: exactly 4 FAILED rows in USA_RGNL outage cluster
- V31: 7 distinct `CASH_BALANCE` combos
- V32: 24 distinct companies in DATA_PERMISSION

## Lint pass — 2026-05-11

Static review of all 15 ported scripts (skipping `00_setup.sql`, already fixed
by the human). All issues fixed in-place with `-- LINT FIX:` comments.

### Files touched

| File                         | Edits | Notes                                                        |
| ---------------------------- | ----- | ------------------------------------------------------------ |
| `01_reference_data.sql`      | 7     | `JSON_PARSE('{...}')` string-concat → `OBJECT(...)`          |
| `04_cash_flows.sql`          | 1     | Correlated scalar subquery in JOIN ON predicate (payroll month-end) refactored as a LEFT-joined `is_last_bd_of_month` flag via a pre-joined subquery |
| `09_fees_forecasts.sql`      | 2     | `JSON_PARSE('["..."]')` → `ARRAY(...)` SUPER constructor     |
| `12_payment_execution.sql`   | 1     | `JSON_PARSE('{"description":...}')` w/ partial `REPLACE` escape (NULL-unsafe, missing backslash escape) → `OBJECT('description', cf.description)` |
| `13_access_audit.sql`        | 7     | 6× `JSON_PARSE` → `OBJECT(...)` (audit before/after images, webhook payload); 1× correlated subquery containing a window function rewritten as a CTE + JOIN (`pf_ranked`) for the webhook `entity_code` selector |

Files reviewed and left unchanged (no dialect issues found):
`02_rates.sql`, `03_static_config.sql` (only literal-string `JSON_PARSE`s with
hard-coded JSON — safe), `05_invoices.sql`, `06_intercompany_sweeps.sql`,
`07_investments_debt.sql`, `08_hedges_fx.sql`, `10_recompute_balances.sql`,
`11_metrics_recon.sql`, `99_validate.sql`.

### Top fix categories by count

1. **Trap #3 — string-built JSON via `JSON_PARSE`** (15 edits across 4 files)
   replaced with native `OBJECT(...)` / `ARRAY(...)` SUPER constructors.
   Eliminates the latent NULL/quote/backslash-escape bugs (the `REPLACE(x,'"','\"')`
   pattern was incomplete — missing backslash escaping and untyped numerics).
2. **Trap #6 / correlated subquery placement** (2 edits) — the payroll-month-end
   `JOIN gen_calendar c ON … = (correlated scalar subquery)` in `04_cash_flows.sql`
   was hoisted into a LEFT-joined flag (`is_last_bd_of_month`) on a pre-joined
   inline derived table; in `13_access_audit.sql` a correlated subquery containing
   a window function was rewritten as a top-level CTE + JOIN.

(No `RANDOM()`, `QUALIFY`, `IFF`/`IFNULL`/`BOOLAND_AGG`/`COUNT_IF`, `*EXCLUDE`,
`DATE_FROM_PARTS`, `::variant`, MERGE, COPY-Snowflake, or lateral-unnest issues
were found — those Snowflake idioms had already been translated cleanly in the
port. Function forward-references in `00_setup.sql` are correctly ordered.)

### Unresolved TODOs

None. All identified issues were fixed in place.

The previous README claim under §4 ("OBJECT_CONSTRUCT → JSON_PARSE strings …
a UDF wrapper would be cleaner; left inline for transparency") is now obsolete:
all SUPER-typed columns are populated via native `OBJECT(...)` / `ARRAY(...)`
constructors. The `JSON_PARSE` literal-string pattern remains only in
`03_static_config.sql` for hard-coded scenario-parameter JSON (no concatenation,
no NULLs — safe).
