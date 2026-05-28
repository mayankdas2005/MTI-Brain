"""Graph context builder: reconstructs the Neo4j knowledge subgraph used for a query.

Loads graph_context snapshot from the assistant message metadata_, re-queries
Neo4j for full node/edge data, builds vis.js-compatible cypherResult records,
injects them into graph_explorer_template.html, and uploads to S3.
Returns (s3_key, s3_url).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import uuid
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import boto3
from sqlalchemy import select

from app.core.config import settings
from app.core.logger import logger
from app.db.session import async_read_session_factory
from app.models.conversation import MTIBrainMessage

_TEMPLATE_PATH = Path(__file__).resolve().parent / "graph_explorer_template.html"
_PLACEHOLDER = "/* GRAPH_DATA_PLACEHOLDER */ []"


# ── S3 helpers (identical pattern to dashboard_builder) ──────────────────────

def _get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def _sync_s3_upload(key: str, html_bytes: bytes, bucket: str) -> None:
    filename = key.split("/")[-1]
    _get_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=html_bytes,
        ContentType="text/html; charset=utf-8",
        ContentDisposition=f'inline; filename="{filename}"',
        CacheControl="no-cache",
    )


def _sync_s3_delete(key: str, bucket: str) -> None:
    _get_s3_client().delete_object(Bucket=bucket, Key=key)


def _sync_presign(key: str, bucket: str, expires_in: int) -> str:
    return _get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


async def generate_presigned_url(s3_key: str, expires_in: int = 7 * 24 * 3600) -> str:
    bucket = settings.AWS_BOTO3_BUCKET_NAME
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_sync_presign, s3_key, bucket, expires_in))


async def _upload_to_s3(key: str, html_bytes: bytes) -> str:
    bucket = settings.AWS_BOTO3_BUCKET_NAME
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_sync_s3_upload, key, html_bytes, bucket))
    return f"https://{bucket}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"


async def delete_from_s3(key: str) -> None:
    try:
        bucket = settings.AWS_BOTO3_BUCKET_NAME
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, partial(_sync_s3_delete, key, bucket))
    except Exception as exc:
        logger.warning("graph_context | S3 delete failed key={} error={}", key, exc)


# ── Embedding strip ───────────────────────────────────────────────────────────

def _strip_embeddings(props: dict | None) -> dict:
    if not props:
        return {}
    return {k: v for k, v in props.items() if not k.endswith("_embedding")}


# ── Node identity registry ────────────────────────────────────────────────────

class _IdRegistry:
    def __init__(self):
        self._map: dict[str, int] = {}
        self._counter = itertools.count(1)

    def get(self, key: str) -> int:
        if key not in self._map:
            self._map[key] = next(self._counter)
        return self._map[key]

    def next_edge_id(self) -> int:
        return next(self._counter)


# ── Triplet helpers ───────────────────────────────────────────────────────────

def _node(identity: int, labels: list[str], props: dict) -> dict:
    return {"identity": identity, "labels": labels, "properties": _strip_embeddings(props)}


def _edge(identity: int, rel_type: str, start: int, end: int, props: dict) -> dict:
    return {"identity": identity, "type": rel_type, "start": start, "end": end,
            "properties": _strip_embeddings(props)}


def _triplet(n: dict, r: dict | None, m: dict | None) -> dict:
    return {"n": n, "r": r, "m": m}


# ── join_clause parser (for multi-hop paths not in JOINS_TO edges) ───────────

def _parse_join_clause_tables(clause: str) -> tuple[str, str] | None:
    """Extract (left_table_fqn, right_table_fqn) from 'schema.t1.col = schema.t2.col'."""
    parts = clause.split("=", 1)
    if len(parts) != 2:
        return None
    def _table(side: str) -> str:
        tokens = side.strip().split(".")
        return ".".join(tokens[:-1]) if len(tokens) >= 3 else ".".join(tokens[:2])
    return _table(parts[0]), _table(parts[1])


# ── Main builder ─────────────────────────────────────────────────────────────

async def generate_and_store(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[str, str]:
    """Build the graph context HTML and upload to S3. Returns (s3_key, s3_url)."""

    # 1. Load assistant message from DB
    async with async_read_session_factory() as session:
        result = await session.execute(
            select(MTIBrainMessage)
            .where(MTIBrainMessage.conversation_id == conversation_id)
            .order_by(MTIBrainMessage.created_at)
        )
        messages = result.scalars().all()

    asst_msg = next((m for m in messages if m.role == "assistant"), None)
    if not asst_msg:
        raise ValueError(f"No assistant message for conversation_id={conversation_id}")

    meta: dict = asst_msg.metadata_ or {}
    snapshot: dict = meta.get("graph_context") or {}
    path_tables: list[str] = snapshot.get("path_tables") or []

    logger.info("graph_context_builder | conv={} | path_tables={}", conversation_id, path_tables)

    if not path_tables:
        raise ValueError(f"No graph_context.path_tables for conversation_id={conversation_id}")

    # 2. Re-query Neo4j for full node/edge data
    from app.services.agents import neo4j_client as nc

    loop = asyncio.get_event_loop()

    # Extract IDs/keys from snapshot upfront — all available before any queries
    join_path_ids: list[str]    = [pid for pid in (snapshot.get("join_path_ids") or []) if pid]
    snap_bt_terms: list[str]    = [bt.get("term") for bt in (snapshot.get("business_terms") or []) if bt.get("term")]
    snap_qp_ids: list[str]      = [qp.get("id") for qp in (snapshot.get("query_patterns") or []) if qp.get("id")]
    snap_ap_ids: list[str]      = [ap.get("id") for ap in (snapshot.get("anti_patterns") or []) if ap.get("id")]
    snap_tmpl_ids: list[str]    = [t.get("id") for t in (snapshot.get("templates") or []) if t.get("id")]

    (
        tables_ctx,
        columns_raw,
        joins_raw,
        relevant_intents,
        struct_similar,
        sem_similar_cols,
        join_paths_raw,
        bt_full,
        qp_full,
        ap_full,
        tmpl_full,
    ) = await asyncio.gather(
        loop.run_in_executor(None, nc.get_tables_with_context, path_tables),
        loop.run_in_executor(None, nc.get_columns_for_tables, path_tables),
        loop.run_in_executor(None, nc.get_direct_joins, path_tables),
        loop.run_in_executor(None, nc.get_table_relevant_intents, path_tables),
        loop.run_in_executor(None, nc.get_structurally_similar_tables, path_tables),
        loop.run_in_executor(None, nc.get_semantically_similar_columns, path_tables),
        loop.run_in_executor(None, nc.get_join_paths_by_ids, join_path_ids),
        loop.run_in_executor(None, nc.get_business_terms_by_terms, snap_bt_terms),
        loop.run_in_executor(None, nc.get_query_patterns_by_ids, snap_qp_ids),
        loop.run_in_executor(None, nc.get_anti_patterns_by_ids, snap_ap_ids),
        loop.run_in_executor(None, nc.get_query_templates_by_ids, snap_tmpl_ids),
    )

    # Community bridges (need community IDs from tables_ctx)
    community_ids = list({
        row["c"]["id"]
        for row in tables_ctx
        if row.get("c") and row["c"].get("id")
    })
    community_bridges = await loop.run_in_executor(None, nc.get_community_bridges, community_ids)

    # 3. Build node/edge tables — Neo4j data is authoritative; snapshot is fallback only
    snapshot_tables: list[dict]         = snapshot.get("tables") or []
    snapshot_intents: list[dict]        = snapshot.get("intents") or []
    selected_columns: list[dict]        = snapshot.get("selected_columns") or []
    template_id: str | None             = snapshot.get("template_id")
    join_clauses: list[str]             = snapshot.get("join_clauses") or []

    # Merge: Neo4j full props keyed by identifier, falling back to snapshot for any missing nodes
    def _merge(neo4j_list: list[dict], snap_list: list[dict], key: str) -> list[dict]:
        """Return Neo4j rows, supplemented by any snapshot rows whose key isn't in Neo4j."""
        neo4j_map = {r.get(key): r for r in neo4j_list if r.get(key)}
        for s in snap_list:
            k = s.get(key)
            if k and k not in neo4j_map:
                neo4j_map[k] = s
        return list(neo4j_map.values())

    snapshot_business_terms: list[dict] = _merge(bt_full,   snapshot.get("business_terms") or [], "term")
    snapshot_query_patterns: list[dict] = _merge(qp_full,   snapshot.get("query_patterns") or [], "id")
    snapshot_anti_patterns:  list[dict] = _merge(ap_full,   snapshot.get("anti_patterns")  or [], "id")
    snapshot_templates:      list[dict] = _merge(tmpl_full, snapshot.get("templates")       or [], "id")

    # Build lookup maps
    table_props_map: dict[str, dict] = {}
    for row in tables_ctx:
        t = row.get("t") or {}
        fqn = t.get("fqn")
        if fqn:
            table_props_map[fqn] = t

    # Enrich with snapshot table data for any tables not returned by get_tables_with_context
    for st in snapshot_tables:
        fqn = st.get("fqn") or st.get("table_fqn")
        if fqn and fqn not in table_props_map:
            table_props_map[fqn] = st

    col_id_map: dict[str, dict] = {}
    col_by_table_name: dict[tuple[str, str], dict] = {}
    for col in columns_raw:
        cid = col.get("id") or f"{col.get('table_fqn')}.{col.get('name')}"
        col_id_map[cid] = col
        col_by_table_name[(col.get("table_fqn", ""), col.get("name", ""))] = col

    selected_set = {
        (sc.get("table_fqn", ""), sc.get("column_name", ""))
        for sc in selected_columns
    }

    domain_map: dict[str, dict] = {}
    community_map: dict[str, dict] = {}
    table_domain_map: dict[str, str] = {}
    table_community_map: dict[str, str] = {}
    for row in tables_ctx:
        t = row.get("t") or {}
        fqn = t.get("fqn")
        d = row.get("d")
        c = row.get("c")
        if d and d.get("name"):
            domain_map[d["name"]] = d
            if fqn:
                table_domain_map[fqn] = d["name"]
        if c and c.get("id"):
            community_map[c["id"]] = c
            if fqn:
                table_community_map[fqn] = c["id"]

    # Start with snapshot intents as a fallback, then overwrite with full Neo4j properties
    # (snapshot intents only carry id/name/score/description; Neo4j has all fields)
    intent_map: dict[str, dict] = {i.get("name", ""): i for i in snapshot_intents if i.get("name")}
    for ri in relevant_intents:
        iname = ri.get("intent", {}).get("name", "")
        if iname:
            intent_map[iname] = ri["intent"]  # always prefer full Neo4j properties

    matched_template = next(
        (t for t in snapshot_templates if t.get("id") == template_id),
        snapshot_templates[0] if snapshot_templates else None,
    )

    # 4. Assign integer identities and build records
    ids = _IdRegistry()
    records: list[dict] = []

    def table_id(fqn: str) -> int:
        return ids.get(f"T:{fqn}")

    def col_id(cid: str) -> int:
        return ids.get(f"C:{cid}")

    def domain_id(name: str) -> int:
        return ids.get(f"D:{name}")

    def community_id_fn(cid: str) -> int:
        return ids.get(f"COM:{cid}")

    def intent_id(name: str) -> int:
        return ids.get(f"I:{name}")

    def template_id_fn(tid: str) -> int:
        return ids.get(f"QT:{tid}")

    def bt_id(term: str) -> int:
        return ids.get(f"BT:{term}")

    def qp_id(qpid: str) -> int:
        return ids.get(f"QP:{qpid}")

    def ap_id(apid: str) -> int:
        return ids.get(f"AP:{apid}")

    def jp_id(jpid: str) -> int:
        return ids.get(f"JP:{jpid}")

    # ── Table HAS_COLUMN Column ──────────────────────────────────────────────
    for col in columns_raw:
        t_fqn = col.get("table_fqn", "")
        cid = col.get("id") or f"{t_fqn}.{col.get('name')}"
        is_selected = (t_fqn, col.get("name", "")) in selected_set
        t_props = table_props_map.get(t_fqn, {"fqn": t_fqn, "name": t_fqn.split(".")[-1]})
        records.append(_triplet(
            _node(table_id(t_fqn), ["Table"], t_props),
            _edge(ids.next_edge_id(), "HAS_COLUMN", table_id(t_fqn), col_id(cid),
                  {"selected": is_selected, "created_at": col.get("created_at")}),
            _node(col_id(cid), ["Column"], col),
        ))

    # ── Table JOINS_TO Table (from Neo4j direct edges) ───────────────────────
    seen_joins: set[tuple[str, str]] = set()
    for j in joins_raw:
        f_fqn, t_fqn = j.get("from_fqn", ""), j.get("to_fqn", "")
        if not f_fqn or not t_fqn:
            continue
        key = (f_fqn, t_fqn)
        if key in seen_joins:
            continue
        seen_joins.add(key)
        f_props = table_props_map.get(f_fqn, {"fqn": f_fqn, "name": f_fqn.split(".")[-1]})
        t_props = table_props_map.get(t_fqn, {"fqn": t_fqn, "name": t_fqn.split(".")[-1]})
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], f_props),
            _edge(ids.next_edge_id(), "JOINS_TO", table_id(f_fqn), table_id(t_fqn), j),
            _node(table_id(t_fqn), ["Table"], t_props),
        ))

    # ── Table JOINS_TO Table (from join_clauses not in direct edges) ─────────
    for clause in join_clauses:
        pair = _parse_join_clause_tables(clause)
        if not pair:
            continue
        f_fqn, t_fqn = pair
        key = (f_fqn, t_fqn)
        if key in seen_joins or f_fqn == t_fqn:
            continue
        seen_joins.add(key)
        f_props = table_props_map.get(f_fqn, {"fqn": f_fqn, "name": f_fqn.split(".")[-1]})
        t_props = table_props_map.get(t_fqn, {"fqn": t_fqn, "name": t_fqn.split(".")[-1]})
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], f_props),
            _edge(ids.next_edge_id(), "JOINS_TO", table_id(f_fqn), table_id(t_fqn),
                  {"join_clause": clause, "source": "join_path"}),
            _node(table_id(t_fqn), ["Table"], t_props),
        ))

    # ── Table STRUCTURALLY_SIMILAR Table ─────────────────────────────────────
    for ss in struct_similar:
        f_fqn, t_fqn = ss.get("from_fqn", ""), ss.get("to_fqn", "")
        if not f_fqn or not t_fqn:
            continue
        f_props = table_props_map.get(f_fqn, {"fqn": f_fqn, "name": f_fqn.split(".")[-1]})
        t_props = table_props_map.get(t_fqn, {"fqn": t_fqn, "name": t_fqn.split(".")[-1]})
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], f_props),
            _edge(ids.next_edge_id(), "STRUCTURALLY_SIMILAR", table_id(f_fqn), table_id(t_fqn),
                  ss.get("rel") or {}),
            _node(table_id(t_fqn), ["Table"], t_props),
        ))

    # ── Table BELONGS_TO Domain ───────────────────────────────────────────────
    for fqn, dname in table_domain_map.items():
        t_props = table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})
        d_props = domain_map.get(dname, {"name": dname})
        records.append(_triplet(
            _node(table_id(fqn), ["Table"], t_props),
            _edge(ids.next_edge_id(), "BELONGS_TO", table_id(fqn), domain_id(dname), {}),
            _node(domain_id(dname), ["Domain"], d_props),
        ))

    # ── Community CONTAINS_TABLE Table ───────────────────────────────────────
    for fqn, cid in table_community_map.items():
        t_props = table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})
        c_props = community_map.get(cid, {"id": cid})
        records.append(_triplet(
            _node(community_id_fn(cid), ["Community"], c_props),
            _edge(ids.next_edge_id(), "CONTAINS_TABLE", community_id_fn(cid), table_id(fqn), {}),
            _node(table_id(fqn), ["Table"], t_props),
        ))

    # ── Community BRIDGES_TO Community ───────────────────────────────────────
    for cb in community_bridges:
        from_cid, to_cid = cb.get("from_id", ""), cb.get("to_id", "")
        if not from_cid or not to_cid:
            continue
        fc_props = community_map.get(from_cid, {"id": from_cid})
        tc_props = community_map.get(to_cid, {"id": to_cid})
        records.append(_triplet(
            _node(community_id_fn(from_cid), ["Community"], fc_props),
            _edge(ids.next_edge_id(), "BRIDGES_TO", community_id_fn(from_cid),
                  community_id_fn(to_cid), cb.get("rel") or {}),
            _node(community_id_fn(to_cid), ["Community"], tc_props),
        ))

    # ── Table RELEVANT_TO Intent ──────────────────────────────────────────────
    for ri in relevant_intents:
        t_fqn = ri.get("table_fqn", "")
        iname = ri.get("intent", {}).get("name", "")
        if not t_fqn or not iname:
            continue
        t_props = table_props_map.get(t_fqn, {"fqn": t_fqn, "name": t_fqn.split(".")[-1]})
        i_props = intent_map.get(iname, {"name": iname})
        records.append(_triplet(
            _node(table_id(t_fqn), ["Table"], t_props),
            _edge(ids.next_edge_id(), "RELEVANT_TO", table_id(t_fqn), intent_id(iname),
                  ri.get("rel") or {}),
            _node(intent_id(iname), ["Intent"], i_props),
        ))

    # ── QueryTemplate REQUIRES_TABLE Table ───────────────────────────────────
    if matched_template:
        tid = matched_template.get("id", template_id or "")
        qt_node = _node(template_id_fn(tid), ["QueryTemplate"], matched_template)
        for anchor_fqn in (matched_template.get("anchor_table_fqns") or []):
            t_props = table_props_map.get(anchor_fqn, {"fqn": anchor_fqn, "name": anchor_fqn.split(".")[-1]})
            records.append(_triplet(
                qt_node,
                _edge(ids.next_edge_id(), "REQUIRES_TABLE", template_id_fn(tid), table_id(anchor_fqn), {}),
                _node(table_id(anchor_fqn), ["Table"], t_props),
            ))

        # ── QueryTemplate CLASSIFIED_AS Intent ───────────────────────────────
        primary_intent_name = matched_template.get("primary_intent", "")
        if primary_intent_name and primary_intent_name in intent_map:
            i_props = intent_map[primary_intent_name]
            records.append(_triplet(
                qt_node,
                _edge(ids.next_edge_id(), "CLASSIFIED_AS", template_id_fn(tid),
                      intent_id(primary_intent_name), {}),
                _node(intent_id(primary_intent_name), ["Intent"], i_props),
            ))

    # ── Column SEMANTICALLY_SIMILAR Column ───────────────────────────────────
    for sc in sem_similar_cols:
        from_cid = sc.get("from_id", "")
        to_cid = sc.get("to_id", "")
        if not from_cid or not to_cid:
            continue
        fc_props = col_id_map.get(from_cid, {"id": from_cid})
        tc_props = col_id_map.get(to_cid, {"id": to_cid})
        records.append(_triplet(
            _node(col_id(from_cid), ["Column"], fc_props),
            _edge(ids.next_edge_id(), "SEMANTICALLY_SIMILAR", col_id(from_cid), col_id(to_cid),
                  sc.get("rel") or {}),
            _node(col_id(to_cid), ["Column"], tc_props),
        ))

    # ── BusinessTerm -[CONTEXT_RELEVANT]-> Table ──────────────────────────────
    # BusinessTerms have no table-reference property; connect them to every
    # path_table from this query so they appear in the top-N-by-degree ranking.
    for bt in snapshot_business_terms:
        term = bt.get("term", "")
        if not term:
            continue
        bt_node = _node(bt_id(term), ["BusinessTerm"], bt)
        linked = False
        for fqn in path_tables:
            t_props = table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})
            records.append(_triplet(
                bt_node,
                _edge(ids.next_edge_id(), "CONTEXT_RELEVANT", bt_id(term), table_id(fqn), {}),
                _node(table_id(fqn), ["Table"], t_props),
            ))
            linked = True
        if not linked:
            records.append(_triplet(bt_node, None, None))

    # ── QueryPattern -[USES_TABLE]-> Table ────────────────────────────────────
    # qp["tables_used"] is a comma-separated FQN string.
    for qp in snapshot_query_patterns:
        qpid = qp.get("id", "") or qp.get("question_text", "")[:40]
        if not qpid:
            continue
        qp_node = _node(qp_id(qpid), ["QueryPattern"], qp)
        raw_tables = [t.strip() for t in (qp.get("tables_used") or "").split(",") if t.strip()]
        linked = False
        for fqn in raw_tables:
            t_props = table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})
            records.append(_triplet(
                qp_node,
                _edge(ids.next_edge_id(), "USES_TABLE", qp_id(qpid), table_id(fqn), {}),
                _node(table_id(fqn), ["Table"], t_props),
            ))
            linked = True
        if not linked:
            records.append(_triplet(qp_node, None, None))

    # ── AntiPattern — intentionally isolated ──────────────────────────────────
    # AntiPatterns are SQL-generation guardrails, not query-answering nodes.
    for ap in snapshot_anti_patterns:
        apid = ap.get("id", "") or ap.get("error_type", "")
        if not apid:
            continue
        records.append(_triplet(_node(ap_id(apid), ["AntiPattern"], ap), None, None))

    # ── JoinPath -[LINKS_TABLE]-> Table ───────────────────────────────────────
    # jp["path_tables"] is the ordered list of FQNs the join path traverses.
    for jp in join_paths_raw:
        jpid = jp.get("id", "")
        if not jpid:
            continue
        jp_node = _node(jp_id(jpid), ["JoinPath"], jp)
        linked = False
        for fqn in (jp.get("path_tables") or []):
            t_props = table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})
            records.append(_triplet(
                jp_node,
                _edge(ids.next_edge_id(), "LINKS_TABLE", jp_id(jpid), table_id(fqn), {}),
                _node(table_id(fqn), ["Table"], t_props),
            ))
            linked = True
        if not linked:
            records.append(_triplet(jp_node, None, None))

    logger.info("graph_context_builder | conv={} | records={}", conversation_id, len(records))

    # 5. Inject into template
    template_html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if _PLACEHOLDER not in template_html:
        raise RuntimeError("graph_explorer_template.html is missing GRAPH_DATA_PLACEHOLDER")
    html = template_html.replace(_PLACEHOLDER, json.dumps(records, default=str))

    # 6. Upload to S3
    short_id = str(conversation_id)[:8]
    today = date.today().isoformat()
    s3_key = f"graph-contexts/graph-{short_id}-{today}.html"
    s3_url = await _upload_to_s3(s3_key, html.encode("utf-8"))

    logger.info("graph_context_builder | uploaded | conv={} | key={}", conversation_id, s3_key)
    return s3_key, s3_url
