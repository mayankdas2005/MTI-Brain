"""Redshift table and relationship mapper — exports tables, defined FK relationships, and LLM-inferred relationships."""

from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path

import psycopg2
from botocore.config import Config
from dotenv import load_dotenv
from langchain_aws import ChatBedrock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent.parent

CHUNK_SIZE = 20


def load_env() -> None:
    backend_env = REPO_ROOT / "backend" / ".env"
    local_env   = SCRIPT_DIR / ".env"
    if backend_env.exists():
        load_dotenv(dotenv_path=backend_env, override=False)
        log.info("Loaded credentials from backend/.env")
    else:
        log.warning("backend/.env not found")
    if local_env.exists():
        load_dotenv(dotenv_path=local_env, override=True)
        log.info("Local .env overrides applied")


def build_llm(read_timeout: int = 120, max_tokens: int = 4096) -> ChatBedrock:
    sonnet_arn = os.environ.get("AWS_BEDROCK_SONNET_ARN", "")
    if not sonnet_arn:
        raise RuntimeError("AWS_BEDROCK_SONNET_ARN is required but not set")
    region = os.environ.get("AWS_REGION", "us-west-2")
    bearer = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or None
    log.info("Initialising ChatBedrock model=%s region=%s timeout=%ds max_tokens=%d", sonnet_arn[:60], region, read_timeout, max_tokens)
    return ChatBedrock(
        model=sonnet_arn,
        provider="anthropic",
        api_key=bearer,
        region=region,
        streaming=False,
        model_kwargs={"temperature": 0.0, "max_tokens": max_tokens},
        config=Config(read_timeout=read_timeout, connect_timeout=30),
    )


def _split_schema_table(value: str) -> tuple[str, str]:
    if "." in value:
        schema, table = value.split(".", 1)
        return schema, table
    return "", value


def normalize_inferred_relationships_csv(path: str | Path = "inferred_relationships.csv") -> None:
    try:
        path = Path(path)
        if not path.exists():
            log.warning("File not found, skipping normalization: %s", path)
            return False

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            existing_fields = reader.fieldnames or []

        if "from_schema" in existing_fields:
            log.info("File already normalized, skipping: %s", path)
            return False

        normalized = []
        for row in rows:
            from_schema, from_table = _split_schema_table(row.get("from_table", ""))
            to_schema, to_table     = _split_schema_table(row.get("to_table", ""))
            normalized.append({
                "from_schema":  from_schema,
                "from_table":   from_table,
                "from_column":  row.get("from_column", ""),
                "to_schema":    to_schema,
                "to_table":     to_table,
                "to_column":    row.get("to_column", ""),
                "confidence":   row.get("confidence", ""),
                "reasoning":    row.get("reasoning", ""),
            })

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["from_schema", "from_table", "from_column", "to_schema", "to_table", "to_column", "confidence", "reasoning"])
            writer.writeheader()
            writer.writerows(normalized)
        log.info("Normalized %d rows → %s", len(normalized), path)
        return True
    except Exception as exc:
        log.error("Error normalizing inferred relationships CSV: %s", exc)
        return False

def get_defined_relationships(cur) -> list:
    log.info("Querying defined FK relationships ...")
    cur.execute("""
        SELECT
            tc.table_schema        AS from_schema,
            tc.table_name          AS from_table,
            kcu.column_name        AS from_column,
            ccu.table_schema       AS to_schema,
            ccu.table_name         AS to_table,
            ccu.column_name        AS to_column,
            tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
        ORDER BY from_schema, from_table
    """)
    relationships = cur.fetchall()
    with open("relationships.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["from_schema", "from_table", "from_column", "to_schema", "to_table", "to_column", "constraint_name"])
        writer.writerows(relationships)
    log.info("Exported %d defined FK relationships to relationships.csv", len(relationships))
    return relationships


def gen_inferred_relationships(cur, llm: ChatBedrock) -> list:
    sample_rows = int(os.getenv("SAMPLE_ROWS", "5"))

    # Step A: Column metadata
    log.info("Fetching column metadata ...")
    cur.execute("""
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name, ordinal_position
    """)
    tables: dict[str, list] = {}
    for schema, table, col, dtype in cur.fetchall():
        tables.setdefault(f"{schema}.{table}", []).append({"column": col, "type": dtype})
    log.info("Found %d tables with column metadata", len(tables))

    # Step B: Sample data per table
    log.info("Sampling up to %d rows per table ...", sample_rows)
    table_samples: dict[str, dict] = {}
    for table_key in tables:
        try:
            cur.execute(f"SELECT * FROM {table_key} LIMIT {sample_rows}")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            table_samples[table_key] = {
                "columns": col_names,
                "sample_rows": [list(r) for r in rows],
            }
        except Exception as exc:
            log.warning("Skipping %s: %s", table_key, exc)
            cur.connection.rollback()

    # Step C: Chunked LLM calls
    all_relationships: list[dict] = []
    table_keys = list(table_samples.keys())
    chunks = [table_keys[i:i + CHUNK_SIZE] for i in range(0, len(table_keys), CHUNK_SIZE)]
    log.info("Running LLM inference over %d chunks (%d tables/chunk) ...", len(chunks), CHUNK_SIZE)

    for i, chunk in enumerate(chunks, start=1):
        chunk_data = {k: table_samples[k] for k in chunk}
        prompt = (
            "You are a database schema analyst. Given the following Redshift table schemas and sample data, "
            "identify likely foreign key relationships between tables.\n\n"
            "Look for:\n"
            "- Column names that match or are similar across tables (e.g., customer_id in orders and customers)\n"
            "- ID columns in one table whose values appear in another table's column\n"
            "- Naming conventions like table_name + _id\n\n"
            f"Tables and sample data:\n{json.dumps(chunk_data, indent=2, default=str)}\n\n"
            "Return ONLY a JSON array of relationships in this exact format:\n"
            "[\n"
            "  {\n"
            '    "from_table": "schema.table",\n'
            '    "from_column": "column_name",\n'
            '    "to_table": "schema.table",\n'
            '    "to_column": "column_name",\n'
            '    "confidence": "high|medium|low",\n'
            '    "reasoning": "brief explanation"\n'
            "  }\n"
            "]\n"
            "Return [] if no relationships found. No other text."
        )
        try:
            response = llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
            rels = json.loads(text)
            if isinstance(rels, list):
                all_relationships.extend(rels)
                log.info("[%d/%d] Found %d relationships", i, len(chunks), len(rels))
            else:
                log.warning("[%d/%d] Unexpected LLM response shape, skipping", i, len(chunks))
        except json.JSONDecodeError as exc:
            log.error("[%d/%d] Failed to parse LLM response as JSON: %s", i, len(chunks), exc)
        except Exception as exc:
            log.error("[%d/%d] LLM error: %s", i, len(chunks), exc)

    # Step D: Write output — split "schema.table" into separate schema and table columns
    fieldnames = ["from_schema", "from_table", "from_column", "to_schema", "to_table", "to_column", "confidence", "reasoning"]
    with open("inferred_relationships.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rel in all_relationships:
            from_schema, from_table = _split_schema_table(rel.get("from_table", ""))
            to_schema, to_table     = _split_schema_table(rel.get("to_table", ""))
            writer.writerow({
                "from_schema":  from_schema,
                "from_table":   from_table,
                "from_column":  rel.get("from_column", ""),
                "to_schema":    to_schema,
                "to_table":     to_table,
                "to_column":    rel.get("to_column", ""),
                "confidence":   rel.get("confidence", ""),
                "reasoning":    rel.get("reasoning", ""),
            })
    log.info("Exported %d inferred relationships to inferred_relationships.csv", len(all_relationships))
    return all_relationships


def main() -> None:
    try:
        load_env()

        host     = os.getenv("REDSHIFT_HOST")
        port     = os.getenv("REDSHIFT_PORT")
        dbname   = os.getenv("REDSHIFT_DB")
        user     = os.getenv("REDSHIFT_USER")
        password = os.getenv("REDSHIFT_PASSWORD")

        log.info("Connecting to Redshift at %s:%s/%s ...", host, port, dbname)
        conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
        cur = conn.cursor()

        cur.execute("""
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name
        """)
        tables = cur.fetchall()
        with open("tables.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["schema", "table_name", "table_type"])
            writer.writerows(tables)
        log.info("Exported %d tables to tables.csv", len(tables))

        get_defined_relationships(cur)

        if not normalize_inferred_relationships_csv("inferred_relationships.csv"):
            llm = build_llm()
            gen_inferred_relationships(cur, llm)
            normalize_inferred_relationships_csv("inferred_relationships.csv")

        cur.close()
        conn.close()
    except Exception as exc:
        log.error("Error in main: %s", exc)

if __name__ == "__main__":
    main()
