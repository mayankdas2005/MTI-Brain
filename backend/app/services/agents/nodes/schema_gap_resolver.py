"""Node: schema_gap_resolver — deterministic schema gap resolution, no LLM.

Runs after directive_writer, before query_compiler.
Parses typed SCHEMA_GAP_* lines from intent_directive_context and fetches the missing
columns/tables and join paths from Neo4j so sql_generator has complete schema coverage.

Three line types (written by directive_writer):
  SCHEMA_GAP_JOIN: lpp.table_a | lpp.table_b   → look up join path between the two tables
  SCHEMA_GAP_TABLE: lpp.table_name             → load all columns for that table
  SCHEMA_GAP_CONCEPT: plain description        → fulltext column search

For every table newly loaded by TABLE/JOIN lines, Pass 3 also looks up join paths to all
pre-existing anchor tables so sql_generator has ON clause hints for the new tables.

Injects new columns into semantic_context["columns"] and "_column_lookup".
Adds newly discovered tables to anchor_tables_resolved.
Injects new join paths into resolved_intent["candidate_join_paths"].
Fast-paths when no SCHEMA_GAP_* lines are present.
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.core.logger import logger
from app.services.agents import neo4j_client
from app.services.agents.state import AnalyticsState

_JOIN_PREFIX  = "SCHEMA_GAP_JOIN:"
_TABLE_PREFIX = "SCHEMA_GAP_TABLE:"
_CONCEPT_PREFIX = "SCHEMA_GAP_CONCEPT:"
_ANY_GAP_MARKER = "SCHEMA_GAP_"


def _parse_gaps(text: str) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Parse directive context into (join_pairs, table_fqns, concepts)."""
    join_pairs: list[tuple[str, str]] = []
    table_fqns: list[str] = []
    concepts:   list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(_JOIN_PREFIX):
            value = line[len(_JOIN_PREFIX):].strip()
            parts = [p.strip() for p in value.split("|")]
            if len(parts) == 2 and parts[0] and parts[1]:
                join_pairs.append((parts[0], parts[1]))
            else:
                logger.warning("schema_gap_resolver | malformed SCHEMA_GAP_JOIN line: {}", line)
        elif line.startswith(_TABLE_PREFIX):
            fqn = line[len(_TABLE_PREFIX):].strip()
            if fqn:
                table_fqns.append(fqn)
        elif line.startswith(_CONCEPT_PREFIX):
            concept = line[len(_CONCEPT_PREFIX):].strip()
            if concept:
                concepts.append(concept)

    return join_pairs, table_fqns, concepts


async def schema_gap_resolver(state: AnalyticsState, config: RunnableConfig) -> dict:
    intent_directive_context = state.get("intent_directive_context") or ""

    if _ANY_GAP_MARKER not in intent_directive_context:
        return {}

    join_pairs, table_fqns, concepts = _parse_gaps(intent_directive_context)

    if not join_pairs and not table_fqns and not concepts:
        return {}

    logger.info(
        "schema_gap_resolver START | thread={} | join_pairs={} | tables={} | concepts={}",
        state.get("thread_id", ""), join_pairs, table_fqns, concepts,
    )

    semantic_context  = dict(state.get("semantic_context") or {})
    existing_lookup: dict = dict(semantic_context.get("_column_lookup") or {})
    existing_fqns: set[str] = {
        c.get("table_fqn") for c in (semantic_context.get("columns") or []) if c.get("table_fqn")
    }
    anchor_tables: list[str] = list(state.get("anchor_tables_resolved") or [])
    new_tables: list[str] = []
    new_cols:   list[dict] = []

    # ── Pass 1: SCHEMA_GAP_TABLE — load all columns for explicit table FQNs ──────────────
    load_fqns = list(dict.fromkeys(t for t in table_fqns if t not in existing_fqns))
    if load_fqns:
        try:
            full_cols = await asyncio.to_thread(neo4j_client.get_columns_for_tables, load_fqns)
            for c in (full_cols or []):
                key = (c.get("table_fqn"), c.get("name"))
                if key not in existing_lookup:
                    new_cols.append(c)
                    existing_lookup[key] = c
            new_tables.extend(t for t in load_fqns if t not in new_tables)
        except Exception as e:
            logger.warning("schema_gap_resolver | table load failed | {} | error={}", load_fqns, e)

    # ── Pass 2: SCHEMA_GAP_CONCEPT — fulltext search for matching columns (parallel) ───────
    async def _concept_search(concept: str) -> list[dict]:
        try:
            return await asyncio.to_thread(neo4j_client.search_columns_fulltext, concept) or []
        except Exception:
            return []

    if concepts:
        concept_results = await asyncio.gather(*[_concept_search(c) for c in concepts])
        for matches in concept_results:
            for col in matches:
                fqn = col.get("table_fqn")
                if not fqn:
                    continue
                if fqn not in existing_fqns and fqn not in new_tables:
                    new_tables.append(fqn)
                key = (fqn, col.get("name"))
                if key not in existing_lookup:
                    new_cols.append(col)
                    existing_lookup[key] = col

    updated_anchor = anchor_tables + [t for t in new_tables if t not in anchor_tables]

    # ── Pass 3: join path lookups (parallel, bounded) ────────────────────────────────────
    # A) Explicit SCHEMA_GAP_JOIN pairs from directive_writer — always include, up to cap.
    # B) New tables (from Pass 1/2) × ORIGINAL anchor tables only.
    #    Never pair new-table × new-table: concept search can return 15+ tables and
    #    cross-pairing them produces O(N²) lookups with no benefit — those tables were
    #    discovered as potential column matches, not as confirmed join partners.
    _MAX_JOIN_LOOKUPS = 15

    pairs_to_lookup: list[tuple[str, str]] = list(join_pairs)[:_MAX_JOIN_LOOKUPS]

    if new_tables and len(pairs_to_lookup) < _MAX_JOIN_LOOKUPS:
        pre_existing = [t for t in anchor_tables if t not in new_tables]
        seen: set[tuple] = {tuple(sorted(p)) for p in pairs_to_lookup}
        for new_tbl in new_tables:
            if len(pairs_to_lookup) >= _MAX_JOIN_LOOKUPS:
                break
            for other in pre_existing:                       # original anchors only — no N×N
                if len(pairs_to_lookup) >= _MAX_JOIN_LOOKUPS:
                    break
                key = tuple(sorted([new_tbl, other]))
                if key not in seen:
                    pairs_to_lookup.append((new_tbl, other))
                    seen.add(key)

    new_join_paths: list[dict] = []

    async def _lookup_path(from_fqn: str, to_fqn: str) -> dict | None:
        try:
            return await asyncio.to_thread(neo4j_client.load_best_join_path, from_fqn, to_fqn)
        except Exception as exc:
            logger.warning(
                "schema_gap_resolver | join_path_lookup failed | {} → {} | error={}",
                from_fqn, to_fqn, exc,
            )
            return None

    if pairs_to_lookup:
        path_results = await asyncio.gather(*[_lookup_path(f, t) for f, t in pairs_to_lookup])
        for (from_fqn, to_fqn), path in zip(pairs_to_lookup, path_results):
            if path:
                new_join_paths.append(path)
                logger.info(
                    "schema_gap_resolver | join_path_found | {} → {} | clauses={}",
                    from_fqn, to_fqn, path.get("join_clauses"),
                )

    if not new_cols and not new_join_paths:
        logger.info("schema_gap_resolver | nothing new found | thread={}", state.get("thread_id", ""))
        return {}

    result: dict = {}

    if new_cols:
        semantic_context["_column_lookup"] = existing_lookup
        semantic_context["columns"] = list(semantic_context.get("columns") or []) + new_cols
        result["semantic_context"] = semantic_context
        result["anchor_tables_resolved"] = updated_anchor

    if new_join_paths:
        ri = dict(state.get("resolved_intent") or {})
        existing_paths = list(ri.get("candidate_join_paths") or [])
        seen_clauses: set[tuple] = {
            tuple(sorted(p.get("join_clauses") or [])) for p in existing_paths
        }
        for p in new_join_paths:
            key = tuple(sorted(p.get("join_clauses") or []))
            if key not in seen_clauses:
                existing_paths.append(p)
                seen_clauses.add(key)
        ri["candidate_join_paths"] = existing_paths
        result["resolved_intent"] = ri

    logger.info(
        "schema_gap_resolver DONE | thread={} | new_tables={} | new_cols={} | new_join_paths={}",
        state.get("thread_id", ""), new_tables, len(new_cols), len(new_join_paths),
    )
    return result
