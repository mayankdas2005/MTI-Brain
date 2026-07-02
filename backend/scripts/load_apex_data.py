"""Load Apex Retail CSV data into Redshift schema 'apex'.

One-command data migration for the analytics warehouse.

Usage (from backend/):
    python scripts/load_apex_data.py

Reads Redshift credentials from .env (REDSHIFT_HOST, REDSHIFT_DB, etc.)
Creates schema 'apex', drops + recreates tables, batch-inserts all rows.
"""

import csv
import os
import sys
from pathlib import Path

import psycopg2

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
CSV_DIR = PROJECT_ROOT / "data" / "Apex Retail"
SCHEMA = "apex"

# ─── Table Definitions ────────────────────────────────────────────────────────

TABLES = [
    ("Dim_Bank_Accounts.csv", "dim_bank_accounts", [
        ("account_id", "VARCHAR(50) PRIMARY KEY"),
        ("bank_name", "VARCHAR(100)"),
        ("account_type", "VARCHAR(100)"),
        ("account_number", "VARCHAR(50)"),
        ("routing_transit_number", "VARCHAR(50)"),
        ("currency", "VARCHAR(20)"),
        ("plaid_item_id", "VARCHAR(100)"),
    ]),
    ("Dim_Credit_Facilities.csv", "dim_credit_facilities", [
        ("facility_id", "VARCHAR(50) PRIMARY KEY"),
        ("lender_syndicate", "VARCHAR(500)"),
        ("facility_type", "VARCHAR(100)"),
        ("total_commitment", "DECIMAL(18,2)"),
        ("amount_drawn", "DECIMAL(18,2)"),
        ("available_capacity", "DECIMAL(18,2)"),
        ("interest_rate_basis", "VARCHAR(50)"),
        ("margin_bps", "INTEGER"),
        ("maturity_date", "DATE"),
    ]),
    ("Dim_FX_Hedges.csv", "dim_fx_hedges", [
        ("hedge_id", "VARCHAR(50) PRIMARY KEY"),
        ("hedge_type", "VARCHAR(100)"),
        ("currency_pair", "VARCHAR(50)"),
        ("notional_foreign_amount", "DECIMAL(18,2)"),
        ("strike_rate", "DECIMAL(10,6)"),
        ("current_forward_rate", "DECIMAL(10,6)"),
        ("execution_date", "DATE"),
        ("value_maturity_date", "DATE"),
        ("counterparty_bank", "VARCHAR(100)"),
        ("unrealized_gain_loss_usd", "DECIMAL(18,2)"),
    ]),
    ("Dim_Investment_Positions.csv", "dim_investment_positions", [
        ("position_id", "VARCHAR(50) PRIMARY KEY"),
        ("asset_class", "VARCHAR(100)"),
        ("issuer", "VARCHAR(200)"),
        ("credit_rating", "VARCHAR(20)"),
        ("purchase_date", "DATE"),
        ("maturity_date", "VARCHAR(50)"),
        ("principal_notional", "DECIMAL(18,2)"),
        ("yield_to_maturity", "VARCHAR(20)"),
        ("current_market_value", "DECIMAL(18,2)"),
    ]),
    ("Dim_Letters_Of_Credit.csv", "dim_letters_of_credit", [
        ("lc_reference_id", "VARCHAR(50) PRIMARY KEY"),
        ("issuing_bank", "VARCHAR(100)"),
        ("beneficiary_vendor_id", "VARCHAR(50)"),
        ("lc_type", "VARCHAR(100)"),
        ("notional_amount", "DECIMAL(18,2)"),
        ("currency", "VARCHAR(20)"),
        ("issue_date", "DATE"),
        ("expiry_date", "DATE"),
        ("status", "VARCHAR(50)"),
    ]),
    ("Dim_Stores.csv", "dim_stores", [
        ("store_id", "VARCHAR(50) PRIMARY KEY"),
        ("store_name", "VARCHAR(100)"),
        ("region", "VARCHAR(50)"),
        ("city", "VARCHAR(100)"),
        ("state", "VARCHAR(20)"),
        ("zip_code", "VARCHAR(20)"),
        ("primary_deposit_account_id", "VARCHAR(50)"),
        ("pos_system", "VARCHAR(50)"),
    ]),
    ("Dim_Vendors.csv", "dim_vendors", [
        ("vendor_id", "VARCHAR(50) PRIMARY KEY"),
        ("vendor_name", "VARCHAR(200)"),
        ("vendor_type", "VARCHAR(50)"),
        ("category", "VARCHAR(100)"),
        ("payment_terms", "VARCHAR(50)"),
        ("primary_payment_method", "VARCHAR(50)"),
        ("routing_transit_number", "VARCHAR(50)"),
    ]),
    ("Fact_Daily_Bank_Balances.csv", "fact_daily_bank_balances", [
        ("balance_id", "VARCHAR(50) PRIMARY KEY"),
        ("date", "DATE"),
        ("account_id", "VARCHAR(50)"),
        ("opening_balance", "DECIMAL(18,2)"),
        ("net_inflows", "DECIMAL(18,2)"),
        ("net_outflows", "DECIMAL(18,2)"),
        ("closing_balance", "DECIMAL(18,2)"),
    ]),
    ("Fact_Daily_Cash_Transactions.csv", "fact_daily_cash_transactions", [
        ("transaction_id", "VARCHAR(50) PRIMARY KEY"),
        ("date", "DATE"),
        ("account_id", "VARCHAR(50)"),
        ("store_id", "VARCHAR(50)"),
        ("transaction_type", "VARCHAR(50)"),
        ("amount", "DECIMAL(18,2)"),
        ("description", "VARCHAR(500)"),
    ]),
    ("Fact_Outbound_Payments.csv", "fact_outbound_payments", [
        ("payment_id", "VARCHAR(50) PRIMARY KEY"),
        ("date", "DATE"),
        ("account_id", "VARCHAR(50)"),
        ("payment_method", "VARCHAR(50)"),
        ("amount", "DECIMAL(18,2)"),
        ("beneficiary_name", "VARCHAR(500)"),
        ("status", "VARCHAR(50)"),
    ]),
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_date(val: str) -> str | None:
    """Convert M/D/YYYY to YYYY-MM-DD for Redshift."""
    if not val or val.upper() == "OPEN":
        return None
    parts = val.split("/")
    if len(parts) == 3:
        m, d, y = parts
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return val


def _parse_numeric(val: str) -> str | None:
    """Strip %, commas from numeric values."""
    if not val:
        return None
    val = val.replace(",", "").replace("%", "").strip()
    return val if val else None


def _get_connection():
    """Connect to Redshift using .env credentials."""
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env")

    host = os.environ.get("REDSHIFT_HOST", "")
    if not host:
        print("ERROR: REDSHIFT_HOST not set in .env")
        sys.exit(1)

    return psycopg2.connect(
        host=host,
        dbname=os.environ.get("REDSHIFT_DB", "dev"),
        user=os.environ["REDSHIFT_USER"],
        password=os.environ["REDSHIFT_PASSWORD"],
        port=int(os.environ.get("REDSHIFT_PORT", "5439")),
        sslmode="disable",
        connect_timeout=30,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not CSV_DIR.exists():
        print(f"ERROR: Data directory not found: {CSV_DIR}")
        sys.exit(1)

    print(f"Data source: {CSV_DIR}")
    print(f"Connecting to Redshift...")
    conn = _get_connection()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};")
        print(f"Schema '{SCHEMA}' ready.\n")

        total_rows = 0
        for csv_file, table_name, col_defs in TABLES:
            fqn = f"{SCHEMA}.{table_name}"
            csv_path = CSV_DIR / csv_file

            if not csv_path.exists():
                print(f"  SKIP {csv_file} (not found)")
                continue

            # Drop + create
            cur.execute(f"DROP TABLE IF EXISTS {fqn} CASCADE;")
            cols_sql = ", ".join(f"{name} {dtype}" for name, dtype in col_defs)
            cur.execute(f"CREATE TABLE {fqn} ({cols_sql});")

            # Identify column types for parsing
            date_cols = set()
            numeric_cols = set()
            for i, (name, dtype) in enumerate(col_defs):
                if "DATE" in dtype.upper() and "VARCHAR" not in dtype.upper():
                    date_cols.add(i)
                elif "DECIMAL" in dtype.upper() or "INTEGER" in dtype.upper():
                    numeric_cols.add(i)

            # Insert data
            col_names = [name for name, _ in col_defs]
            placeholders = ", ".join(["%s"] * len(col_names))
            insert_sql = f"INSERT INTO {fqn} ({', '.join(col_names)}) VALUES ({placeholders})"

            row_count = 0
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                next(reader)  # skip header

                batch = []
                for row in reader:
                    if len(row) != len(col_defs):
                        continue

                    parsed = []
                    for i, val in enumerate(row):
                        val = val.strip()
                        if not val:
                            parsed.append(None)
                        elif i in date_cols:
                            parsed.append(_parse_date(val))
                        elif i in numeric_cols:
                            parsed.append(_parse_numeric(val))
                        else:
                            parsed.append(val)
                    batch.append(parsed)
                    row_count += 1

                    if len(batch) >= 500:
                        cur.executemany(insert_sql, batch)
                        batch = []

                if batch:
                    cur.executemany(insert_sql, batch)

            total_rows += row_count
            print(f"  {fqn:<45} {row_count:>6} rows")

        conn.commit()
        print(f"\n{'='*60}")
        print(f"  SUCCESS: {len(TABLES)} tables, {total_rows} total rows")
        print(f"  Schema: {SCHEMA}")
        print(f"{'='*60}")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
