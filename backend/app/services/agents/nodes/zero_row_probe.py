"""Zero-row diagnosis for the executor node.

Uses an Opus LLM to generate three diagnostic COUNT(*) variants from the
generated SQL, then runs each against Redshift to diagnose why 0 rows were returned.

Probe types returned:
  "time_filter"      — time range too narrow
  "filter_mismatch"  — filter values don't exist in the data
  "bad_join"         — all tables have data but joined result is empty (repair candidate)
  "table_empty"      — a source table itself has no rows
  "aggregate_filter" — HAVING clause removes all results
  "unknown"          — no diagnosis possible

LLM replaces sqlglot-based SQL manipulation — Redshift has AWS-specific functions
(DATEADD, ILIKE, NVL, CONVERT, GETDATE) that sqlglot misparses, causing false negatives.
"""

from __future__ import annotations

import json_repair

from app.core.logger import logger
from app.services.agents.helpers import format_sql
from app.services.agents.semantic_ir import SemanticIR
from app.services.agents.state import AnalyticsState


def _describe_filters(filters: list) -> str:
    parts = []
    for f in filters:
        val_str = ", ".join(str(v) for v in f.value) if isinstance(f.value, list) else str(f.value)
        parts.append(f"`{f.column_name} {f.operator} {val_str}`")
    return " and ".join(parts)


async def _probe_individual_tables(table_fqns: list[str], state: AnalyticsState) -> dict:
    """Probe each table individually — distinguishes empty table from bad join."""
    from app.services.agents.redshift_client import execute_query

    for fqn in table_fqns:
        try:
            _, rows = await execute_query(
                f"SELECT COUNT(*) AS cnt FROM {fqn}",
                timeout_s=15,
                thread_id=state["thread_id"],
            )
            count = int(rows[0][0]) if rows and rows[0] else 0
            if count == 0:
                logger.info("zero_row_probe | stage3b | table={} has 0 rows", fqn)
                return {
                    "probe_type": "table_empty",
                    "needs_clarification": False,
                    "reason": (
                        f"The source table {fqn} contains no data. "
                        "The table may be empty or the relevant data has not been loaded."
                    ),
                }
        except Exception as e:
            logger.warning("zero_row_probe | stage3b | count failed for {} | error={}", fqn, e)

    return {
        "probe_type": "bad_join",
        "needs_clarification": False,
        "reason": (
            "All source tables contain data but the join produces 0 rows. "
            "The join ON clause likely references incorrect column names — "
            "the SQL will be rewritten using the correct join paths."
        ),
    }


async def _llm_generate_probe_sqls(sql: str, state: AnalyticsState) -> dict | None:
    """Ask Opus to generate 3 diagnostic COUNT(*) variants of the original SQL.

    Returns {no_time_filter_sql, no_any_filter_sql, bare_join_sql, diagnosis_hint}
    or None if the LLM call fails.
    """
    try:
        from app.services.agents.bedrock import get_llm
        from app.services.agents.prompts import ZERO_ROW_PROBE_PROMPT

        from app.core.retry import retry_async
        llm = get_llm("complex")   # Opus
        messages = ZERO_ROW_PROBE_PROMPT.format_messages(original_sql=sql)
        response = await retry_async(lambda: llm.ainvoke(messages), service="bedrock-zero-row-probe", max_attempts=2, backoff_base=5.0)
        text = (response.content or "").strip()

        # Strip markdown code fences if present
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
            if "```" in text:
                text = text.split("```")[0].strip()

        parsed = json_repair.loads(text)
        # Basic validation — all three SQL keys must be present and non-empty
        required = ("no_time_filter_sql", "no_any_filter_sql", "bare_join_sql")
        if not all(parsed.get(k) for k in required):
            logger.warning("zero_row_probe | LLM response missing SQL keys | thread={}", state.get("thread_id"))
            return None
        # Format probe SQLs for consistent pretty-printing in logs
        for key in required:
            if parsed.get(key):
                parsed[key] = format_sql(parsed[key])
        return parsed
    except Exception as e:
        logger.warning("zero_row_probe | LLM probe generation failed | error={}", e)
        return None


async def _run_count(sql: str, state: AnalyticsState) -> int:
    """Run a COUNT(*) SQL against Redshift. Returns -1 on error, count otherwise."""
    if not sql or not sql.strip():
        return -1

    # Validate before executing — don't run unsafe or malformed SQL
    try:
        from app.services.agents.nodes.sql_validator import is_safe_count_query
        if not is_safe_count_query(sql):
            logger.warning("zero_row_probe | probe SQL failed safety check | sql_preview={}", sql[:200])
            return -1
    except ImportError:
        pass   # validator not available — proceed anyway

    try:
        from app.services.agents.redshift_client import execute_query
        _, rows = await execute_query(sql, timeout_s=30, thread_id=state["thread_id"])
        return int(rows[0][0]) if rows and rows[0] else 0
    except Exception as e:
        logger.warning("zero_row_probe | count query failed | sql_preview={} | error={}", sql[:200], e)
        return -1


# ── Main probe ────────────────────────────────────────────────────────────────

async def zero_row_probe(ir: SemanticIR | None, state: AnalyticsState) -> dict:
    if not ir or not ir.anchor_tables:
        logger.warning("zero_row_probe | early exit | no IR | thread={}", state.get("thread_id", "?"))
        return {
            "probe_type": "unknown",
            "needs_clarification": False,
            "reason": "No data found for the requested query.",
        }

    sql_list = state.get("sql_list") or []
    generated_sql = sql_list[0] if sql_list else ""

    if not generated_sql:
        return {
            "probe_type": "unknown",
            "needs_clarification": False,
            "reason": "No SQL available to probe.",
        }

    # Stage 4 (no DB call): HAVING filter check — if the IR has HAVING filters,
    # those aggregate filters may be removing all rows. Check this before touching Redshift.
    having_filters = [f for f in (ir.filters or []) if f.is_having and f.resolved]
    where_filters  = [f for f in (ir.filters or []) if not f.is_having and f.resolved]
    time_filter    = ir.time_filter if (ir.time_filter and ir.time_filter.resolved) else None

    # Ask Opus to generate diagnostic probe SQLs
    probe_sqls = await _llm_generate_probe_sqls(generated_sql, state)
    if not probe_sqls:
        logger.warning(
            "zero_row_probe | LLM probe generation failed, falling back to individual table probes | thread={}",
            state.get("thread_id", "?"),
        )
        # Fallback: probe anchor tables directly
        for fqn in ir.anchor_tables:
            from app.services.agents.redshift_client import execute_query
            try:
                _, rows = await execute_query(
                    f"SELECT COUNT(*) AS cnt FROM {fqn}", timeout_s=15, thread_id=state["thread_id"]
                )
                count = int(rows[0][0]) if rows and rows[0] else 0
                if count == 0:
                    return {
                        "probe_type": "table_empty",
                        "needs_clarification": False,
                        "reason": f"Source table {fqn} contains no data.",
                    }
            except Exception:
                pass
        return {
            "probe_type": "bad_join",
            "needs_clarification": False,
            "reason": "Could not diagnose the zero-row result. The join may be incorrect.",
        }

    diagnosis_hint = probe_sqls.get("diagnosis_hint", "")

    # Stage 1: no time filter, other WHERE filters kept
    s1 = await _run_count(probe_sqls["no_time_filter_sql"], state)
    logger.info("zero_row_probe | stage1 (no time filter) | count={} | thread={}", s1, state["thread_id"])
    if s1 > 0:
        if time_filter:
            from app.services.agents.helpers import render_filter_value
            time_str = " for " + render_filter_value(time_filter.operator, time_filter.value)
        else:
            time_str = ""
        return {
            "probe_type": "time_filter",
            "needs_clarification": True,
            "reason": (
                f"Found {s1:,} record(s) matching your filters but not{time_str} the requested time period. "
                f"The time range is too narrow. {diagnosis_hint}"
            ),
        }

    # Stage 2: all WHERE/HAVING removed — do the joined tables have any data at all?
    s2 = await _run_count(probe_sqls["no_any_filter_sql"], state)
    logger.info("zero_row_probe | stage2 (no filters) | count={} | thread={}", s2, state["thread_id"])
    if s2 > 0:
        desc = _describe_filters(where_filters) if where_filters else "(time filter)"
        time_suffix = " and time filter" if time_filter and where_filters else ""
        return {
            "probe_type": "filter_mismatch",
            "needs_clarification": True,
            "reason": (
                f"Tables contain {s2:,} record(s) but all applied filters{time_suffix} ({desc}) return 0 results. "
                f"The filter values may not match what is stored — check exact spelling and casing. {diagnosis_hint}"
            ),
        }

    # Stage 3: bare join — do the tables join at all?
    s3 = await _run_count(probe_sqls["bare_join_sql"], state)
    logger.info("zero_row_probe | stage3 (bare join) | count={} | thread={}", s3, state["thread_id"])
    if s3 == 0:
        return {
            "probe_type": "bad_join",
            "needs_clarification": False,
            "reason": (
                f"The joined tables produce 0 rows even without filters. "
                f"The join condition may reference incorrect columns. {diagnosis_hint}"
            ),
        }

    if s3 > 0 and having_filters:
        desc = _describe_filters(having_filters)
        return {
            "probe_type": "aggregate_filter",
            "needs_clarification": True,
            "reason": (
                f"Data exists ({s3:,} record(s)) but the aggregate filter {desc} removes all results. "
                "Try relaxing this threshold."
            ),
        }

    # Stage 3b: probe individual anchor tables
    if s3 == 0:
        return await _probe_individual_tables(ir.anchor_tables, state)

    return {
        "probe_type": "unknown",
        "needs_clarification": False,
        "reason": f"No data found matching the query criteria. {diagnosis_hint}",
    }
