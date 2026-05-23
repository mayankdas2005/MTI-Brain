"""
All Redshift metadata queries (Q1–Q16).
Returns typed dicts keyed by table_name / column identifiers.
Uses redshift_connector (official Amazon driver).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import redshift_connector

log = logging.getLogger(__name__)

_SCHEMA = "lpp"


def _in_list(names: set[str]) -> str:
    """Return SQL IN literal: ('a', 'b', 'c') — safe for identifier values (table/col names)."""
    escaped = ", ".join(f"'{n}'" for n in sorted(names))
    return f"({escaped})"


@contextmanager
def _conn(host: str, database: str, user: str, password: str, port: int = 5439):
    con = redshift_connector.connect(
        host=host,
        database=database,
        user=user,
        password=password,
        port=port,
    )
    try:
        yield con
    finally:
        con.close()


def _fetch(con, sql: str, params=None) -> list[dict]:
    cur = con.cursor()
    try:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()


class RedshiftExtractor:
    def __init__(self, host: str, database: str, user: str, password: str,
                 port: int = 5439, schema: str = _SCHEMA):
        self.host = host
        self.database = database
        self.user = user
        self.password = password
        self.port = port
        self.schema = schema

    def run_all(
        self,
        table_names: set[str] | None = None,
        col_names: set[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        log.info("Connecting to Redshift …")
        tbl_filter = table_names or set()
        col_filter = col_names or set()
        if tbl_filter:
            log.info("Scoped to %d YML tables, %d YML columns", len(tbl_filter), len(col_filter))
        with _conn(self.host, self.database, self.user, self.password, self.port) as con:
            log.info("q1  tables …")
            tables = self.q1_tables(con, tbl_filter)
            log.info("q1  done: %d tables", len(tables))

            log.info("q2  columns …")
            columns = self.q2_columns(con, tbl_filter, col_filter)
            log.info("q2  done: %d columns", len(columns))

            log.info("q3  pg_stats …")
            pg_stats = self.q3_pg_stats(con, tbl_filter)
            log.info("q3  done: %d stat rows", len(pg_stats))

            log.info("q4  constraints …")
            constraints = self.q4_constraints(con, tbl_filter)
            log.info("q4  done: %d rows", len(constraints))

            log.info("q5  key_cols …")
            key_cols = self.q5_key_cols(con, tbl_filter)
            log.info("q5  done: %d rows", len(key_cols))

            log.info("q6  views …")
            views = self.q6_views(con)
            log.info("q6  done: %d views", len(views))

            log.info("q7  stl_recent …")
            stl_recent = self.q7_stl_recent(con)
            log.info("q7  done: %d rows", len(stl_recent))

            log.info("q9  col_type_dist …")
            col_type_dist = self.q9_col_type_dist(con, tbl_filter)
            log.info("q9  done: %d rows", len(col_type_dist))

            log.info("q10 cardinality …")
            cardinality = self.q10_cardinality(con, tbl_filter)
            log.info("q10 done: %d rows", len(cardinality))

            log.info("q11 encoding …")
            encoding = self.q11_encoding(con, tbl_filter)
            log.info("q11 done: %d rows", len(encoding))

            log.info("q12 stl_joins …")
            stl_joins = self.q12_stl_joins(con)
            log.info("q12 done: %d rows", len(stl_joins))

            log.info("q13 cross_schema …")
            cross_schema = self.q13_cross_schema(con)
            log.info("q13 done: %d rows", len(cross_schema))

            log.info("q14 views_lpp_ref …")
            views_lpp_ref = self.q14_views_lpp_ref(con)
            log.info("q14 done: %d rows", len(views_lpp_ref))

            log.info("q15 shared_cols …")
            shared_cols = self.q15_shared_cols(con, tbl_filter)
            log.info("q15 done: %d rows", len(shared_cols))

            log.info("q16 never_joined …")
            never_joined = self.q16_never_joined(con)
            log.info("q16 done: %d rows", len(never_joined))

            log.info("q8  samples (per-table) …")
            samples = self.q8_samples(con, tbl_filter, col_filter)
            log.info("q8  done: %d tables with samples", len(samples))

            log.info("top_freq_values (per-table) …")
            top_freq_values = self.get_top_frequent_values(con, pg_stats, columns, tbl_filter, col_filter)
            log.info("top_freq done: %d tables", len(top_freq_values))

            result = {
                "tables":          tables,
                "columns":         columns,
                "pg_stats":        pg_stats,
                "constraints":     constraints,
                "key_cols":        key_cols,
                "views":           views,
                "stl_recent":      stl_recent,
                "col_type_dist":   col_type_dist,
                "cardinality":     cardinality,
                "encoding":        encoding,
                "stl_joins":       stl_joins,
                "cross_schema":    cross_schema,
                "views_lpp_ref":   views_lpp_ref,
                "shared_cols":     shared_cols,
                "never_joined":    never_joined,
                "samples":         samples,
                "top_freq_values": top_freq_values,
            }
        log.info("Redshift extraction complete.")
        return result

    def q1_tables(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND \"table\" IN {_in_list(tbl_filter)}" if tbl_filter else ""
        # SVV views must be queried separately from pg_* catalog tables in Redshift.
        rows = _fetch(con, f"""
            SELECT
                "table"      AS table_name,
                diststyle,
                sortkey1,
                sortkey_num  AS sortkey_count,
                tbl_rows     AS row_count,
                size         AS size_mb,
                unsorted     AS pct_unsorted,
                stats_off
            FROM svv_table_info
            WHERE schema = '{self.schema}'
            {tbl_clause}
            ORDER BY "table"
        """)

        tbl_pg_clause = f"AND tablename IN {_in_list(tbl_filter)}" if tbl_filter else ""
        key_rows = _fetch(con, f"""
            SELECT tablename, "column", type, distkey, sortkey
            FROM pg_table_def
            WHERE schemaname = '{self.schema}'
              AND (distkey = true OR sortkey = 1)
            {tbl_pg_clause}
        """)

        distkey_map: dict[str, str] = {}
        sortkey1_type_map: dict[str, str] = {}
        for r in key_rows:
            tbl = r["tablename"]
            if r["distkey"]:
                distkey_map[tbl] = r["column"]
            if r["sortkey"] == 1:
                sortkey1_type_map[tbl] = r["type"]

        type_rows = _fetch(con, f"""
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = '{self.schema}'
        """)
        table_type_map: dict[str, str] = {
            r["table_name"]: r.get("table_type", "BASE TABLE") for r in type_rows
        }

        for row in rows:
            tbl = row["table_name"]
            row["distkey_col"]   = distkey_map.get(tbl, "")
            row["sortkey1_type"] = sortkey1_type_map.get(tbl, "")
            row["table_comment"] = ""
            row["table_type_db"] = table_type_map.get(tbl, "BASE TABLE")

        return rows

    def q2_columns(self, con, tbl_filter: set[str] = frozenset(), col_filter: set[tuple[str, str]] = frozenset()) -> list[dict]:
        tbl_clause = f"AND table_name IN {_in_list(tbl_filter)}" if tbl_filter else ""
        rows = _fetch(con, f"""
            SELECT
                table_name,
                column_name,
                ordinal_position,
                data_type,
                character_maximum_length AS max_length,
                numeric_precision,
                numeric_scale,
                is_nullable,
                column_default
            FROM svv_columns
            WHERE table_schema = '{self.schema}'
            {tbl_clause}
            ORDER BY table_name, ordinal_position
        """)
        if col_filter:
            rows = [r for r in rows if (r["table_name"], r["column_name"]) in col_filter]

        tbl_pg_clause = f"AND tablename IN {_in_list(tbl_filter)}" if tbl_filter else ""
        pg_rows = _fetch(con, f"""
            SELECT tablename, "column" AS column_name,
                   distkey, sortkey, "notnull", encoding
            FROM pg_table_def
            WHERE schemaname = '{self.schema}'
            {tbl_pg_clause}
        """)

        pg_map: dict[tuple, dict] = {}
        for r in pg_rows:
            pg_map[(r["tablename"], r["column_name"])] = r

        for row in rows:
            key = (row["table_name"], row["column_name"])
            pg = pg_map.get(key, {})
            row["col_comment"] = ""
            row["is_distkey"]  = bool(pg.get("distkey", False))
            row["sortkey_pos"] = int(pg.get("sortkey", 0) or 0)
            row["is_notnull"]  = bool(pg.get("notnull", False))
            row["encoding"]    = pg.get("encoding", "none") or "none"

        return rows

    def q3_pg_stats(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        # most_common_vals / histogram_bounds are anyarray — not castable in Redshift.
        # We use only the scalar statistics; sample values come from Q8.
        tbl_clause = f"AND tablename IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT
                tablename  AS table_name,
                attname    AS column_name,
                null_frac,
                avg_width,
                n_distinct,
                correlation
            FROM pg_stats
            WHERE schemaname = '{self.schema}'
            {tbl_clause}
            ORDER BY tablename, attname
        """)

    def q4_constraints(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND tc.table_name IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT
                tc.constraint_type,
                tc.table_name,
                kcu.column_name,
                ccu.table_name  AS ref_table,
                ccu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name
             AND kcu.table_schema    = tc.table_schema
            LEFT JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            WHERE tc.table_schema   = '{self.schema}'
              AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
            {tbl_clause}
            ORDER BY tc.table_name, tc.constraint_type
        """)

    def q5_key_cols(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND tablename IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT tablename AS table_name, "column" AS column_name,
                   type AS col_type, encoding, distkey, sortkey, "notnull"
            FROM pg_table_def
            WHERE schemaname = '{self.schema}'
              AND (distkey = true OR sortkey > 0)
            {tbl_clause}
            ORDER BY tablename, sortkey, distkey DESC
        """)

    def q6_views(self, con) -> list[dict]:
        return _fetch(con, f"""
            SELECT table_name AS view_name, view_definition
            FROM information_schema.views
            WHERE table_schema = '{self.schema}'
            ORDER BY table_name
        """)

    def q7_stl_recent(self, con) -> list[dict]:
        try:
            return _fetch(con, f"""
                SELECT DISTINCT trim(text) AS query_fragment
                FROM stl_querytext
                WHERE text ILIKE '%{self.schema}.%'
                  AND text ILIKE '%join%'
                LIMIT 2000
            """)
        except Exception as e:
            log.warning("q7_stl_recent failed (permission?): %s", e)
            return []

    def q8_samples(
        self,
        con,
        tbl_filter: set[str] = frozenset(),
        col_filter: set[tuple[str, str]] = frozenset(),
    ) -> dict[str, dict[str, list]]:
        """
        Returns {table_name: {col_name: [val, ...]}} for qualifying columns.
        Only processes tables/columns present in tbl_filter/col_filter when provided.

        Two paths:
        - Low-cardinality (2 <= n_distinct <= 50): multi-column SELECT DISTINCT LIMIT 50
          (safe — the low n_distinct guarantees Redshift can hash-distinct quickly).
        - Short-varchar with n_distinct < 0 (e.g. code cols in small lookup tables):
          single-column SELECT … LIMIT 100, deduplicated in Python. No DISTINCT keyword
          so Redshift does a minimal block read and stops — avoids the full-sort hang.
        """
        stats = self.q3_pg_stats(con, tbl_filter)

        tbl_clause = f"AND table_name IN {_in_list(tbl_filter)}" if tbl_filter else ""
        user_col_rows = _fetch(con, f"""
            SELECT table_name, column_name, data_type
            FROM svv_columns
            WHERE table_schema = '{self.schema}'
            {tbl_clause}
        """)
        user_cols = {(r["table_name"], r["column_name"]) for r in user_col_rows}
        if col_filter:
            user_cols &= col_filter
        col_type_map = {
            (r["table_name"], r["column_name"]): (r.get("data_type") or "").lower()
            for r in user_col_rows
        }
        _VARCHAR_SUBTYPES = ("char", "text", "string", "nvarchar")

        stats_map: dict[tuple[str, str], dict] = {
            (r["table_name"], r["column_name"]): r for r in stats
        }

        low_card: dict[str, list[str]] = {}   # 2 <= n_distinct <= 50
        varchar_neg: dict[str, list[str]] = {}  # n_distinct < 0, short varchar

        for (tbl, col), stat in stats_map.items():
            if (tbl, col) not in user_cols:
                continue
            nd    = float(stat.get("n_distinct") or 0)
            avg_w = float(stat.get("avg_width") or 0)
            ctype = col_type_map.get((tbl, col), "")
            is_varchar = any(vt in ctype for vt in _VARCHAR_SUBTYPES)

            if 2 <= nd <= 50:
                low_card.setdefault(tbl, []).append(col)
            elif nd < 0 and is_varchar and avg_w <= 30:
                varchar_neg.setdefault(tbl, []).append(col)

        result: dict[str, dict[str, list]] = {}

        total_low = len(low_card)
        # Path 1 — original: multi-col SELECT DISTINCT (safe for truly low-cardinality cols)
        for idx, (tbl, cols) in enumerate(low_card.items(), 1):
            sel_cols = cols[:5]
            col_list = ", ".join(f'"{c}"' for c in sel_cols)
            try:
                rows = _fetch(con, f"""
                    SELECT DISTINCT {col_list}
                    FROM {self.schema}.{tbl}
                    WHERE {sel_cols[0]} IS NOT NULL
                    LIMIT 50
                """)
                tbl_samples: dict[str, list] = {c: [] for c in sel_cols}
                for r in rows:
                    for c in sel_cols:
                        v = r.get(c)
                        if v is not None and v not in tbl_samples[c]:
                            tbl_samples[c].append(str(v))
                result[tbl] = tbl_samples
                log.info("q8 low-card [%d/%d] %s — %d cols, %d rows", idx, total_low, tbl, len(sel_cols), len(rows))
            except Exception as e:
                log.warning("q8 low-card [%d/%d] %s — FAILED: %s", idx, total_low, tbl, e)

        total_neg = len(varchar_neg)
        # Path 2 — one query per table: SELECT top 200 rows, deduplicate in Python.
        # No DISTINCT, no GROUP BY, no ORDER BY — just a minimal block read. Fast on any
        # table size because Redshift stops reading after the first ~200 rows stored.
        for idx, (tbl, cols) in enumerate(varchar_neg.items(), 1):
            tbl_result = result.get(tbl, {})
            new_cols = [c for c in cols[:10] if c not in tbl_result]
            if not new_cols:
                log.info("q8 varchar-neg [%d/%d] %s — skipped (already sampled)", idx, total_neg, tbl)
                continue
            col_list = ", ".join(f'"{c}"' for c in new_cols)
            try:
                rows = _fetch(con, f"""
                    SELECT {col_list}
                    FROM {self.schema}.{tbl}
                    LIMIT 200
                """)
                per_col: dict[str, list] = {c: [] for c in new_cols}
                seen_per_col: dict[str, set] = {c: set() for c in new_cols}
                for r in rows:
                    for c in new_cols:
                        v = r.get(c)
                        if v is None:
                            continue
                        sv = str(v)
                        if sv not in seen_per_col[c] and len(per_col[c]) < 20:
                            seen_per_col[c].add(sv)
                            per_col[c].append(sv)
                for c in new_cols:
                    if per_col[c]:
                        tbl_result[c] = per_col[c]
                filled = sum(1 for c in new_cols if per_col[c])
                log.info("q8 varchar-neg [%d/%d] %s — %d/%d cols filled from %d rows",
                         idx, total_neg, tbl, filled, len(new_cols), len(rows))
            except Exception as e:
                log.warning("q8 varchar-neg [%d/%d] %s — FAILED: %s", idx, total_neg, tbl, e)
            if tbl_result:
                result[tbl] = tbl_result

        return result

    def q9_col_type_dist(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND table_name IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT
                table_name,
                COUNT(*)    AS total_cols,
                SUM(CASE WHEN data_type IN (
                        'integer','bigint','smallint','int','int2','int4','int8',
                        'numeric','decimal','float','float4','float8',
                        'real','double precision'
                    ) THEN 1 ELSE 0 END)                AS numeric_cols,
                SUM(CASE WHEN data_type ILIKE '%char%'
                          OR data_type IN ('text','string','nvarchar','bpchar')
                    THEN 1 ELSE 0 END)                  AS varchar_cols,
                SUM(CASE WHEN data_type ILIKE '%timestamp%'
                          OR data_type = 'date'
                    THEN 1 ELSE 0 END)                  AS date_cols,
                SUM(CASE WHEN data_type IN ('boolean','bool')
                    THEN 1 ELSE 0 END)                  AS bool_cols,
                AVG(character_maximum_length)            AS avg_varchar_len
            FROM svv_columns
            WHERE table_schema = '{self.schema}'
            {tbl_clause}
            GROUP BY table_name
            ORDER BY table_name
        """)

    def q10_cardinality(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND tablename IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT
                tablename                                           AS table_name,
                COUNT(*)                                            AS analyzed_col_count,
                ROUND(AVG(null_frac)::numeric, 4)                   AS avg_null_frac,
                ROUND(MAX(null_frac)::numeric, 4)                   AS max_null_frac,
                SUM(CASE WHEN n_distinct = -1.0     THEN 1 ELSE 0 END)  AS unique_col_count,
                SUM(CASE WHEN n_distinct < -0.01
                          AND n_distinct > -0.99    THEN 1 ELSE 0 END)  AS rel_high_card_count,
                SUM(CASE WHEN n_distinct > 0
                          AND n_distinct <= 50      THEN 1 ELSE 0 END)  AS low_card_col_count,
                SUM(CASE WHEN n_distinct > 50
                          AND n_distinct <= 10000   THEN 1 ELSE 0 END)  AS mid_card_col_count
            FROM pg_stats
            WHERE schemaname = '{self.schema}'
            {tbl_clause}
            GROUP BY tablename
            ORDER BY tablename
        """)

    def q11_encoding(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND tablename IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT
                tablename                                           AS table_name,
                COUNT(*)                                            AS total_cols,
                SUM(CASE WHEN encoding NOT IN ('none','raw','')
                    THEN 1 ELSE 0 END)                              AS encoded_col_count,
                ROUND(
                    100.0 * SUM(CASE WHEN encoding NOT IN ('none','raw','')
                                THEN 1 ELSE 0 END) / COUNT(*), 1
                )                                                   AS pct_encoded
            FROM pg_table_def
            WHERE schemaname = '{self.schema}'
            {tbl_clause}
            GROUP BY tablename
            ORDER BY tablename
        """)

    def q12_stl_joins(self, con) -> list[dict]:
        try:
            return _fetch(con, f"""
                SELECT
                    q.query                                             AS query_id,
                    LISTAGG(TRIM(qt.text), '')
                        WITHIN GROUP (ORDER BY qt.sequence)             AS full_query_text
                FROM stl_query q
                JOIN stl_querytext qt ON qt.query = q.query
                WHERE q.database  = current_database()
                  AND q.aborted   = 0
                  AND q.starttime > DATEADD(day, -180, GETDATE())
                GROUP BY q.query
                HAVING LISTAGG(TRIM(qt.text), '')
                           WITHIN GROUP (ORDER BY qt.sequence) ILIKE '%{self.schema}.%'
                   AND LISTAGG(TRIM(qt.text), '')
                           WITHIN GROUP (ORDER BY qt.sequence) ILIKE '%join%'
                ORDER BY q.query
                LIMIT 10000
            """)
        except Exception as e:
            log.warning("q12_stl_joins failed (permission?): %s — falling back to q7", e)
            return []

    def q13_cross_schema(self, con) -> list[dict]:
        return _fetch(con, f"""
            SELECT
                kcu.table_schema  AS from_schema,
                kcu.table_name    AS from_table,
                kcu.column_name   AS from_col,
                ccu.table_schema  AS to_schema,
                ccu.table_name    AS to_table,
                ccu.column_name   AS to_col,
                tc.constraint_type
            FROM information_schema.table_constraints  tc
            JOIN information_schema.key_column_usage   kcu
                ON kcu.constraint_name = tc.constraint_name
               AND kcu.table_schema   = tc.table_schema
            LEFT JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type IN ('FOREIGN KEY','PRIMARY KEY')
              AND (tc.table_schema = '{self.schema}' OR ccu.table_schema = '{self.schema}')
            ORDER BY from_schema, from_table
        """)

    def q14_views_lpp_ref(self, con) -> list[dict]:
        try:
            return _fetch(con, f"""
                SELECT schemaname AS view_schema, viewname AS view_name, definition AS view_sql
                FROM pg_views
                WHERE definition ILIKE '%{self.schema}.%'
                ORDER BY schemaname, viewname
            """)
        except Exception as e:
            log.warning("q14 failed: %s", e)
            return []

    def q15_shared_cols(self, con, tbl_filter: set[str] = frozenset()) -> list[dict]:
        tbl_clause = f"AND table_name IN {_in_list(tbl_filter)}" if tbl_filter else ""
        return _fetch(con, f"""
            SELECT
                column_name,
                data_type,
                COUNT(table_name)                      AS appears_in_n_tables,
                LISTAGG(table_name, ',')
                    WITHIN GROUP (ORDER BY table_name) AS tables
            FROM svv_columns
            WHERE table_schema = '{self.schema}'
              AND data_type IN (
                  'integer','bigint','smallint','int','int2','int4','int8',
                  'character varying','varchar','char','text'
              )
            {tbl_clause}
            GROUP BY column_name, data_type
            HAVING COUNT(table_name) >= 2
            ORDER BY appears_in_n_tables DESC, column_name
        """)

    def q16_never_joined(self, con) -> list[dict]:
        try:
            return _fetch(con, f"""
                WITH all_tables AS (
                    SELECT table_name FROM svv_tables
                    WHERE table_schema = '{self.schema}' AND table_type = 'BASE TABLE'
                ),
                joined_tables AS (
                    SELECT DISTINCT TRIM(LOWER(
                        REGEXP_SUBSTR(LOWER(text), '{self.schema}\\.([a-z0-9_]+)')
                    )) AS table_name
                    FROM stl_querytext
                    WHERE text ILIKE '%{self.schema}.%' AND text ILIKE '%join%'
                )
                SELECT
                    a.table_name,
                    CASE WHEN j.table_name IS NULL THEN 'never_joined' ELSE 'has_joins' END AS join_status
                FROM all_tables a
                LEFT JOIN joined_tables j ON j.table_name = a.table_name
                ORDER BY join_status, a.table_name
            """)
        except Exception as e:
            log.warning("q16 failed: %s", e)
            return []

    def get_top_frequent_values(
        self,
        con,
        pg_stats: list[dict],
        columns: list[dict],
        tbl_filter: set[str] = frozenset(),
        col_filter: set[tuple[str, str]] = frozenset(),
        max_distinct: int = 50,
        top_n: int = 20,
    ) -> dict[str, dict[str, list[str]]]:
        """
        Returns {table_name: {col_name: ["val:count", ...]}} for low-cardinality columns.
        Uses UNION ALL + GROUP BY COUNT(*) ORDER BY freq DESC — frequency-ranked, not random.
        Covers columns where 1 < n_distinct <= max_distinct only.
        Columns with n_distinct < 0 are handled by q8_samples (LIMIT 200, no sort/group).
        Only processes tables/columns present in tbl_filter/col_filter when provided.
        """
        _SKIP_FREQ_TYPES = {"boolean", "bool", "hllsketch", "super", "geometry"}
        col_type_map: dict[tuple[str, str], str] = {
            (r["table_name"], r["column_name"]): (r.get("data_type") or "").lower()
            for r in columns
        }
        user_cols: set[tuple[str, str]] = set(col_type_map.keys())
        if col_filter:
            user_cols &= col_filter

        candidates: dict[str, list[str]] = {}
        for row in pg_stats:
            nd  = float(row.get("n_distinct") or 0)
            tbl = row["table_name"]
            col = row["column_name"]
            if tbl_filter and tbl not in tbl_filter:
                continue
            if (tbl, col) not in user_cols:
                continue
            ctype = col_type_map.get((tbl, col), "")
            if ctype in _SKIP_FREQ_TYPES:
                continue
            if 1 < nd <= max_distinct:
                candidates.setdefault(tbl, []).append(col)

        result: dict[str, dict[str, list[str]]] = {}
        total_cand = len(candidates)
        for idx, (tbl, cols) in enumerate(candidates.items(), 1):
            union_parts = [
                f"SELECT '{c}' AS col_name, \"{c}\"::varchar AS val, COUNT(*) AS freq "
                f"FROM {self.schema}.{tbl} WHERE \"{c}\" IS NOT NULL GROUP BY \"{c}\""
                for c in cols
            ]
            sql = " UNION ALL ".join(union_parts) + " ORDER BY col_name, freq DESC"
            try:
                rows = _fetch(con, sql)
                tbl_result: dict[str, list[str]] = {}
                for r in rows:
                    col_name = r["col_name"]
                    entry = f"{r['val']}:{r['freq']}"
                    bucket = tbl_result.setdefault(col_name, [])
                    if len(bucket) < top_n:
                        bucket.append(entry)
                result[tbl] = tbl_result
                log.info("top_freq [%d/%d] %s — %d cols, %d freq rows", idx, total_cand, tbl, len(tbl_result), len(rows))
            except Exception as e:
                log.warning("top_freq [%d/%d] %s — FAILED: %s", idx, total_cand, tbl, e)
        log.info("top_freq_values: processed %d/%d tables.", len(result), total_cand)
        return result
