import argparse
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

CONN = {
    "host":     os.environ["REDSHIFT_HOST"],
    "port":     int(os.environ.get("REDSHIFT_PORT", "5439")),
    "dbname":   os.environ["REDSHIFT_DBNAME"],
    "user":     os.environ["REDSHIFT_USER"],
    "password": os.environ["REDSHIFT_PASSWORD"],
}

HEADER_LINE = "-- " + "-" * 77


def get_col_types(cur, schema: str, table: str) -> dict:
    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return {row[0]: row[1].lower() for row in cur.fetchall()}


def format_value(v, col_type: str) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (float, Decimal)):
        return repr(float(v))
    if isinstance(v, datetime):
        return f"TIMESTAMP '{v.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'"
    s = str(v)
    if col_type == "super":
        escaped = s.replace("'", "''")
        return f"JSON_PARSE('{escaped}')"
    return "'" + s.replace("'", "''") + "'"


def export_table(cur, schema: str, table: str, batch_size: int, f) -> int:
    col_types = get_col_types(cur, schema, table)
    cur.execute(f"SELECT * FROM {schema}.{table}")
    cols     = [desc[0] for desc in cur.description]
    col_list = ", ".join(cols)
    total    = 0

    f.write(f"{HEADER_LINE}\n")
    f.write(f"-- {table}\n")
    f.write(f"{HEADER_LINE}\n")
    f.write(f"TRUNCATE TABLE {table};\n\n")

    batch = cur.fetchmany(batch_size)
    while batch:
        f.write(f"INSERT INTO {table} ({col_list}) VALUES\n")
        for idx, row in enumerate(batch):
            vals      = ", ".join(
                format_value(v, col_types.get(c, "")) for v, c in zip(row, cols)
            )
            separator = "," if idx < len(batch) - 1 else ";"
            f.write(f"    ({vals}){separator}\n")
        f.write("\n")
        total += len(batch)
        batch  = cur.fetchmany(batch_size)

    return total


def list_tables(cur, schema: str) -> list:
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        (schema,),
    )
    return [row[0] for row in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Redshift schema tables as INSERT SQL"
    )
    parser.add_argument("--schema",     default="lpp")
    parser.add_argument("--output-dir", default="./export")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--tables", default=None,
        help="Comma-separated table names; omit to export all tables in schema",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "insert.sql"

    conn = psycopg2.connect(**CONN)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute(f"SET search_path TO {args.schema}")

    tables = args.tables.split(",") if args.tables else list_tables(cur, args.schema)
    print(f"Exporting {len(tables)} tables from {args.schema} → {out_file}\n")

    with out_file.open("w", encoding="utf-8") as f:
        f.write(f"SET search_path TO {args.schema};\n\n")
        for table in tables:
            print(f"  {table} ...", end=" ", flush=True)
            rows = export_table(cur, args.schema, table, args.batch_size, f)
            print(f"{rows} rows")

    conn.close()
    print(f"\nWritten to {out_file}")


if __name__ == "__main__":
    main()
