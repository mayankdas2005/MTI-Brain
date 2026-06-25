"""Graph context builder: reconstructs the Neo4j knowledge subgraph used for a query.

Loads graph_context snapshot from the assistant message metadata_, re-queries
Neo4j for full node/edge data, builds vis.js-compatible cypherResult records,
injects them into graph_explorer_template.html, and uploads to S3.
Returns (s3_key, s3_url).

Trust principle: shows ONLY what the agents actually touched —
  - Tables that appear in the SQL (anchor + path tables from SemanticIR)
  - Columns that are in measures / dimensions / filters / time_filter
  - The JoinPath nodes actually used (not all candidates)
  - BusinessTerms with real REFERENCES_TABLE edges to anchor tables
  - Detected intents pointing to anchor tables
  - AntiPatterns used as guardrails
  - QueryPatterns matched as ground truth
  - Domain + Community context for anchor tables
  - Cross-domain hub + BRIDGES_TO if is_cross_domain=True
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

import boto3
from sqlalchemy import select

from app.core.config import settings
from app.core.logger import logger
from app.db.session import async_read_session_factory
from app.models.conversation import MTIBrainMessage

_TEMPLATE_PATH = Path(__file__).resolve().parent / "graph_explorer_template.html"
_PLACEHOLDER          = "/* GRAPH_DATA_PLACEHOLDER */ []"
_SQL_PLACEHOLDER      = '/* SQL_PATH_PLACEHOLDER */ ""'
_FEEDBACK_PLACEHOLDER = "/* FEEDBACK_OVERLAY_PLACEHOLDER */ {}"


# ── S3 helpers ────────────────────────────────────────────────────────────────

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


# ── Server-side graph layout ─────────────────────────────────────────────────

def _compute_layout(records: list[dict]) -> dict[int, tuple[float, float]]:
    """Compute Fruchterman-Reingold layout in Python; returns {node_id: (x_px, y_px)}.

    Running layout server-side (once, in ~ms) eliminates the O(n²) physics
    simulation that vis.js would otherwise run in the browser on every page load.
    Positions are embedded as _x/_y on each node so the template starts with
    physics disabled and pre-positioned nodes — instant render regardless of scale.
    """
    try:
        import networkx as nx
    except ImportError:
        return {}

    G = nx.Graph()
    for rec in records:
        for key in ("n", "m"):
            nd = rec.get(key)
            if nd and nd.get("identity") is not None:
                G.add_node(nd["identity"])
        r = rec.get("r")
        if r and r.get("start") is not None and r.get("end") is not None:
            G.add_edge(r["start"], r["end"])

    if not G.nodes:
        return {}

    n = len(G.nodes)
    # k controls ideal edge length; larger k → more spread.  seed → deterministic.
    pos = nx.spring_layout(G, k=1 / max(1, n ** 0.5), iterations=80, seed=42)

    # Scale normalized [-1, 1] → vis.js pixel coords
    scale = max(800, n * 55)
    return {node_id: (float(x) * scale, float(y) * scale) for node_id, (x, y) in pos.items()}


def _inject_layout(records: list[dict], positions: dict[int, tuple[float, float]]) -> None:
    """Stamp _x/_y top-level fields onto every node dict in-place."""
    if not positions:
        return
    seen: set[int] = set()
    for rec in records:
        for key in ("n", "m"):
            nd = rec.get(key)
            if nd is None:
                continue
            nid = nd.get("identity")
            if nid is None or nid in seen:
                continue
            seen.add(nid)
            if nid in positions:
                nd["_x"], nd["_y"] = positions[nid]


# ── join_clause parser ────────────────────────────────────────────────────────

def _parse_sql_path(sql: str) -> tuple[set[str], set[str]]:
    """Extract table FQNs and column names referenced in the final SQL."""
    tables = {m.lower() for m in re.findall(r'\blpp\.\w+\b', sql, re.IGNORECASE)}
    # Extract alias.column and bare column tokens after SELECT / WHERE / GROUP BY
    cols: set[str] = set()
    for m in re.finditer(r'\b\w+\.(\w+)\b', sql):
        cols.add(m.group(1).lower())
    return tables, cols


def _mark_sql_path(records: list[dict], sql_tables: set[str], sql_cols: set[str]) -> None:
    """Stamp _in_sql_path=True on Table/Column nodes whose FQN/name appears in the SQL."""
    seen: set[int] = set()
    for rec in records:
        for key in ("n", "m"):
            nd = rec.get(key)
            if nd is None:
                continue
            nid = nd.get("identity")
            if nid is None or nid in seen:
                continue
            seen.add(nid)
            labels = nd.get("labels") or []
            props = nd.get("properties") or {}
            if "Table" in labels:
                fqn = (props.get("fqn") or "").lower()
                if fqn in sql_tables:
                    props["_in_sql_path"] = True
            elif "Column" in labels:
                name = (props.get("name") or "").lower()
                if name in sql_cols:
                    props["_in_sql_path"] = True


async def _build_feedback_overlay(thread_id: str) -> dict:
    """Return {positive: [fqn, ...], negative: [fqn, ...]} for this thread's feedback."""
    try:
        from sqlalchemy import select as sa_select
        from app.db.session import async_read_session_factory
        from app.models.conversation import MTIBrainFeedback, MTIBrainMessage
        async with async_read_session_factory() as db:
            rows = (await db.execute(
                sa_select(MTIBrainFeedback.liked, MTIBrainMessage.metadata_)
                .join(MTIBrainMessage, MTIBrainMessage.id == MTIBrainFeedback.message_id, isouter=True)
                .where(MTIBrainFeedback.thread_id == thread_id)
                .order_by(MTIBrainFeedback.created_at.desc())
                .limit(20)
            )).all()
        positive: set[str] = set()
        negative: set[str] = set()
        for liked, meta in rows:
            source_tables = ((meta or {}).get("source_tables") or [])
            if liked:
                positive.update(source_tables)
            else:
                negative.update(source_tables)
        return {"positive": list(positive), "negative": list(negative)}
    except Exception as exc:
        logger.warning("graph_context_builder | feedback_overlay_failed | err={}", exc)
        return {"positive": [], "negative": []}


def _parse_join_clause_tables(clause: str) -> tuple[str, str] | None:
    """Extract (left_table_fqn, right_table_fqn) from 'schema.t1.col = schema.t2.col'."""
    parts = clause.split("=", 1)
    if len(parts) != 2:
        return None
    def _table(side: str) -> str:
        tokens = side.strip().split(".")
        return ".".join(tokens[:-1]) if len(tokens) >= 3 else ".".join(tokens[:2])
    return _table(parts[0]), _table(parts[1])


# ── Main builder ──────────────────────────────────────────────────────────────

async def generate_and_store(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[str, str]:
    """Build the trust graph HTML and upload to S3. Returns (s3_key, s3_url).

    Shows only what was actually used to generate the answer:
    - anchor tables + path tables (SQL scope)
    - used columns (measures, dimensions, filters)
    - actual join paths
    - real BusinessTerm → Table edges (not synthetic connections)
    - intents, anti-patterns, query patterns
    - domain + community context
    """

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
    sql_string: str = meta.get("sql") or ""
    thread_id_str: str = str(asst_msg.thread_id)

    # Migration: old snapshot format uses "tables" + "selected_columns" instead of
    # "anchor_tables" + "used_columns". Fall back to legacy builder for those.
    if "anchor_tables" not in snapshot and "path_tables" in snapshot:
        logger.warning(
            "graph_context_builder | old_snapshot_format | conv={} | using legacy builder",
            conversation_id,
        )
        return await _generate_from_old_snapshot(snapshot, conversation_id)

    # 2. Extract new-format snapshot fields
    anchor_tables: list[str] = snapshot.get("anchor_tables") or []
    path_tables: list[str]   = snapshot.get("path_tables") or anchor_tables
    all_tables = list(dict.fromkeys(path_tables + anchor_tables))

    if not all_tables:
        logger.warning(
            "graph_context_builder | no_anchor_tables | conv={} | building partial graph from metadata only",
            conversation_id,
        )

    used_columns: list[dict] = snapshot.get("used_columns") or []
    join_clauses: list[str]  = snapshot.get("join_clauses") or []
    join_path_ids: list[str] = snapshot.get("join_path_ids") or []
    is_cross_domain: bool    = snapshot.get("is_cross_domain", False)
    cross_domain_hub: dict   = snapshot.get("cross_domain_hub") or {}

    snap_bt_terms: list[str]   = [bt.get("term") for bt in (snapshot.get("business_terms") or []) if bt.get("term")]
    snap_qp_ids: list[str]     = [qp.get("id") for qp in (snapshot.get("query_patterns") or []) if qp.get("id")]
    snap_ap_ids: list[str]     = [ap.get("id") for ap in (snapshot.get("anti_patterns") or []) if ap.get("id")]
    snap_tmpl_ids: list[str]   = list(dict.fromkeys(
        t for t in [
            snapshot.get("template_id", ""),
            *[tmpl.get("id", "") for tmpl in (snapshot.get("templates") or [])],
        ] if t
    ))

    raw_graph: dict = meta.get("neo4j_raw_graph") or {}

    col_ids = [
        f"{c['table_fqn']}.{c['column_name']}"
        for c in used_columns
        if c.get("table_fqn") and c.get("column_name")
    ]

    logger.info(
        "graph_context_builder | conv={} | anchor_tables={} | path_tables={} | used_cols={} | cross_domain={}",
        conversation_id, anchor_tables, path_tables, len(used_columns), is_cross_domain,
    )

    # 3. Re-query Neo4j for full node/edge data (10 parallel queries)
    from app.services.agents import neo4j_client as nc
    loop = asyncio.get_event_loop()

    (
        tables_ctx,
        col_props_raw,
        joins_raw,
        relevant_intents,
        join_paths_raw,
        bt_full,
        bt_table_edges,
        qp_full,
        ap_full,
        tmpl_full,
    ) = await asyncio.gather(
        loop.run_in_executor(None, nc.get_tables_with_context, all_tables),
        loop.run_in_executor(None, nc.get_columns_by_ids, col_ids),
        loop.run_in_executor(None, nc.get_direct_joins, all_tables),
        loop.run_in_executor(None, nc.get_table_relevant_intents, anchor_tables),
        loop.run_in_executor(None, nc.get_join_paths_by_ids, join_path_ids),
        loop.run_in_executor(None, nc.get_business_terms_by_terms, snap_bt_terms),
        loop.run_in_executor(None, nc.get_business_term_table_edges, snap_bt_terms, anchor_tables),
        loop.run_in_executor(None, nc.get_query_patterns_by_ids, snap_qp_ids),
        loop.run_in_executor(None, nc.get_anti_patterns_by_ids, snap_ap_ids),
        loop.run_in_executor(None, nc.get_query_templates_by_ids, snap_tmpl_ids),
    )

    # Community bridges (only for cross-domain — needs community IDs from tables)
    community_ids = list({
        row["c"]["id"]
        for row in tables_ctx
        if row.get("c") and row["c"].get("id")
    })
    community_bridges = await loop.run_in_executor(None, nc.get_community_bridges, community_ids)

    # 4. Build lookup maps
    table_props_map: dict[str, dict] = {}
    domain_map: dict[str, dict] = {}
    community_map: dict[str, dict] = {}
    table_domain_map: dict[str, str] = {}
    table_community_map: dict[str, str] = {}

    for row in tables_ctx:
        t = row.get("t") or {}
        fqn = t.get("fqn")
        if fqn:
            table_props_map[fqn] = t
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

    # Annotate cross-domain hub table
    if is_cross_domain and cross_domain_hub.get("hub_table_fqn"):
        hub_fqn = cross_domain_hub["hub_table_fqn"]
        if hub_fqn in table_props_map:
            table_props_map[hub_fqn]["_is_cross_domain_hub"] = True
            table_props_map[hub_fqn]["_hub_join_col"] = cross_domain_hub.get("hub_join_col", "")

    # Column props by id
    col_props_by_id: dict[str, dict] = {c.get("id", ""): c for c in col_props_raw if c.get("id")}

    # Build used_column role lookup: (table_fqn, column_name) → role info
    used_col_role: dict[tuple, dict] = {
        (c["table_fqn"], c["column_name"]): c
        for c in used_columns
        if c.get("table_fqn") and c.get("column_name")
    }

    # Intent map
    intent_map: dict[str, dict] = {}
    for ri in relevant_intents:
        iname = ri.get("intent", {}).get("name", "")
        if iname:
            intent_map[iname] = ri["intent"]

    # BusinessTerm full props
    bt_map: dict[str, dict] = {bt.get("term", ""): bt for bt in bt_full if bt.get("term")}
    # BusinessTerm → Table edges (real REFERENCES_TABLE)
    bt_to_table: dict[str, list[str]] = {}
    for edge in bt_table_edges:
        term = edge.get("term", "")
        table_fqn = edge.get("table_fqn", "")
        if term and table_fqn:
            bt_to_table.setdefault(term, []).append(table_fqn)

    # 5. Assign integer identities and build records
    ids = _IdRegistry()
    records: list[dict] = []

    def table_id(fqn: str) -> int:       return ids.get(f"T:{fqn}")
    def col_id(cid: str) -> int:         return ids.get(f"C:{cid}")
    def domain_id(name: str) -> int:     return ids.get(f"D:{name}")
    def comm_id(cid: str) -> int:        return ids.get(f"COM:{cid}")
    def intent_id(name: str) -> int:     return ids.get(f"I:{name}")
    def bt_id(term: str) -> int:         return ids.get(f"BT:{term}")
    def qp_id(qpid: str) -> int:         return ids.get(f"QP:{qpid}")
    def ap_id(apid: str) -> int:         return ids.get(f"AP:{apid}")
    def jp_id(jpid: str) -> int:         return ids.get(f"JP:{jpid}")
    def tmpl_node_id(tid: str) -> int:   return ids.get(f"QT:{tid}")

    def _t_props(fqn: str) -> dict:
        return table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})

    # ── Table HAS_COLUMN Column (only USED columns with role annotation) ─────
    for uc in used_columns:
        t_fqn = uc.get("table_fqn", "")
        c_name = uc.get("column_name", "")
        role = uc.get("role", "")
        agg = uc.get("aggregation")
        if not t_fqn or not c_name:
            continue
        cid = f"{t_fqn}.{c_name}"
        c_full_props = col_props_by_id.get(cid, {"id": cid, "name": c_name, "table_fqn": t_fqn})
        edge_props: dict = {"role": role}
        if agg:
            edge_props["aggregation"] = agg
        records.append(_triplet(
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            _edge(ids.next_edge_id(), "HAS_COLUMN", table_id(t_fqn), col_id(cid), edge_props),
            _node(col_id(cid), ["Column"], c_full_props),
        ))

    # ── Table JOINS_TO Table (direct FK edges between path tables) ───────────
    seen_joins: set[tuple[str, str]] = set()
    for j in joins_raw:
        f_fqn, t_fqn = j.get("from_fqn", ""), j.get("to_fqn", "")
        if not f_fqn or not t_fqn:
            continue
        key = (f_fqn, t_fqn)
        if key in seen_joins:
            continue
        seen_joins.add(key)
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], _t_props(f_fqn)),
            _edge(ids.next_edge_id(), "JOINS_TO", table_id(f_fqn), table_id(t_fqn), j),
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
        ))

    # ── Table JOINS_TO Table (from join_clauses for multi-hop paths) ──────────
    for clause in join_clauses:
        pair = _parse_join_clause_tables(clause)
        if not pair:
            continue
        f_fqn, t_fqn = pair
        key = (f_fqn, t_fqn)
        if key in seen_joins or f_fqn == t_fqn:
            continue
        seen_joins.add(key)
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], _t_props(f_fqn)),
            _edge(ids.next_edge_id(), "JOINS_TO", table_id(f_fqn), table_id(t_fqn),
                  {"join_clause": clause, "source": "join_path"}),
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
        ))

    # ── Table BELONGS_TO Domain ───────────────────────────────────────────────
    for fqn, dname in table_domain_map.items():
        d_props = domain_map.get(dname, {"name": dname})
        records.append(_triplet(
            _node(table_id(fqn), ["Table"], _t_props(fqn)),
            _edge(ids.next_edge_id(), "BELONGS_TO", table_id(fqn), domain_id(dname), {}),
            _node(domain_id(dname), ["Domain"], d_props),
        ))

    # ── Community CONTAINS_TABLE Table ───────────────────────────────────────
    for fqn, cid in table_community_map.items():
        c_props = community_map.get(cid, {"id": cid})
        records.append(_triplet(
            _node(comm_id(cid), ["Community"], c_props),
            _edge(ids.next_edge_id(), "CONTAINS_TABLE", comm_id(cid), table_id(fqn), {}),
            _node(table_id(fqn), ["Table"], _t_props(fqn)),
        ))

    # ── Community BRIDGES_TO Community (cross-domain only) ───────────────────
    for cb in community_bridges:
        from_cid, to_cid = cb.get("from_id", ""), cb.get("to_id", "")
        if not from_cid or not to_cid:
            continue
        fc_props = community_map.get(from_cid, {"id": from_cid})
        tc_props = community_map.get(to_cid, {"id": to_cid})
        records.append(_triplet(
            _node(comm_id(from_cid), ["Community"], fc_props),
            _edge(ids.next_edge_id(), "BRIDGES_TO", comm_id(from_cid), comm_id(to_cid),
                  cb.get("rel") or {}),
            _node(comm_id(to_cid), ["Community"], tc_props),
        ))

    # ── Table RELEVANT_TO Intent (anchor tables only) ─────────────────────────
    for ri in relevant_intents:
        t_fqn = ri.get("table_fqn", "")
        iname = ri.get("intent", {}).get("name", "")
        if not t_fqn or not iname:
            continue
        i_props = intent_map.get(iname, {"name": iname})
        records.append(_triplet(
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            _edge(ids.next_edge_id(), "RELEVANT_TO", table_id(t_fqn), intent_id(iname),
                  ri.get("rel") or {}),
            _node(intent_id(iname), ["Intent"], i_props),
        ))

    # ── BusinessTerm REFERENCES_TABLE Table (real Neo4j edges only) ──────────
    # Only show terms that actually point to anchor tables — not all terms to all tables
    for term, linked_tables in bt_to_table.items():
        bt_props = bt_map.get(term, {"term": term})
        bt_node = _node(bt_id(term), ["BusinessTerm"], bt_props)
        for t_fqn in linked_tables:
            records.append(_triplet(
                bt_node,
                _edge(ids.next_edge_id(), "REFERENCES_TABLE", bt_id(term), table_id(t_fqn), {}),
                _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            ))

    # BusinessTerms with no REFERENCES_TABLE edge to anchor tables — show isolated
    for bt in bt_full:
        term = bt.get("term", "")
        if not term or term in bt_to_table:
            continue
        records.append(_triplet(_node(bt_id(term), ["BusinessTerm"], bt), None, None))

    # ── QueryPattern USES_TABLE Table ─────────────────────────────────────────
    for qp in qp_full:
        qpid = qp.get("id", "") or qp.get("question_text", "")[:40]
        if not qpid:
            continue
        qp_node = _node(qp_id(qpid), ["QueryPattern"], qp)
        raw_tables = qp.get("tables_used") or []
        if isinstance(raw_tables, str):
            raw_tables = [t.strip() for t in raw_tables.split(",") if t.strip()]
        linked = False
        for t_fqn in raw_tables:
            records.append(_triplet(
                qp_node,
                _edge(ids.next_edge_id(), "USES_TABLE", qp_id(qpid), table_id(t_fqn), {}),
                _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            ))
            linked = True
        if not linked:
            records.append(_triplet(qp_node, None, None))

    # ── AntiPattern — isolated (SQL-generation guardrails, not query nodes) ───
    for ap in ap_full:
        apid = ap.get("id", "") or ap.get("error_type", "")
        if not apid:
            continue
        records.append(_triplet(_node(ap_id(apid), ["AntiPattern"], ap), None, None))

    # ── JoinPath LINKS_TABLE Table ────────────────────────────────────────────
    for jp in join_paths_raw:
        jpid = jp.get("id", "")
        if not jpid:
            continue
        jp_node = _node(jp_id(jpid), ["JoinPath"], jp)
        linked = False
        for t_fqn in (jp.get("path_tables") or []):
            records.append(_triplet(
                jp_node,
                _edge(ids.next_edge_id(), "LINKS_TABLE", jp_id(jpid), table_id(t_fqn), {}),
                _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            ))
            linked = True
        if not linked:
            records.append(_triplet(jp_node, None, None))

    # ── QueryTemplate REQUIRES_TABLE Table ───────────────────────────────────
    selected_template_id = snapshot.get("template_id", "")
    for tmpl in tmpl_full:
        tid = tmpl.get("id", "")
        if not tid:
            continue
        # Mark whichever template was actually selected by intent_resolver
        if selected_template_id and tid == selected_template_id:
            tmpl = {**tmpl, "_selected": True}
        qt_node = _node(tmpl_node_id(tid), ["QueryTemplate"], tmpl)
        linked = False
        for anchor_fqn in (tmpl.get("anchor_table_fqns") or []):
            records.append(_triplet(
                qt_node,
                _edge(ids.next_edge_id(), "REQUIRES_TABLE", tmpl_node_id(tid), table_id(anchor_fqn), {}),
                _node(table_id(anchor_fqn), ["Table"], _t_props(anchor_fqn)),
            ))
            linked = True
        primary_intent_name = tmpl.get("primary_intent", "")
        if primary_intent_name and primary_intent_name in intent_map:
            records.append(_triplet(
                qt_node,
                _edge(ids.next_edge_id(), "CLASSIFIED_AS", tmpl_node_id(tid),
                      intent_id(primary_intent_name), {}),
                _node(intent_id(primary_intent_name), ["Intent"], intent_map[primary_intent_name]),
            ))
            linked = True
        if not linked:
            records.append(_triplet(qt_node, None, None))

    # 5b. Retrieved-only nodes from neo4j_raw_graph — all Neo4j nodes not shown by the anchor path
    _anchor_table_set: set[str] = set(all_tables)
    _seen_col_keys: set[str] = {
        f"{uc['table_fqn']}.{uc['column_name']}"
        for uc in used_columns if uc.get("table_fqn") and uc.get("column_name")
    }
    _seen_bt_terms: set[str] = {bt.get("term", "") for bt in bt_full}
    _seen_qp_ids: set[str] = set(snap_qp_ids)
    _seen_ap_ids: set[str] = set(snap_ap_ids)
    _seen_jp_ids: set[str] = set(join_path_ids)

    for _rn in raw_graph.get("nodes") or []:
        _label = _rn.get("_label", "")
        _props = {k: v for k, v in _rn.items() if not k.startswith("_")}

        if _label == "Table":
            _fqn = _rn.get("fqn")
            if not _fqn or _fqn in _anchor_table_set:
                continue
            records.append(_triplet(
                _node(table_id(_fqn), ["Table"], _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "Column":
            _ctable = _rn.get("table_fqn")
            _cname = _rn.get("name")
            if not _ctable or not _cname:
                continue
            _ckey = f"{_ctable}.{_cname}"
            if _ckey in _seen_col_keys:
                continue
            _seen_col_keys.add(_ckey)
            _cid_val = col_id(_ckey)
            records.append(_triplet(
                _node(table_id(_ctable), ["Table"],
                      table_props_map.get(_ctable, {"fqn": _ctable, "name": _ctable.split(".")[-1]})),
                _edge(ids.next_edge_id(), "HAS_COLUMN", table_id(_ctable), _cid_val,
                      {"_retrieved_only": True}),
                _node(_cid_val, ["Column"], _strip_embeddings({**_props, "_retrieved_only": True})),
            ))

        elif _label == "BusinessTerm":
            _term = _rn.get("term")
            if not _term or _term in _seen_bt_terms:
                continue
            records.append(_triplet(
                _node(bt_id(_term), ["BusinessTerm"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "Intent":
            _iname = _rn.get("name")
            if not _iname or _iname in intent_map:
                continue
            records.append(_triplet(
                _node(intent_id(_iname), ["Intent"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "QueryTemplate":
            _qtid = _rn.get("id")
            if not _qtid or _qtid in snap_tmpl_ids:
                continue
            records.append(_triplet(
                _node(tmpl_node_id(_qtid), ["QueryTemplate"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "QueryPattern":
            _qpid_r = _rn.get("id") or _rn.get("intent", "")
            if not _qpid_r or _qpid_r in _seen_qp_ids:
                continue
            records.append(_triplet(
                _node(qp_id(_qpid_r), ["QueryPattern"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "AntiPattern":
            _apid_r = _rn.get("id") or _rn.get("error_type", "")
            if not _apid_r or _apid_r in _seen_ap_ids:
                continue
            records.append(_triplet(
                _node(ap_id(_apid_r), ["AntiPattern"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "JoinPath":
            _jpid_r = _rn.get("id") or f"{_rn.get('from_fqn','')}→{_rn.get('to_fqn','')}"
            if not _jpid_r or _jpid_r in _seen_jp_ids:
                continue
            records.append(_triplet(
                _node(jp_id(_jpid_r), ["JoinPath"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "Community":
            _com_id = _rn.get("id")
            if not _com_id or _com_id in community_map:
                continue
            records.append(_triplet(
                _node(comm_id(_com_id), ["Community"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

        elif _label == "Domain":
            _dom_name = _rn.get("name")
            if not _dom_name or _dom_name in domain_map:
                continue
            records.append(_triplet(
                _node(domain_id(_dom_name), ["Domain"],
                      _strip_embeddings({**_props, "_retrieved_only": True})),
                None, None,
            ))

    # 5c. Edges from neo4j_raw_graph — wire retrieved-only (and anchor) nodes together.
    # addNode() in the template deduplicates by identity, so emitting _triplet(None, edge, None)
    # is safe: the edge is registered against already-present node IDs with no node re-emission.
    def _resolve_raw_edge_endpoints(raw_edge: dict) -> tuple[str, str] | None:
        t = raw_edge.get("_type", "")
        if t == "JOINS_TO":
            ff, tf = raw_edge.get("from_fqn"), raw_edge.get("to_fqn")
            return (f"T:{ff}", f"T:{tf}") if ff and tf else None
        if t == "HAS_COLUMN":
            tf, cn = raw_edge.get("table_fqn"), raw_edge.get("column_name")
            return (f"T:{tf}", f"C:{tf}.{cn}") if tf and cn else None
        if t == "REFERENCES_TABLE":
            term, tf = raw_edge.get("term"), raw_edge.get("table_fqn")
            return (f"BT:{term}", f"T:{tf}") if term and tf else None
        if t == "RELEVANT_TO":
            tf, iname = raw_edge.get("table_fqn"), raw_edge.get("intent_name")
            return (f"T:{tf}", f"I:{iname}") if tf and iname else None
        if t == "CONTAINS_TABLE":
            cid, tf = raw_edge.get("community_id"), raw_edge.get("table_fqn")
            return (f"COM:{cid}", f"T:{tf}") if cid and tf else None
        if t == "BELONGS_TO":
            tf, dn = raw_edge.get("table_fqn"), raw_edge.get("domain_name")
            return (f"T:{tf}", f"D:{dn}") if tf and dn else None
        if t == "REQUIRES_TABLE":
            tid, tf = raw_edge.get("template_id"), raw_edge.get("table_fqn")
            return (f"QT:{tid}", f"T:{tf}") if tid and tf else None
        if t == "SEMANTICALLY_SIMILAR":
            fc, tc = raw_edge.get("from_col_id"), raw_edge.get("to_col_id")
            return (f"C:{fc}", f"C:{tc}") if fc and tc else None
        if t == "STRUCTURALLY_SIMILAR":
            ff, tf = raw_edge.get("from_fqn"), raw_edge.get("to_fqn")
            return (f"T:{ff}", f"T:{tf}") if ff and tf else None
        if t == "BRIDGES_TO":
            fc, tc = raw_edge.get("from_community_id"), raw_edge.get("to_community_id")
            return (f"COM:{fc}", f"COM:{tc}") if fc and tc else None
        return None

    _seen_raw_edges: set[tuple[int, int, str]] = {
        (_rec["r"]["start"], _rec["r"]["end"], _rec["r"]["type"])
        for _rec in records
        if _rec.get("r")
    }
    _raw_edge_count = 0
    for _re in raw_graph.get("edges") or []:
        _endpoints = _resolve_raw_edge_endpoints(_re)
        if not _endpoints:
            continue
        _src_key, _tgt_key = _endpoints
        # Only emit the edge if BOTH endpoints already exist as registered nodes.
        # ids._map is the authoritative set of node IDs assigned during sections 1–5b.
        if _src_key not in ids._map or _tgt_key not in ids._map:
            continue
        _src_id = ids._map[_src_key]
        _tgt_id = ids._map[_tgt_key]
        _rel_type = _re.get("_type", "RELATED_TO")
        _edge_key = (_src_id, _tgt_id, _rel_type)
        if _edge_key in _seen_raw_edges:
            continue
        _seen_raw_edges.add(_edge_key)
        _eprops = {k: v for k, v in _re.items() if not k.startswith("_")}
        _eprops["_retrieved_only"] = True
        # Emit edge-only triplet: addNode(None) is null-safe in the template.
        records.append(_triplet(
            None,
            _edge(ids.next_edge_id(), _rel_type, _src_id, _tgt_id, _eprops),
            None,
        ))
        _raw_edge_count += 1

    logger.info(
        "graph_context_builder | conv={} | records={} | raw_edges_wired={}",
        conversation_id, len(records), _raw_edge_count,
    )

    # 6. Compute server-side layout and embed positions before serialization
    _inject_layout(records, _compute_layout(records))

    # 6a. Mark nodes that appear in the final SQL
    if sql_string:
        sql_tables, sql_cols = _parse_sql_path(sql_string)
        _mark_sql_path(records, sql_tables, sql_cols)

    # 6b. Build feedback overlay (async, non-blocking on failure)
    feedback_overlay = await _build_feedback_overlay(thread_id_str)

    # 7. Inject into template
    template_html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if _PLACEHOLDER not in template_html:
        raise RuntimeError("graph_explorer_template.html is missing GRAPH_DATA_PLACEHOLDER")
    html = template_html.replace(_PLACEHOLDER, json.dumps(records, default=str))
    if _SQL_PLACEHOLDER in template_html:
        html = html.replace(_SQL_PLACEHOLDER, json.dumps(sql_string, default=str))
    if _FEEDBACK_PLACEHOLDER in template_html:
        html = html.replace(_FEEDBACK_PLACEHOLDER, json.dumps(feedback_overlay, default=str))

    # 8. Upload to S3
    short_id = str(conversation_id)[:8]
    today = date.today().isoformat()
    s3_key = f"graph-contexts/graph-{short_id}-{today}.html"
    s3_url = await _upload_to_s3(s3_key, html.encode("utf-8"))

    logger.info("graph_context_builder | uploaded | conv={} | key={}", conversation_id, s3_key)
    return s3_key, s3_url


# ── Legacy builder (backward compat for old snapshot format) ─────────────────

async def _generate_from_old_snapshot(
    snapshot: dict,
    conversation_id: uuid.UUID,
) -> tuple[str, str]:
    """Handle old-format snapshots that use 'path_tables' + 'selected_columns'.

    This preserves functionality for conversations recorded before the new
    snapshot format was deployed.
    """
    path_tables: list[str] = snapshot.get("path_tables") or []
    if not path_tables:
        raise ValueError(f"No graph_context.path_tables for conversation_id={conversation_id}")

    from app.services.agents import neo4j_client as nc
    loop = asyncio.get_event_loop()

    join_path_ids: list[str] = [pid for pid in (snapshot.get("join_path_ids") or []) if pid]
    snap_bt_terms = [bt.get("term") for bt in (snapshot.get("business_terms") or []) if bt.get("term")]
    snap_qp_ids   = [qp.get("id") for qp in (snapshot.get("query_patterns") or []) if qp.get("id")]
    snap_ap_ids   = [ap.get("id") for ap in (snapshot.get("anti_patterns") or []) if ap.get("id")]
    snap_tmpl_ids = [t.get("id") for t in (snapshot.get("templates") or []) if t.get("id")]

    (
        tables_ctx, columns_raw, joins_raw, relevant_intents,
        struct_similar, sem_similar_cols, join_paths_raw,
        bt_full, qp_full, ap_full, tmpl_full,
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

    community_ids = list({
        row["c"]["id"] for row in tables_ctx if row.get("c") and row["c"].get("id")
    })
    community_bridges = await loop.run_in_executor(None, nc.get_community_bridges, community_ids)

    # (abbreviated legacy build — just enough to not error)
    snapshot_tables = snapshot.get("tables") or []
    selected_columns = snapshot.get("selected_columns") or []
    join_clauses = snapshot.get("join_clauses") or []
    template_id = snapshot.get("template_id")

    def _merge(neo4j_list, snap_list, key):
        neo4j_map = {r.get(key): r for r in neo4j_list if r.get(key)}
        for s in snap_list:
            k = s.get(key)
            if k and k not in neo4j_map:
                neo4j_map[k] = s
        return list(neo4j_map.values())

    snapshot_business_terms = _merge(bt_full, snapshot.get("business_terms") or [], "term")
    snapshot_query_patterns = _merge(qp_full, snapshot.get("query_patterns") or [], "id")
    snapshot_anti_patterns  = _merge(ap_full, snapshot.get("anti_patterns") or [], "id")
    snapshot_templates      = _merge(tmpl_full, snapshot.get("templates") or [], "id")

    table_props_map: dict[str, dict] = {}
    for row in tables_ctx:
        t = row.get("t") or {}
        if t.get("fqn"):
            table_props_map[t["fqn"]] = t
    for st in snapshot_tables:
        fqn = st.get("fqn") or st.get("table_fqn")
        if fqn and fqn not in table_props_map:
            table_props_map[fqn] = st

    col_id_map = {col.get("id") or f"{col.get('table_fqn')}.{col.get('name')}": col for col in columns_raw}
    col_by_table_name = {(col.get("table_fqn", ""), col.get("name", "")): col for col in columns_raw}
    selected_set = {(sc.get("table_fqn", ""), sc.get("column_name", "")) for sc in selected_columns}

    domain_map: dict[str, dict] = {}
    community_map: dict[str, dict] = {}
    table_domain_map: dict[str, str] = {}
    table_community_map: dict[str, str] = {}
    for row in tables_ctx:
        t = row.get("t") or {}
        fqn = t.get("fqn")
        d, c = row.get("d"), row.get("c")
        if d and d.get("name"):
            domain_map[d["name"]] = d
            if fqn:
                table_domain_map[fqn] = d["name"]
        if c and c.get("id"):
            community_map[c["id"]] = c
            if fqn:
                table_community_map[fqn] = c["id"]

    intent_map: dict[str, dict] = {i.get("name", ""): i for i in (snapshot.get("intents") or []) if i.get("name")}
    for ri in relevant_intents:
        iname = ri.get("intent", {}).get("name", "")
        if iname:
            intent_map[iname] = ri["intent"]

    matched_template = next(
        (t for t in snapshot_templates if t.get("id") == template_id),
        snapshot_templates[0] if snapshot_templates else None,
    )

    ids = _IdRegistry()
    records: list[dict] = []

    def table_id(fqn): return ids.get(f"T:{fqn}")
    def col_id(cid): return ids.get(f"C:{cid}")
    def domain_id(name): return ids.get(f"D:{name}")
    def comm_id(cid): return ids.get(f"COM:{cid}")
    def intent_id(name): return ids.get(f"I:{name}")
    def bt_id(term): return ids.get(f"BT:{term}")
    def qp_id(qpid): return ids.get(f"QP:{qpid}")
    def ap_id(apid): return ids.get(f"AP:{apid}")
    def jp_id(jpid): return ids.get(f"JP:{jpid}")
    def template_id_fn(tid): return ids.get(f"QT:{tid}")
    def _t_props(fqn): return table_props_map.get(fqn, {"fqn": fqn, "name": fqn.split(".")[-1]})

    for col in columns_raw:
        t_fqn = col.get("table_fqn", "")
        cid = col.get("id") or f"{t_fqn}.{col.get('name')}"
        is_selected = (t_fqn, col.get("name", "")) in selected_set
        records.append(_triplet(
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            _edge(ids.next_edge_id(), "HAS_COLUMN", table_id(t_fqn), col_id(cid),
                  {"selected": is_selected}),
            _node(col_id(cid), ["Column"], col),
        ))

    seen_joins: set[tuple] = set()
    for j in joins_raw:
        f_fqn, t_fqn = j.get("from_fqn", ""), j.get("to_fqn", "")
        key = (f_fqn, t_fqn)
        if not f_fqn or not t_fqn or key in seen_joins:
            continue
        seen_joins.add(key)
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], _t_props(f_fqn)),
            _edge(ids.next_edge_id(), "JOINS_TO", table_id(f_fqn), table_id(t_fqn), j),
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
        ))

    for clause in join_clauses:
        pair = _parse_join_clause_tables(clause)
        if not pair:
            continue
        f_fqn, t_fqn = pair
        key = (f_fqn, t_fqn)
        if key in seen_joins or f_fqn == t_fqn:
            continue
        seen_joins.add(key)
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], _t_props(f_fqn)),
            _edge(ids.next_edge_id(), "JOINS_TO", table_id(f_fqn), table_id(t_fqn),
                  {"join_clause": clause, "source": "join_path"}),
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
        ))

    for ss in struct_similar:
        f_fqn, t_fqn = ss.get("from_fqn", ""), ss.get("to_fqn", "")
        if not f_fqn or not t_fqn:
            continue
        records.append(_triplet(
            _node(table_id(f_fqn), ["Table"], _t_props(f_fqn)),
            _edge(ids.next_edge_id(), "STRUCTURALLY_SIMILAR", table_id(f_fqn), table_id(t_fqn),
                  ss.get("rel") or {}),
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
        ))

    for fqn, dname in table_domain_map.items():
        records.append(_triplet(
            _node(table_id(fqn), ["Table"], _t_props(fqn)),
            _edge(ids.next_edge_id(), "BELONGS_TO", table_id(fqn), domain_id(dname), {}),
            _node(domain_id(dname), ["Domain"], domain_map.get(dname, {"name": dname})),
        ))

    for fqn, cid in table_community_map.items():
        c_props = community_map.get(cid, {"id": cid})
        records.append(_triplet(
            _node(comm_id(cid), ["Community"], c_props),
            _edge(ids.next_edge_id(), "CONTAINS_TABLE", comm_id(cid), table_id(fqn), {}),
            _node(table_id(fqn), ["Table"], _t_props(fqn)),
        ))

    for cb in community_bridges:
        from_cid, to_cid = cb.get("from_id", ""), cb.get("to_id", "")
        if not from_cid or not to_cid:
            continue
        records.append(_triplet(
            _node(comm_id(from_cid), ["Community"], community_map.get(from_cid, {"id": from_cid})),
            _edge(ids.next_edge_id(), "BRIDGES_TO", comm_id(from_cid), comm_id(to_cid),
                  cb.get("rel") or {}),
            _node(comm_id(to_cid), ["Community"], community_map.get(to_cid, {"id": to_cid})),
        ))

    for ri in relevant_intents:
        t_fqn = ri.get("table_fqn", "")
        iname = ri.get("intent", {}).get("name", "")
        if not t_fqn or not iname:
            continue
        i_props = intent_map.get(iname, {"name": iname})
        records.append(_triplet(
            _node(table_id(t_fqn), ["Table"], _t_props(t_fqn)),
            _edge(ids.next_edge_id(), "RELEVANT_TO", table_id(t_fqn), intent_id(iname),
                  ri.get("rel") or {}),
            _node(intent_id(iname), ["Intent"], i_props),
        ))

    if matched_template:
        tid = matched_template.get("id", template_id or "")
        qt_node = _node(template_id_fn(tid), ["QueryTemplate"], matched_template)
        for anchor_fqn in (matched_template.get("anchor_table_fqns") or []):
            records.append(_triplet(
                qt_node,
                _edge(ids.next_edge_id(), "REQUIRES_TABLE", template_id_fn(tid),
                      table_id(anchor_fqn), {}),
                _node(table_id(anchor_fqn), ["Table"], _t_props(anchor_fqn)),
            ))
        primary_intent_name = matched_template.get("primary_intent", "")
        if primary_intent_name and primary_intent_name in intent_map:
            records.append(_triplet(
                qt_node,
                _edge(ids.next_edge_id(), "CLASSIFIED_AS", template_id_fn(tid),
                      intent_id(primary_intent_name), {}),
                _node(intent_id(primary_intent_name), ["Intent"], intent_map[primary_intent_name]),
            ))

    for sc in sem_similar_cols:
        from_cid = sc.get("from_id", "")
        to_cid = sc.get("to_id", "")
        if not from_cid or not to_cid:
            continue
        records.append(_triplet(
            _node(col_id(from_cid), ["Column"], col_id_map.get(from_cid, {"id": from_cid})),
            _edge(ids.next_edge_id(), "SEMANTICALLY_SIMILAR", col_id(from_cid), col_id(to_cid),
                  sc.get("rel") or {}),
            _node(col_id(to_cid), ["Column"], col_id_map.get(to_cid, {"id": to_cid})),
        ))

    for bt in snapshot_business_terms:
        term = bt.get("term", "")
        if not term:
            continue
        bt_node = _node(bt_id(term), ["BusinessTerm"], bt)
        linked = False
        for fqn in path_tables:
            records.append(_triplet(
                bt_node,
                _edge(ids.next_edge_id(), "CONTEXT_RELEVANT", bt_id(term), table_id(fqn), {}),
                _node(table_id(fqn), ["Table"], _t_props(fqn)),
            ))
            linked = True
        if not linked:
            records.append(_triplet(bt_node, None, None))

    for qp in snapshot_query_patterns:
        qpid = qp.get("id", "") or qp.get("question_text", "")[:40]
        if not qpid:
            continue
        qp_node = _node(qp_id(qpid), ["QueryPattern"], qp)
        raw_tables = [t.strip() for t in (qp.get("tables_used") or "").split(",") if t.strip()]
        linked = False
        for fqn in raw_tables:
            records.append(_triplet(
                qp_node,
                _edge(ids.next_edge_id(), "USES_TABLE", qp_id(qpid), table_id(fqn), {}),
                _node(table_id(fqn), ["Table"], _t_props(fqn)),
            ))
            linked = True
        if not linked:
            records.append(_triplet(qp_node, None, None))

    for ap in snapshot_anti_patterns:
        apid = ap.get("id", "") or ap.get("error_type", "")
        if not apid:
            continue
        records.append(_triplet(_node(ap_id(apid), ["AntiPattern"], ap), None, None))

    for jp in join_paths_raw:
        jpid = jp.get("id", "")
        if not jpid:
            continue
        jp_node = _node(jp_id(jpid), ["JoinPath"], jp)
        linked = False
        for fqn in (jp.get("path_tables") or []):
            records.append(_triplet(
                jp_node,
                _edge(ids.next_edge_id(), "LINKS_TABLE", jp_id(jpid), table_id(fqn), {}),
                _node(table_id(fqn), ["Table"], _t_props(fqn)),
            ))
            linked = True
        if not linked:
            records.append(_triplet(jp_node, None, None))

    logger.info("graph_context_builder | legacy | conv={} | records={}", conversation_id, len(records))

    _inject_layout(records, _compute_layout(records))

    template_html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if _PLACEHOLDER not in template_html:
        raise RuntimeError("graph_explorer_template.html is missing GRAPH_DATA_PLACEHOLDER")
    html = template_html.replace(_PLACEHOLDER, json.dumps(records, default=str))

    short_id = str(conversation_id)[:8]
    today = date.today().isoformat()
    s3_key = f"graph-contexts/graph-{short_id}-{today}.html"
    s3_url = await _upload_to_s3(s3_key, html.encode("utf-8"))
    logger.info("graph_context_builder | legacy uploaded | conv={} | key={}", conversation_id, s3_key)
    return s3_key, s3_url
