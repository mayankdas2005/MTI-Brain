"""
enrich_ontology.py

Pipeline:
  1. Read semantic_model.yml  → tables + columns
  2. Sample Redshift          → per-column example values
  3. LLM (Bedrock Sonnet)     → generate table + column descriptions (one call per table)
  4. Checkpoint to JSON       → save after every table so crashes don't lose progress
  5. Build enriched TTL       → merge lpp-ontology.ttl + lpp-r2rml.ttl + LLM descriptions
                                 into one self-contained output TTL

Usage:
    python enrich_ontology.py              # full run
    python enrich_ontology.py --ttl-only   # skip sampling/LLM, rebuild TTL from cache
    python enrich_ontology.py --reset      # wipe cache and start fresh
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import boto3
import psycopg2
import yaml
from dotenv import load_dotenv
from rdflib import Graph, Literal, Namespace, RDF, RDFS, OWL, URIRef
from rdflib.namespace import XSD

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR.parent / "backend" / "data"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

SEMANTIC_MODEL   = OUTPUT_DIR / "semantic_model.yml"
CACHE_FILE       = OUTPUT_DIR / "enrichment_cache.json"
ONTOLOGY_TTL     = DATA_DIR / "lpp-ontology.ttl"
R2RML_TTL        = DATA_DIR / "lpp-r2rml.ttl"
ENRICHED_TTL_OUT = OUTPUT_DIR / "lpp-ontology-enriched.ttl"

LPP  = Namespace("https://lpp.example/ontology#")
RR   = Namespace("http://www.w3.org/ns/r2rml#")

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------

load_dotenv(BASE_DIR / ".env")

PG_HOST   = os.environ["POSTGRES_HOST"]
PG_PORT   = int(os.environ.get("POSTGRES_PORT", 5439))
PG_DB     = os.environ["POSTGRES_DB"]
PG_USER   = os.environ["POSTGRES_USER"]
PG_PASS   = os.environ["POSTGRES_PASSWORD"]
PG_SCHEMA = os.environ.get("POSTGRES_SCHEMA", "lpp")

AWS_REGION      = os.environ.get("AWS_REGION", "us-west-2")
AWS_KEY_ID      = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET      = os.environ["AWS_SECRET_ACCESS_KEY"].strip("'")
SONNET_ARN      = os.environ["AWS_BEDROCK_SONNET_ARN"]

# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Redshift sampling
# ---------------------------------------------------------------------------

def get_db_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
        connect_timeout=15,
        sslmode="require",
    )


def sample_table(conn, source_table: str, columns: list[str], limit: int = 15) -> dict[str, list]:
    """
    Returns {col_name: [distinct_sample_values, ...]} for each column.
    Falls back to empty lists if table doesn't exist or query fails.
    """
    # strip "(sql) " prefix and any stray punctuation (e.g. trailing ")" from subquery parsing)
    table = re.sub(r"^\(sql\)\s*", "", source_table).strip()
    table = re.sub(r"[^\w\.]", "", table)  # keep only word chars and dots

    samples: dict[str, list] = {c: [] for c in columns}
    if not table:
        return samples

    # build column list from only the db_column names we know
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT * FROM {table} LIMIT {limit}')
            col_names = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()

        # map db_column → sample values
        col_idx = {name: i for i, name in enumerate(col_names)}
        for col in columns:
            idx = col_idx.get(col)
            if idx is None:
                continue
            seen, vals = set(), []
            for row in rows:
                v = row[idx]
                if v is not None and str(v) not in seen:
                    seen.add(str(v))
                    vals.append(str(v))
                    if len(vals) >= 8:
                        break
            samples[col] = vals

    except Exception as e:
        print(f"    [warn] could not sample {table}: {e}")
        conn.rollback()

    return samples


def sample_table_sql(conn, sql_query: str, columns: list[str], limit: int = 15) -> dict[str, list]:
    """
    Execute the raw SQL query (wrapped as a subquery) and return samples by alias column name.
    Used for TriplesMaps whose rr:column values are SQL SELECT aliases, not physical columns.
    """
    samples: dict[str, list] = {c: [] for c in columns}
    wrapped = f"SELECT * FROM (\n{sql_query}\n) AS _s LIMIT {limit}"
    try:
        with conn.cursor() as cur:
            cur.execute(wrapped)
            col_names = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        col_idx = {name: i for i, name in enumerate(col_names)}
        for col in columns:
            idx = col_idx.get(col)
            if idx is None:
                continue
            seen, vals = set(), []
            for row in rows:
                v = row[idx]
                if v is not None and str(v) not in seen:
                    seen.add(str(v))
                    vals.append(str(v))
                    if len(vals) >= 8:
                        break
            samples[col] = vals
    except Exception as e:
        print(f"    [warn] SQL sampling failed: {e}")
        conn.rollback()
    return samples


# ---------------------------------------------------------------------------
# LLM description generation
# ---------------------------------------------------------------------------

def build_bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_KEY_ID,
        aws_secret_access_key=AWS_SECRET,
    )


SYSTEM_PROMPT = """You are a senior data engineer documenting a treasury and payments data warehouse
for a large retail company (similar to Costco). The system covers bank accounts, cash positions,
transactions, FX hedging, card acquiring, fraud, capital allocation, and peer benchmarks.
Your descriptions must be precise, business-facing, and useful for an AI that generates SPARQL queries."""


def generate_descriptions(bedrock, class_name: str, source_table: str,
                           columns: list[str], samples: dict[str, list] = None) -> dict:
    """
    One LLM call per table. Returns:
      { "table_description": "...", "columns": { col: "..." } }
    """
    samples = samples or {}
    col_lines = []
    for col in columns:
        vals = samples.get(col, [])
        sample_str = ", ".join(f'"{v}"' for v in vals[:6]) if vals else "no samples"
        col_lines.append(f"  - {col}: [{sample_str}]")

    columns_block = "\n".join(col_lines) if col_lines else "  (no columns)"

    prompt = f"""Document the following database table for use in a SPARQL ontology.

RDF Class : {class_name}
DB Table  : {source_table}

Columns and sample values:
{columns_block}

Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{
  "table_description": "2-3 sentences describing what this table stores and what treasury/payments business questions it answers",
  "columns": {{
    "column_name": "one sentence describing what this column stores"
  }}
}}

Include every column listed above in the columns object."""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    })

    try:
        resp = bedrock.invoke_model(modelId=SONNET_ARN, body=body)
        raw = json.loads(resp["body"].read())
        text = raw["content"][0]["text"].strip()

        # strip any accidental markdown fences
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

        result = json.loads(text)
        # ensure all requested columns are present (fill blanks if LLM missed any)
        for col in columns:
            result["columns"].setdefault(col, f"Column {col} in {source_table}.")
        return result

    except Exception as e:
        print(f"    [warn] LLM call failed for {class_name}: {e}")
        return {
            "table_description": f"{class_name} records from {source_table}.",
            "columns": {col: f"Column {col}." for col in columns},
        }


# ---------------------------------------------------------------------------
# Enrichment run
# ---------------------------------------------------------------------------

def run_enrichment(model: dict, cache: dict, conn, bedrock) -> dict:
    classes = model.get("classes", {})
    total   = len(classes)
    done    = 0

    for cls_name, cls_info in classes.items():
        if cls_name in cache:
            done += 1
            continue

        source_table  = cls_info.get("source_table", "")
        sql_query     = cls_info.get("sql_query", "")
        raw_columns   = cls_info.get("columns", [])
        raw_rels      = cls_info.get("relationships", [])

        # unique db_columns from datatype properties for sampling
        db_cols_unique = list(dict.fromkeys(
            c["db_column"] for c in raw_columns if c.get("db_column")
        ))

        # all property names: datatype columns first, then object/relationship properties
        col_props = [c["property"] for c in raw_columns if c.get("property")]
        rel_props = [r["property"] for r in raw_rels
                     if r.get("property") and r["property"] not in col_props]
        all_props = col_props + rel_props

        # property → db_column (for sample lookup; relationships omitted — no literal column)
        prop_to_dbcol = {c["property"]: c.get("db_column", "") for c in raw_columns}

        done += 1
        print(f"[{done}/{total}] {cls_name}  ({source_table or 'no table'})")

        # sample using the SQL query if available (alias columns), else plain table
        if sql_query and conn:
            col_samples = sample_table_sql(conn, sql_query, db_cols_unique)
        elif source_table and conn:
            col_samples = sample_table(conn, source_table, db_cols_unique)
        else:
            col_samples = {}

        # per-property samples; relationship props get empty lists (no literal column)
        prop_samples = {
            prop: col_samples.get(prop_to_dbcol.get(prop, ""), [])
            for prop in all_props
        }

        # LLM — pass all property names + their sample values
        result = generate_descriptions(bedrock, cls_name, source_table, all_props, prop_samples)
        result["source_table"] = source_table
        prop_descs = result.get("columns", {})

        cache[cls_name] = result
        save_cache(cache)
        print(f"    saved ({len(prop_descs)} properties described)")

    return cache


# ---------------------------------------------------------------------------
# TTL generation
# ---------------------------------------------------------------------------

def build_enriched_ttl(cache: dict) -> None:
    print("\nBuilding enriched TTL ...")

    # load both source TTLs into one graph
    g = Graph()
    g.parse(ONTOLOGY_TTL, format="turtle")
    print(f"  loaded ontology:  {len(g)} triples")
    g.parse(R2RML_TTL, format="turtle")
    print(f"  loaded r2rml:     {len(g)} triples (merged)")

    # strip R2RML operational triples (rr:* predicates) — keep semantic content only
    rr_triples = list(g.triples((None, None, None)))
    removed = 0
    for s, p, o in rr_triples:
        if str(p).startswith(str(RR)) or str(s).startswith(str(RR)):
            g.remove((s, p, o))
            removed += 1
    print(f"  stripped {removed} R2RML triples")

    BRAIN = Namespace("https://lpp.example/ontology/brain#")
    PROV  = Namespace("http://www.w3.org/ns/prov#")

    def resolve_class_uri(name: str) -> URIRef:
        if name.startswith("brain:"):
            return BRAIN[name[6:]]
        if name.startswith("prov:"):
            return PROV[name[5:]]
        return LPP[name]

    def resolve_prop_uri(name: str) -> URIRef:
        if name.startswith("brain:"):
            return BRAIN[name[6:]]
        if name.startswith("lpp:"):
            return LPP[name[4:]]
        return LPP[name]

    # load semantic model to build prop → [class URIs] domain mapping
    with open(SEMANTIC_MODEL, encoding="utf-8") as _f:
        _model = yaml.safe_load(_f)

    prop_to_classes: dict[str, list[URIRef]] = {}   # prop URI → [domain class URIs]
    prop_to_ranges:  dict[str, list[URIRef]] = {}   # prop URI → [range class URIs]
    prop_is_object:  set[str]               = set() # prop URIs that are object properties

    for _cls_name, _cls_info in _model.get("classes", {}).items():
        _cls_uri = resolve_class_uri(_cls_name)
        for _col in _cls_info.get("columns", []):
            _p = _col.get("property", "")
            if _p:
                _pu = str(resolve_prop_uri(_p))
                if _cls_uri not in prop_to_classes.get(_pu, []):
                    prop_to_classes.setdefault(_pu, []).append(_cls_uri)
        for _rel in _cls_info.get("relationships", []):
            _p = _rel.get("property", "")
            if _p:
                _pu = str(resolve_prop_uri(_p))
                prop_is_object.add(_pu)
                if _cls_uri not in prop_to_classes.get(_pu, []):
                    prop_to_classes.setdefault(_pu, []).append(_cls_uri)
                _target = _rel.get("target", "")
                if _target and _target != "?":
                    _range_uri = resolve_class_uri(_target)
                    if _range_uri not in prop_to_ranges.get(_pu, []):
                        prop_to_ranges.setdefault(_pu, []).append(_range_uri)

    # accumulate property descriptions across tables (join with " | " if shared)
    prop_desc_parts: dict[str, list[str]] = {}
    for cls_info in cache.values():
        for prop_name, desc in cls_info.get("columns", {}).items():
            prop_desc_parts.setdefault(prop_name, [])
            if desc and desc not in prop_desc_parts[prop_name]:
                prop_desc_parts[prop_name].append(desc)

    # inject class descriptions — declare as owl:Class if not already present
    enriched_classes = 0
    for cls_name, cls_info in cache.items():
        table_desc = cls_info.get("table_description", "")
        if not table_desc:
            continue
        uri = resolve_class_uri(cls_name)
        if not any(True for _ in g.triples((uri, None, None))):
            g.add((uri, RDF.type, OWL.Class))
        g.set((uri, RDFS.comment, Literal(table_desc, lang="en")))
        enriched_classes += 1

    # inject property descriptions + rdfs:domain + rdfs:range
    enriched_props = 0
    domain_assertions = 0
    range_assertions = 0
    for prop_name, parts in prop_desc_parts.items():
        combined = " | ".join(parts)
        uri = resolve_prop_uri(prop_name)
        uri_str = str(uri)
        is_obj = uri_str in prop_is_object
        # declare property with correct type if not already in graph
        if not any(True for _ in g.triples((uri, None, None))):
            g.add((uri, RDF.type, OWL.ObjectProperty if is_obj else OWL.DatatypeProperty))
        g.set((uri, RDFS.comment, Literal(combined, lang="en")))
        # rdfs:domain — all classes that carry this property
        for cls_uri in prop_to_classes.get(uri_str, []):
            g.add((uri, RDFS.domain, cls_uri))
            domain_assertions += 1
        # rdfs:range — all target classes (object properties only)
        for range_uri in prop_to_ranges.get(uri_str, []):
            g.add((uri, RDFS.range, range_uri))
            range_assertions += 1
        enriched_props += 1

    print(f"  enriched {enriched_classes} classes, {enriched_props} properties")
    print(f"  domain assertions: {domain_assertions}  range assertions: {range_assertions}")

    # serialize
    g.serialize(destination=str(ENRICHED_TTL_OUT), format="turtle")
    print(f"  written -> {ENRICHED_TTL_OUT}  ({len(g)} triples)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ttl-only", action="store_true",
                        help="Skip sampling/LLM; rebuild TTL from existing cache")
    parser.add_argument("--reset", action="store_true",
                        help="Delete cache and start from scratch")
    args = parser.parse_args()

    if args.reset and CACHE_FILE.exists():
        CACHE_FILE.unlink()
        print("Cache deleted.")

    with open(SEMANTIC_MODEL, encoding="utf-8") as f:
        model = yaml.safe_load(f)

    cache = load_cache()
    already_done = len([k for k in model.get("classes", {}) if k in cache])
    total = len(model.get("classes", {}))
    print(f"Semantic model: {total} classes  |  Cache: {already_done} already done\n")

    if not args.ttl_only:
        # connect to Redshift
        conn = None
        try:
            print("Connecting to Redshift ...")
            conn = get_db_conn()
            print("  connected\n")
        except Exception as e:
            print(f"  [warn] Redshift connection failed: {e}")
            print("  Continuing without samples (LLM will use column names only)\n")

        bedrock = build_bedrock_client()
        cache   = run_enrichment(model, cache, conn, bedrock)

        if conn:
            conn.close()

    if not cache:
        print("Cache is empty — nothing to write to TTL. Run without --ttl-only first.")
        sys.exit(1)

    build_enriched_ttl(cache)
    print("\nDone.")


if __name__ == "__main__":
    main()
