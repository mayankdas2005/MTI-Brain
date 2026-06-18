"""
Standalone join-key statistical profiler.

Reads join keys from:
  1. YAML semantic model relationships
  2. Neo4j JOINS_TO edges
  3. Neo4j JoinPath nodes

Profiles each column pair against Redshift (via PgBouncer/psycopg2) and outputs
a flat JSON file with per-pair scores + safe/caution/dangerous verdict.

Usage:
    cd semantic_model_generator
    python profile_join_keys.py [--skip-neo4j] [--skip-redshift-cross] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import yaml

# ─── Config ──────────────────────────────────────────────────────────────────

# Read .env manually for Redshift (PgBouncer) and Neo4j creds
_ENV_FILE = Path(__file__).resolve().parent / ".env"
_BACKEND_ENV_FILE = Path(__file__).resolve().parent.parent / "backend" / ".env"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("profile_join_keys")

_YAML_PATH = Path(__file__).resolve().parent / "output" / "lpp_semantic_model_with_descriptions.yml"
_OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "join_key_profile.json"
_SCHEMA = "lpp"

# Join clause pattern: lpp.table_name.col_name = lpp.table_name.col_name
_CLAUSE_RE = re.compile(
    r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)"
    r"\s*=\s*"
    r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_$]*)"
)


def _load_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Strips quotes from values."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        env[key] = val
    return env


# ─── Redshift connection (psycopg2 via PgBouncer) ────────────────────────────

def _get_redshift_conn(env: dict[str, str]):
    """Create a psycopg2 connection using backend .env credentials (PgBouncer)."""
    return psycopg2.connect(
        host=env.get("REDSHIFT_HOST", "100.21.28.155"),
        dbname=env.get("REDSHIFT_DB", "dev"),
        user=env.get("REDSHIFT_USER", "admin"),
        password=env.get("REDSHIFT_PASSWORD", ""),
        port=int(env.get("REDSHIFT_PORT", "5433")),
        sslmode="disable",
        keepalives=1,
        keepalives_idle=60,
        keepalives_interval=10,
        keepalives_count=5,
        connect_timeout=15,
    )


def _fetch(conn, sql: str) -> list[dict]:
    """Execute SQL and return list of dicts."""
    cur = conn.cursor()
    try:
        cur.execute(sql)
        if not cur.description:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ─── Neo4j helpers ────────────────────────────────────────────────────────────

def _get_neo4j_driver(env: dict[str, str]):
    from neo4j import GraphDatabase
    uri = env.get("NEO4J_URI", "bolt://100.21.28.155:7687")
    user = env.get("NEO4J_USER", "neo4j")
    password = env.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


def _neo4j_query(driver, query: str, params: dict | None = None, db: str = "mtibraindev") -> list[dict]:
    with driver.session(database=db) as session:
        result = session.run(query, params or {})
        return [dict(record) for record in result]


# ─── Phase 1: Collect join keys ──────────────────────────────────────────────

def _parse_yaml_relationships(yaml_path: Path) -> list[dict]:
    """Extract (from_table, from_col, to_table, to_col) from YAML relationships."""
    log.info("Phase 1a: parsing YAML relationships from %s", yaml_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    pairs = []
    for rel in data.get("relationships", []):
        from_table = f"{_SCHEMA}.{rel['from_table']}"
        from_col = rel["from_column"]
        to_table = f"{_SCHEMA}.{rel['to_table']}"
        to_col = rel["to_column"]
        pairs.append({
            "from_table": from_table, "from_col": from_col,
            "to_table": to_table, "to_col": to_col,
            "source": "yaml",
        })
    log.info("  YAML: %d relationship pairs", len(pairs))
    return pairs


def _parse_neo4j_joins_to(driver, db: str) -> list[dict]:
    """Extract join keys from JOINS_TO edges."""
    log.info("Phase 1b: querying Neo4j JOINS_TO edges")
    query = """
    MATCH (t1:Table)-[r:JOINS_TO]->(t2:Table)
    RETURN r.from_table AS from_table, r.from_col AS from_col,
           r.to_table AS to_table, r.to_col AS to_col,
           r.confidence AS confidence, r.source AS source_tier
    """
    rows = _neo4j_query(driver, query, db=db)
    pairs = []
    for r in rows:
        if r.get("from_table") and r.get("from_col") and r.get("to_table") and r.get("to_col"):
            pairs.append({
                "from_table": r["from_table"], "from_col": r["from_col"],
                "to_table": r["to_table"], "to_col": r["to_col"],
                "source": "joins_to",
            })
    log.info("  JOINS_TO: %d edges", len(pairs))
    return pairs


def _parse_neo4j_join_paths(driver, db: str) -> list[dict]:
    """Extract join keys from JoinPath nodes by parsing join_clauses."""
    log.info("Phase 1c: querying Neo4j JoinPath nodes")
    query = """
    MATCH (jp:JoinPath)
    RETURN jp.join_clauses AS join_clauses
    """
    rows = _neo4j_query(driver, query, db=db)
    pairs = []
    for r in rows:
        clauses = r.get("join_clauses") or []
        for clause in clauses:
            m = _CLAUSE_RE.search(clause)
            if m:
                pairs.append({
                    "from_table": f"{m.group(1)}.{m.group(2)}",
                    "from_col": m.group(3),
                    "to_table": f"{m.group(4)}.{m.group(5)}",
                    "to_col": m.group(6),
                    "source": "join_path",
                })
    log.info("  JoinPath: %d clause pairs", len(pairs))
    return pairs


def _parse_joins_file(path: Path) -> list[dict]:
    """Load JOINS_TO edges exported from Neo4j browser as JSON.

    Expected format: [{"from_table": "...", "from_col": "...",
                       "to_table": "...",   "to_col": "..."}]
    Tables without a schema prefix get _SCHEMA prepended.
    """
    log.info("Phase 1d: loading joins file %s", path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    pairs = []
    for r in rows:
        ft = r.get("from_table", "")
        fc = r.get("from_col", "")
        tt = r.get("to_table", "")
        tc = r.get("to_col", "")
        if not (ft and fc and tt and tc):
            continue
        # Prepend schema if not already qualified (no dot in name)
        if "." not in ft:
            ft = f"{_SCHEMA}.{ft}"
        if "." not in tt:
            tt = f"{_SCHEMA}.{tt}"
        pairs.append({
            "from_table": ft, "from_col": fc,
            "to_table": tt,   "to_col": tc,
            "source": "joins_file",
        })
    log.info("  joins_file: %d edges loaded", len(pairs))
    return pairs


def _deduplicate(all_pairs: list[dict]) -> list[dict]:
    """Deduplicate by (from_table, from_col, to_table, to_col), merge sources."""
    seen: dict[tuple, dict] = {}
    for p in all_pairs:
        # Normalize key: sort the two sides so A→B and B→A are the same pair
        side_a = (p["from_table"], p["from_col"])
        side_b = (p["to_table"], p["to_col"])
        if side_a > side_b:
            side_a, side_b = side_b, side_a
            key = (*side_a, *side_b)
            entry_data = {
                "from_table": side_a[0], "from_col": side_a[1],
                "to_table": side_b[0], "to_col": side_b[1],
            }
        else:
            key = (*side_a, *side_b)
            entry_data = {
                "from_table": p["from_table"], "from_col": p["from_col"],
                "to_table": p["to_table"], "to_col": p["to_col"],
            }

        if key not in seen:
            seen[key] = {**entry_data, "sources": [p["source"]]}
        else:
            if p["source"] not in seen[key]["sources"]:
                seen[key]["sources"].append(p["source"])

    result = list(seen.values())
    log.info("  Deduplicated: %d unique pairs", len(result))
    return result


# ─── Phase 2: Column profiling ───────────────────────────────────────────────

def _collect_unique_columns(pairs: list[dict]) -> dict[str, list[str]]:
    """Group unique columns by table. Returns {table_fqn: [col1, col2, ...]}."""
    table_cols: dict[str, set[str]] = defaultdict(set)
    for p in pairs:
        table_cols[p["from_table"]].add(p["from_col"])
        table_cols[p["to_table"]].add(p["to_col"])
    return {t: sorted(cols) for t, cols in table_cols.items()}


def _profile_columns(conn, table_cols: dict[str, list[str]]) -> dict[tuple[str, str], dict]:
    """Run Q1 (types) + Q2 (basic stats) + Q3 (top-N freq) per table.

    Returns {(table_fqn, col_name): {stats dict}}.
    """
    stats: dict[tuple[str, str], dict] = {}
    total_tables = len(table_cols)

    for idx, (table_fqn, cols) in enumerate(table_cols.items(), 1):
        table_short = table_fqn.split(".", 1)[-1] if "." in table_fqn else table_fqn
        schema = table_fqn.split(".", 1)[0] if "." in table_fqn else _SCHEMA
        log.info("Phase 2 [%d/%d] profiling %s (%d cols)", idx, total_tables, table_fqn, len(cols))

        # ── Q_row: row count ─────────────────────────────────────────────
        try:
            row_count_rows = _fetch(conn, f'SELECT COUNT(*) AS row_count FROM {schema}."{table_short}"')
            row_count = int(row_count_rows[0]["row_count"]) if row_count_rows else 0
        except Exception as e:
            log.warning("  row_count FAILED for %s: %s", table_fqn, e)
            row_count = 0

        # ── Q1: data types ───────────────────────────────────────────────
        col_in = ", ".join(f"'{c}'" for c in cols)
        try:
            type_rows = _fetch(conn, f"""
                SELECT column_name, data_type
                FROM svv_columns
                WHERE table_schema = '{schema}'
                  AND table_name = '{table_short}'
                  AND column_name IN ({col_in})
            """)
            type_map = {r["column_name"]: r["data_type"] for r in type_rows}
        except Exception as e:
            log.warning("  Q1 (types) FAILED for %s: %s", table_fqn, e)
            type_map = {}

        # ── Q2: basic stats (UNION ALL per column) ───────────────────────
        union_parts = []
        for c in cols:
            union_parts.append(
                f"SELECT '{c}' AS col_name, "
                f'COUNT("{c}") AS non_null_count, '
                f'APPROXIMATE COUNT(DISTINCT "{c}") AS distinct_count '
                f'FROM {schema}."{table_short}"'
            )
        q2_sql = " UNION ALL ".join(union_parts)

        try:
            q2_rows = _fetch(conn, q2_sql)
        except Exception as e:
            log.warning("  Q2 (stats) FAILED for %s: %s", table_fqn, e)
            q2_rows = []

        q2_map: dict[str, dict] = {}
        for r in q2_rows:
            q2_map[r["col_name"]] = {
                "non_null_count": int(r.get("non_null_count") or 0),
                "distinct_count": int(r.get("distinct_count") or 0),
            }

        # ── Q3: top-N frequency (per column subquery) ────────────────────
        freq_parts = []
        for c in cols:
            freq_parts.append(
                f"SELECT * FROM ("
                f"SELECT '{c}' AS col_name, "
                f'"{c}"::varchar AS val, '
                f"COUNT(*) AS freq "
                f'FROM {schema}."{table_short}" '
                f'WHERE "{c}" IS NOT NULL '
                f'GROUP BY "{c}" '
                f"ORDER BY freq DESC LIMIT 10)"
            )
        q3_sql = " UNION ALL ".join(freq_parts) if freq_parts else None

        q3_map: dict[str, list[dict]] = defaultdict(list)
        if q3_sql:
            try:
                q3_rows = _fetch(conn, q3_sql)
                for r in q3_rows:
                    q3_map[r["col_name"]].append({
                        "val": str(r.get("val", "")),
                        "freq": int(r.get("freq") or 0),
                    })
            except Exception as e:
                log.warning("  Q3 (freq) FAILED for %s: %s", table_fqn, e)

        # ── Assemble per-column stats ────────────────────────────────────
        for c in cols:
            s = q2_map.get(c, {"non_null_count": 0, "distinct_count": 0})
            non_null = s["non_null_count"]
            distinct = s["distinct_count"]
            null_count = row_count - non_null
            all_null = (non_null == 0)

            uniqueness_ratio = round(distinct / row_count, 6) if row_count > 0 else 0.0
            null_ratio = round(null_count / row_count, 6) if row_count > 0 else 1.0
            multiplicity = round(non_null / distinct, 4) if distinct > 0 else None
            cardinality_ratio = round(distinct / row_count, 6) if row_count > 0 else 0.0

            # Dominance from Q3
            top_freqs = q3_map.get(c, [])
            dominance_ratio = round(top_freqs[0]["freq"] / row_count, 6) if top_freqs and row_count > 0 else None
            top_10_concentration = (
                round(sum(f["freq"] for f in top_freqs) / row_count, 6)
                if top_freqs and row_count > 0 else None
            )

            key_likelihood = round(0.7 * uniqueness_ratio - 0.3 * null_ratio, 6)

            stats[(table_fqn, c)] = {
                "data_type": type_map.get(c, "unknown"),
                "row_count": row_count,
                "non_null_count": non_null,
                "distinct_count": distinct,
                "null_count": null_count,
                "all_null": all_null,
                "uniqueness_ratio": uniqueness_ratio,
                "null_ratio": null_ratio,
                "multiplicity": multiplicity,
                "cardinality_ratio": cardinality_ratio,
                "dominance_ratio": dominance_ratio,
                "top_10_concentration": top_10_concentration,
                "key_likelihood": key_likelihood,
                "top_values": [f"{f['val']}:{f['freq']}" for f in top_freqs[:5]],
            }

    return stats


# ─── Phase 3: Cross-table analysis ───────────────────────────────────────────

def _profile_pairs(
    conn,
    pairs: list[dict],
    col_stats: dict[tuple[str, str], dict],
) -> list[dict]:
    """Run Q4 (combined cross-table query) per pair. Returns enriched pair list."""
    total = len(pairs)
    results = []

    for idx, p in enumerate(pairs, 1):
        from_table = p["from_table"]
        from_col = p["from_col"]
        to_table = p["to_table"]
        to_col = p["to_col"]

        from_short = from_table.split(".", 1)[-1]
        from_schema = from_table.split(".", 1)[0]
        to_short = to_table.split(".", 1)[-1]
        to_schema = to_table.split(".", 1)[0]

        fs = col_stats.get((from_table, from_col), {})
        ts = col_stats.get((to_table, to_col), {})

        log.info(
            "Phase 3 [%d/%d] %s.%s <-> %s.%s",
            idx, total, from_short, from_col, to_short, to_col,
        )

        # Short-circuit: either column is all-null → dangerous immediately
        from_all_null = fs.get("all_null", True)
        to_all_null = ts.get("all_null", True)

        if from_all_null or to_all_null:
            null_side = "from_col" if from_all_null else "to_col"
            null_table = from_table if from_all_null else to_table
            null_col = from_col if from_all_null else to_col
            null_rc = fs.get("row_count", 0) if from_all_null else ts.get("row_count", 0)
            null_nn = fs.get("non_null_count", 0) if from_all_null else ts.get("non_null_count", 0)

            results.append({
                **p,
                "from_col_stats": fs,
                "to_col_stats": ts,
                "cross_stats": None,
                "relationship_score": 0.0,
                "fanout_risk": "N/A",
                "verdict": "dangerous",
                "verdict_reason": (
                    f"{null_side} all_null ({null_nn}/{null_rc} non-null in "
                    f"{null_table}.{null_col}) — join produces 0 rows"
                ),
            })
            continue

        # ── Q4: combined cross-table query ───────────────────────────────
        q4_sql = f"""
        WITH a_stats AS (
            SELECT "{from_col}"::varchar AS v, COUNT(*) AS freq
            FROM {from_schema}."{from_short}"
            WHERE "{from_col}" IS NOT NULL
            GROUP BY "{from_col}"
        ),
        b_stats AS (
            SELECT "{to_col}"::varchar AS v, COUNT(*) AS freq
            FROM {to_schema}."{to_short}"
            WHERE "{to_col}" IS NOT NULL
            GROUP BY "{to_col}"
        ),
        overlap AS (
            SELECT a.v,
                   a.freq AS a_freq,
                   b.freq AS b_freq
            FROM a_stats a
            JOIN b_stats b ON a.v = b.v
        )
        SELECT
            (SELECT COUNT(*) FROM a_stats)     AS distinct_a,
            (SELECT COUNT(*) FROM b_stats)     AS distinct_b,
            COUNT(*)                           AS intersection_count,
            COALESCE(SUM(a_freq), 0)           AS matched_rows_a,
            COALESCE(SUM(b_freq), 0)           AS matched_rows_b,
            COALESCE(SUM(a_freq * b_freq), 0)  AS expected_join_rows
        FROM overlap
        """

        try:
            q4_rows = _fetch(conn, q4_sql)
        except Exception as e:
            log.warning("  Q4 FAILED for %s.%s <-> %s.%s: %s", from_short, from_col, to_short, to_col, e)
            results.append({
                **p,
                "from_col_stats": fs,
                "to_col_stats": ts,
                "cross_stats": None,
                "relationship_score": None,
                "fanout_risk": "UNKNOWN",
                "verdict": "caution",
                "verdict_reason": f"Q4 query failed: {e}",
            })
            continue

        if not q4_rows:
            results.append({
                **p,
                "from_col_stats": fs,
                "to_col_stats": ts,
                "cross_stats": None,
                "relationship_score": None,
                "fanout_risk": "UNKNOWN",
                "verdict": "caution",
                "verdict_reason": "Q4 returned no rows",
            })
            continue

        r = q4_rows[0]
        distinct_a = int(r.get("distinct_a") or 0)
        distinct_b = int(r.get("distinct_b") or 0)
        intersection = int(r.get("intersection_count") or 0)
        matched_rows_a = int(r.get("matched_rows_a") or 0)
        matched_rows_b = int(r.get("matched_rows_b") or 0)
        expected_join_rows = int(r.get("expected_join_rows") or 0)

        row_count_a = fs.get("row_count", 0)
        row_count_b = ts.get("row_count", 0)

        # ── Derived metrics ──────────────────────────────────────────────
        min_distinct = min(distinct_a, distinct_b) if distinct_a > 0 and distinct_b > 0 else 1
        max_distinct = max(distinct_a, distinct_b) if max(distinct_a, distinct_b) > 0 else 1

        overlap_ratio = round(intersection / min_distinct, 6) if min_distinct > 0 else 0.0
        containment_from_in_to = round(intersection / distinct_a, 6) if distinct_a > 0 else 0.0
        containment_to_in_from = round(intersection / distinct_b, 6) if distinct_b > 0 else 0.0
        selectivity = round(intersection / max_distinct, 6) if max_distinct > 0 else 0.0

        left_multiplicity = round(matched_rows_a / intersection, 4) if intersection > 0 else None
        right_multiplicity = round(matched_rows_b / intersection, 4) if intersection > 0 else None

        left_coverage = round(matched_rows_a / row_count_a, 6) if row_count_a > 0 else 0.0
        right_coverage = round(matched_rows_b / row_count_b, 6) if row_count_b > 0 else 0.0

        # Fanout score
        if left_multiplicity is not None and right_multiplicity is not None:
            fanout_score = round(left_multiplicity * right_multiplicity, 4)
        else:
            fanout_score = None

        # Relationship type
        rel_type = _classify_relationship(left_multiplicity, right_multiplicity)

        cross_stats = {
            "distinct_a": distinct_a,
            "distinct_b": distinct_b,
            "intersection_count": intersection,
            "overlap_ratio": overlap_ratio,
            "containment_from_in_to": containment_from_in_to,
            "containment_to_in_from": containment_to_in_from,
            "left_multiplicity": left_multiplicity,
            "right_multiplicity": right_multiplicity,
            "relationship_type": rel_type,
            "fanout_score": fanout_score,
            "selectivity": selectivity,
            "left_coverage": left_coverage,
            "right_coverage": right_coverage,
            "matched_rows_a": matched_rows_a,
            "matched_rows_b": matched_rows_b,
            "expected_join_rows": expected_join_rows,
        }

        # ── Phase 4: Scoring ─────────────────────────────────────────────
        relationship_score = _compute_relationship_score(
            overlap_ratio, containment_from_in_to, containment_to_in_from,
            selectivity, left_coverage, right_coverage,
            fs.get("key_likelihood", 0), ts.get("key_likelihood", 0),
        )
        fanout_risk, fanout_label = _compute_fanout_risk(fanout_score, expected_join_rows, row_count_a, row_count_b)
        verdict, verdict_reason = _compute_verdict(
            relationship_score, fanout_label, overlap_ratio, intersection,
            from_all_null, to_all_null,
        )

        results.append({
            **p,
            "from_col_stats": fs,
            "to_col_stats": ts,
            "cross_stats": cross_stats,
            "relationship_score": relationship_score,
            "fanout_risk": fanout_label,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
        })

    return results


# ─── Scoring helpers ─────────────────────────────────────────────────────────

def _classify_relationship(left_mult: float | None, right_mult: float | None) -> str:
    if left_mult is None or right_mult is None:
        return "UNKNOWN"
    if left_mult <= 1.05 and right_mult <= 1.05:
        return "1:1"
    if left_mult <= 1.05:
        return "1:N"
    if right_mult <= 1.05:
        return "N:1"
    return "M:N"


def _compute_relationship_score(
    overlap_ratio: float,
    containment_a: float,
    containment_b: float,
    selectivity: float,
    left_coverage: float,
    right_coverage: float,
    key_likelihood_a: float,
    key_likelihood_b: float,
) -> float:
    score = (
        0.30 * overlap_ratio
        + 0.25 * max(containment_a, containment_b)
        + 0.20 * selectivity
        + 0.15 * max(left_coverage, right_coverage)
        + 0.10 * max(0, (key_likelihood_a + key_likelihood_b) / 2)
    )
    return round(min(max(score, 0.0), 1.0), 4)


def _compute_fanout_risk(
    fanout_score: float | None,
    expected_join_rows: int,
    row_count_a: int,
    row_count_b: int,
) -> tuple[float | None, str]:
    if fanout_score is None:
        return None, "UNKNOWN"

    # Also check expected_join_rows vs max input table
    max_input = max(row_count_a, row_count_b, 1)
    expansion_ratio = expected_join_rows / max_input if max_input > 0 else 0

    # Use the worse of fanout_score and expansion_ratio
    effective = max(fanout_score, expansion_ratio)

    if effective < 5:
        return round(effective, 4), "LOW"
    if effective < 50:
        return round(effective, 4), "MEDIUM"
    if effective < 500:
        return round(effective, 4), "HIGH"
    return round(effective, 4), "CRITICAL"


def _compute_verdict(
    relationship_score: float | None,
    fanout_label: str,
    overlap_ratio: float,
    intersection: int,
    from_all_null: bool,
    to_all_null: bool,
) -> tuple[str, str | None]:
    # Dangerous conditions
    if from_all_null or to_all_null:
        side = "from_col" if from_all_null else "to_col"
        return "dangerous", f"{side} is all NULL — join produces 0 rows"

    if intersection == 0:
        return "dangerous", "zero value overlap — join produces 0 rows"

    if fanout_label == "CRITICAL":
        return "dangerous", f"critical fan-out risk (fanout_score in CRITICAL range)"

    if relationship_score is not None and relationship_score < 0.3:
        return "dangerous", f"very low relationship_score ({relationship_score})"

    # Safe conditions
    if (relationship_score is not None and relationship_score >= 0.7
            and fanout_label in ("LOW", "MEDIUM")):
        return "safe", None

    # Everything else is caution
    reasons = []
    if relationship_score is not None and relationship_score < 0.7:
        reasons.append(f"moderate relationship_score ({relationship_score})")
    if fanout_label in ("HIGH",):
        reasons.append(f"high fan-out risk")
    if fanout_label == "UNKNOWN":
        reasons.append("fan-out could not be assessed")

    return "caution", "; ".join(reasons) if reasons else None


# ─── Phase 5: Output ─────────────────────────────────────────────────────────

def _build_output(profiles: list[dict]) -> dict:
    summary = {"safe": 0, "caution": 0, "dangerous": 0}
    for p in profiles:
        v = p.get("verdict", "caution")
        summary[v] = summary.get(v, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_pairs": len(profiles),
        "summary": summary,
        "profiles": profiles,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Profile join key quality")
    parser.add_argument("--skip-neo4j", action="store_true", help="Skip Neo4j queries, use YAML only")
    parser.add_argument("--joins-file", type=str, default=None,
                        help="Path to Neo4j-exported JOINS_TO JSON file (supplements Neo4j live query)")
    parser.add_argument("--output", type=str, default=str(_OUTPUT_PATH), help="Output JSON path")
    args = parser.parse_args()

    t0 = time.time()

    # Load env from both locations (backend has PgBouncer config)
    env = _load_env(_ENV_FILE)
    backend_env = _load_env(_BACKEND_ENV_FILE)
    # Merge: prefer backend .env for Redshift (PgBouncer), semantic_model .env for Neo4j
    merged_env = {**env, **backend_env}

    # ── Phase 1: Collect join keys ────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 1: Collecting join keys")
    log.info("=" * 60)

    all_pairs: list[dict] = []

    # 1a: YAML
    yaml_pairs = _parse_yaml_relationships(_YAML_PATH)
    all_pairs.extend(yaml_pairs)

    # 1b + 1c: Neo4j live
    if not args.skip_neo4j:
        neo4j_db = merged_env.get("NEO4J_DB", "mtibraindev")
        try:
            driver = _get_neo4j_driver(merged_env)
            joins_to_pairs = _parse_neo4j_joins_to(driver, neo4j_db)
            join_path_pairs = _parse_neo4j_join_paths(driver, neo4j_db)
            all_pairs.extend(joins_to_pairs)
            all_pairs.extend(join_path_pairs)
            driver.close()
        except Exception as e:
            log.warning("Neo4j connection failed, continuing with YAML only: %s", e)
    else:
        log.info("  Skipping Neo4j (--skip-neo4j)")

    # 1d: optional exported JSON file
    if args.joins_file:
        file_pairs = _parse_joins_file(Path(args.joins_file))
        all_pairs.extend(file_pairs)

    # Deduplicate
    pairs = _deduplicate(all_pairs)

    # ── Phase 2: Column profiling ─────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 2: Column profiling against Redshift")
    log.info("=" * 60)

    table_cols = _collect_unique_columns(pairs)
    log.info("Unique tables: %d, unique (table, col) pairs: %d",
             len(table_cols), sum(len(v) for v in table_cols.values()))

    conn = _get_redshift_conn(merged_env)
    log.info("Connected to Redshift via psycopg2 (host=%s port=%s)",
             merged_env.get("REDSHIFT_HOST"), merged_env.get("REDSHIFT_PORT"))

    try:
        col_stats = _profile_columns(conn, table_cols)
        log.info("Column profiling complete: %d columns profiled", len(col_stats))

        # ── Phase 3 + 4: Cross-table analysis + scoring ──────────────────
        log.info("=" * 60)
        log.info("PHASE 3+4: Cross-table analysis + scoring")
        log.info("=" * 60)

        profiles = _profile_pairs(conn, pairs, col_stats)
    finally:
        conn.close()
        log.info("Redshift connection closed")

    # ── Phase 5: Output ──────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 5: Writing output")
    log.info("=" * 60)

    output = _build_output(profiles)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    elapsed = time.time() - t0
    log.info("Done in %.1f seconds", elapsed)
    log.info("Output: %s", output_path)
    log.info("Summary: %s", output["summary"])
    log.info("  safe: %d | caution: %d | dangerous: %d",
             output["summary"]["safe"], output["summary"]["caution"], output["summary"]["dangerous"])


if __name__ == "__main__":
    main()
