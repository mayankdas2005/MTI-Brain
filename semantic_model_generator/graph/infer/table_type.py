"""
Statistical table type inference.
8 signals derived from Redshift metadata — no naming conventions required.
Returns (table_type, confidence, signals_list).
"""

from __future__ import annotations

_TIMESTAMP_TYPES = frozenset({
    "timestamp", "timestamptz",
    "timestamp without time zone", "timestamp with time zone", "date",
})
_NUMERIC_TYPES = frozenset({
    "integer", "bigint", "smallint", "int", "int2", "int4", "int8",
    "numeric", "decimal", "float", "float4", "float8",
    "real", "double precision",
})
_TABLE_TYPES = ("fact", "dimension", "bridge", "reference", "staging", "derived")


def infer_table_type(
    table: dict,
    col_dist: dict,
    card: dict,
    enc: dict,
    columns: list[dict],
) -> tuple[str, float, list[str]]:
    """
    Parameters
    ----------
    table     : Q1 row keyed by Q1 column names
    col_dist  : Q9 row for this table
    card      : Q10 row for this table
    enc       : Q11 row for this table
    columns   : Q2 rows for this table (list of column dicts)

    Returns
    -------
    (table_type, confidence, signals)
    """
    scores = {t: 0.0 for t in _TABLE_TYPES}
    signals: list[str] = []

    total_cols   = max(int(col_dist.get("total_cols") or 1), 1)
    numeric_cols = int(col_dist.get("numeric_cols") or 0)
    varchar_cols = int(col_dist.get("varchar_cols") or 0)
    date_cols    = int(col_dist.get("date_cols") or 0)
    bool_cols    = int(col_dist.get("bool_cols") or 0)

    numeric_ratio = numeric_cols / total_cols
    varchar_ratio = varchar_cols / total_cols

    rows         = int(table.get("row_count") or 0)
    size_mb      = float(table.get("size_mb") or 0)
    diststyle    = (table.get("diststyle") or "").upper()
    distkey_col  = (table.get("distkey_col") or "").lower()
    sk1_type     = (table.get("sortkey1_type") or "").lower()
    sortkey_num  = int(table.get("sortkey_count") or 0)
    stats_off    = float(table.get("stats_off") or 0)
    pct_encoded  = float(enc.get("pct_encoded") or 0)

    avg_null_frac = float(card.get("avg_null_frac") or 0)
    max_null_frac = float(card.get("max_null_frac") or 0)
    unique_cols   = int(card.get("unique_col_count") or 0)
    low_card_cols = int(card.get("low_card_col_count") or 0)
    rel_high_card = int(card.get("rel_high_card_count") or 0)

    # FK-like columns: column name ends with _ref, _id, _key, _sk, _code, _no
    _fk_suf = ("_ref", "_id", "_key", "_sk", "_code", "_no", "_num", "_fk")
    fk_count = sum(1 for c in columns if c.get("column_name", "").lower().endswith(_fk_suf))
    fk_ratio = fk_count / total_cols

    # ── Signal 1: diststyle ────────────────────────────────────────────────
    if diststyle == "ALL":
        scores["reference"] += 3.5
        scores["dimension"] += 2.0
        signals.append(f"diststyle=ALL → small reference/dim table")
    elif diststyle == "EVEN":
        scores["staging"] += 3.5
        signals.append("diststyle=EVEN → no distribution key → staging/ETL")
    elif diststyle == "KEY":
        distkey_is_date = any(
            c.get("column_name", "").lower() == distkey_col
            and c.get("data_type", "").lower() in _TIMESTAMP_TYPES
            for c in columns
        )
        if distkey_is_date:
            scores["fact"] += 4.0
            signals.append(f"diststyle=KEY on timestamp col '{distkey_col}' → fact")
        else:
            scores["fact"] += 1.5
            scores["dimension"] += 1.0
            signals.append(f"diststyle=KEY on '{distkey_col}'")

    # ── Signal 2: Sort key type ────────────────────────────────────────────
    if "timestamp" in sk1_type or sk1_type == "date":
        scores["fact"] += 3.5
        signals.append(f"sortkey1_type={sk1_type} → time-ordered fact")
    if sortkey_num >= 2:
        scores["fact"] += 1.0
        signals.append(f"{sortkey_num} sort keys → complex fact")

    # ── Signal 3: Row count ────────────────────────────────────────────────
    if rows > 50_000_000:
        scores["fact"] += 4.0
        signals.append(f"rows={rows:,} → large fact")
    elif rows > 5_000_000:
        scores["fact"] += 2.5
    elif rows > 500_000:
        scores["fact"] += 1.0
        scores["dimension"] += 0.5
    elif rows < 50_000 and rows > 0:
        scores["reference"] += 2.5
        scores["dimension"] += 1.0
        signals.append(f"rows={rows:,} → small → reference/dim")
    if stats_off > 25:
        signals.append(f"stats_off={stats_off:.0f}% → row count may be stale")

    # ── Signal 4: Column type distribution ────────────────────────────────
    if numeric_ratio > 0.35 and date_cols > 0:
        scores["fact"] += 2.5
        signals.append(
            f"numeric_ratio={numeric_ratio:.2f} + {date_cols} date col(s) → measures+time → fact"
        )
    if varchar_ratio > 0.50 and numeric_ratio < 0.25:
        scores["dimension"] += 2.5
        signals.append(
            f"varchar_ratio={varchar_ratio:.2f} numeric={numeric_ratio:.2f} → descriptive → dim"
        )
    if varchar_ratio > 0.70 and rows < 100_000:
        scores["reference"] += 2.0
        signals.append("mostly varchar + small → reference/lookup")

    # ── Signal 5: Cardinality profile ─────────────────────────────────────
    if total_cols <= 5 and fk_count >= 2 and fk_ratio >= 0.40:
        scores["bridge"] += 6.0
        signals.append(f"bridge pattern: {fk_count} FKs in only {total_cols} cols")
    elif rel_high_card >= 3 and numeric_cols >= 2:
        scores["fact"] += 2.0
        signals.append(f"{rel_high_card} high-cardinality cols + {numeric_cols} numerics → fact")
    if unique_cols == 1 and low_card_cols >= 2:
        scores["dimension"] += 2.5
        signals.append(f"1 unique PK + {low_card_cols} low-card cols → dimension")

    # ── Signal 6: Encoding ─────────────────────────────────────────────────
    if pct_encoded > 65:
        scores["fact"] += 2.0
        signals.append(f"pct_encoded={pct_encoded:.0f}% → heavily compressed → fact")
    elif pct_encoded < 20:
        scores["reference"] += 1.0
        scores["dimension"] += 0.5
        signals.append(f"pct_encoded={pct_encoded:.0f}% → minimal compression → ref/dim")

    # ── Signal 7: Null fraction ────────────────────────────────────────────
    if avg_null_frac > 0.35:
        scores["staging"] += 2.5
        signals.append(f"avg_null_frac={avg_null_frac:.2f} → sparse cols → staging")
    if max_null_frac > 0.80 and total_cols > 10:
        scores["staging"] += 1.5
        signals.append(f"max_null_frac={max_null_frac:.2f} on wide table → ETL artifact")

    # ── Signal 8: Boolean columns ─────────────────────────────────────────
    bool_ratio = bool_cols / total_cols
    if bool_ratio > 0.15:
        scores["dimension"] += 1.0
        signals.append(f"{bool_cols} boolean cols → status flags → dim/ref")

    # ── Compute winner ─────────────────────────────────────────────────────
    winner = max(scores, key=scores.get)
    sorted_vals = sorted(scores.values(), reverse=True)
    top, second = sorted_vals[0], sorted_vals[1]
    total_score = sum(scores.values()) or 1.0
    confidence = min(top / total_score, 0.96)

    if top - second < 1.5:
        confidence = min(confidence, 0.55)
        signals.append("LOW_CONFIDENCE → needs LLM review")

    return winner, round(confidence, 3), signals
