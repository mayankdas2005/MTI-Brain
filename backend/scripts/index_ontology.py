"""index_ontology.py

Offline script — parse lpp-ontology-enriched.ttl + lpp-r2rml.ttl, embed every
class and property via Bedrock Cohere Embed, and upsert into Postgres pgvector.

Run once (or after re-enrichment):
    python backend/scripts/index_ontology.py
    python backend/scripts/index_ontology.py --wipe   # drop + recreate table first
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import boto3
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"

ONTOLOGY_TTL = DATA_DIR / "lpp-ontology-enriched.ttl"
R2RML_TTL    = DATA_DIR / "lpp-r2rml.ttl"

ENV_FILE = BASE_DIR / ".env"



load_dotenv(dotenv_path=ENV_FILE)


def _pg_conn():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=15,
        sslmode=os.environ.get("DATABASE_SSL_MODE", "disable"),
    )


def _bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip("'"),
    )


COHERE_BATCH = 96


def _embed_batch(client, texts: list[str], input_type: str = "search_document") -> list[list[float]]:
    arn = os.environ.get("AWS_BEDROCK_COHERE_EMBED_V4_ARN", "")
    if not arn:
        raise RuntimeError("AWS_BEDROCK_COHERE_EMBED_V4_ARN not set in .env")
    body = json.dumps({"texts": texts, "input_type": input_type})
    resp = client.invoke_model(modelId=arn, body=body,
                               contentType="application/json", accept="*/*")
    result = json.loads(resp["body"].read())
    embs = result["embeddings"]
    return embs["float"] if isinstance(embs, dict) else embs


def _embed_all(client, texts: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), COHERE_BATCH):
        batch = texts[i: i + COHERE_BATCH]
        embeddings.extend(_embed_batch(client, batch, "search_document"))
        print(f"  embedded {min(i + COHERE_BATCH, len(texts))}/{len(texts)}")
    return embeddings


def _parse_ontology() -> tuple[list[dict], list[dict], dict[str, list[str]]]:
    from rdflib import Graph, Namespace, RDF, RDFS, OWL
    from rdflib.namespace import SKOS

    g = Graph()
    g.parse(str(ONTOLOGY_TTL), format="turtle")

    LPP   = Namespace("https://lpp.example/ontology#")
    BRAIN = Namespace("https://lpp.example/ontology/brain#")
    PROV  = Namespace("http://www.w3.org/ns/prov#")

    def _s(node, fallback="") -> str:
        if node is None:
            return fallback
        s = str(node).split("@")[0].strip()
        return s

    def _local(uri: str) -> str:
        for sep in ("#", "/"):
            if sep in uri:
                return uri.rsplit(sep, 1)[-1]
        return uri

    concept_vals: dict[str, list[str]] = {}
    for s, _, cls in g.triples((None, RDF.type, None)):
        cls_str = str(cls)
        if "lpp.example/ontology" not in cls_str:
            continue
        parent = _local(cls_str)
        pref = g.value(s, SKOS.prefLabel)
        if pref:
            concept_vals.setdefault(parent, [])
            v = _s(pref)
            if v not in concept_vals[parent]:
                concept_vals[parent].append(v)

    classes: list[dict] = []
    for s, _, _ in g.triples((None, RDF.type, OWL.Class)):
        uri = str(s)
        if "lpp.example/ontology" not in uri:
            continue
        local = _local(uri)
        comment = _s(g.value(s, RDFS.comment))
        parents = [_local(str(o)) for _, _, o in g.triples((s, RDFS.subClassOf, None))]
        enums = concept_vals.get(local, [])
        embed_text = f"{local}: {comment}"
        if parents:
            embed_text += f" | Parents: {', '.join(parents)}"
        if enums:
            embed_text += f" | Values: {', '.join(enums[:20])}"
        classes.append({
            "uri": uri,
            "local_name": local,
            "node_type": "class",
            "prop_kind": None,
            "label": _s(g.value(s, RDFS.label), local),
            "comment": comment,
            "subclass_of": parents,
            "domain_uris": [],
            "range_uri": None,
            "named_graph": None,
            "embed_text": embed_text,
        })

    properties: list[dict] = []
    for prop_type, kind in [(OWL.ObjectProperty, "object"), (OWL.DatatypeProperty, "datatype")]:
        for s, _, _ in g.triples((None, RDF.type, prop_type)):
            uri = str(s)
            if "lpp.example/ontology" not in uri and "brain#" not in uri:
                continue
            local = _local(uri)
            comment = _s(g.value(s, RDFS.comment))
            domain_uris = [str(o) for _, _, o in g.triples((s, RDFS.domain, None))]
            range_node = g.value(s, RDFS.range)
            range_uri = str(range_node) if range_node else None
            domain_locals = [_local(u) for u in domain_uris]
            range_local = _local(range_uri) if range_uri else ""
            embed_text = f"{local}: {comment}"
            if domain_locals:
                embed_text += f" | Domain: {', '.join(domain_locals)}"
            if range_local:
                embed_text += f" | Range: {range_local}"
            properties.append({
                "uri": uri,
                "local_name": local,
                "node_type": "property",
                "prop_kind": kind,
                "label": _s(g.value(s, RDFS.label), local),
                "comment": comment,
                "subclass_of": [],
                "domain_uris": domain_uris,
                "range_uri": range_uri,
                "named_graph": None,
                "embed_text": embed_text,
            })

    return classes, properties, concept_vals


def _extract_named_graphs(classes: list[dict]) -> None:
    if not R2RML_TTL.exists():
        print("  [warn] lpp-r2rml.ttl not found — named_graph will be null")
        return

    from rdflib import Graph, Namespace, URIRef
    RR = Namespace("http://www.w3.org/ns/r2rml#")
    g = Graph()
    g.parse(str(R2RML_TTL), format="turtle")

    uri_to_graph: dict[str, str] = {}

    for tm in set(g.subjects(RR.subjectMap, None)):
        sm = g.value(tm, RR.subjectMap)
        if sm is None:
            continue
        cls_uri = g.value(sm, RR["class"])
        if cls_uri is None:
            continue
        cls_str = str(cls_uri)

        graph_map = g.value(tm, RR.graphMap)
        if graph_map is not None:
            const = g.value(graph_map, RR.constant)
            if const:
                uri_to_graph[cls_str] = str(const)
                continue
        graph_const = g.value(tm, RR.graph)
        if graph_const is not None:
            uri_to_graph[cls_str] = str(graph_const)

    DEFAULT_GRAPH = "graph:treasury:all"
    cls_by_uri = {c["uri"]: c for c in classes}
    for cls in classes:
        cls["named_graph"] = uri_to_graph.get(cls["uri"], DEFAULT_GRAPH)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ontology_nodes (
    id          BIGSERIAL PRIMARY KEY,
    uri         TEXT NOT NULL UNIQUE,
    local_name  TEXT NOT NULL,
    node_type   TEXT NOT NULL,
    prop_kind   TEXT,
    label       TEXT,
    comment     TEXT,
    subclass_of TEXT[],
    domain_uris TEXT[],
    range_uri   TEXT,
    named_graph TEXT,
    embedding   vector({dim}),
    metadata    JSONB DEFAULT '{{}}'
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_onto_embedding ON ontology_nodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)",
    "CREATE INDEX IF NOT EXISTS idx_onto_domain    ON ontology_nodes USING GIN (domain_uris)",
    "CREATE INDEX IF NOT EXISTS idx_onto_subclass  ON ontology_nodes USING GIN (subclass_of)",
    "CREATE INDEX IF NOT EXISTS idx_onto_type      ON ontology_nodes (node_type)",
]

UPSERT_SQL = """
INSERT INTO ontology_nodes
  (uri, local_name, node_type, prop_kind, label, comment,
   subclass_of, domain_uris, range_uri, named_graph, embedding)
VALUES
  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
ON CONFLICT (uri) DO UPDATE SET
  local_name  = EXCLUDED.local_name,
  node_type   = EXCLUDED.node_type,
  prop_kind   = EXCLUDED.prop_kind,
  label       = EXCLUDED.label,
  comment     = EXCLUDED.comment,
  subclass_of = EXCLUDED.subclass_of,
  domain_uris = EXCLUDED.domain_uris,
  range_uri   = EXCLUDED.range_uri,
  named_graph = EXCLUDED.named_graph,
  embedding   = EXCLUDED.embedding
"""


def _upsert_nodes(conn, nodes: list[dict], embeddings: list[list[float]]) -> None:
    rows = []
    for node, emb in zip(nodes, embeddings):
        rows.append((
            node["uri"],
            node["local_name"],
            node["node_type"],
            node["prop_kind"],
            node["label"],
            node["comment"],
            node["subclass_of"],
            node["domain_uris"],
            node["range_uri"],
            node["named_graph"],
            str(emb),
        ))
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, UPSERT_SQL, rows, page_size=50)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wipe", action="store_true",
                        help="Drop and recreate ontology_nodes table")
    args = parser.parse_args()


    print("Parsing enriched TTL ...")
    classes, properties, _ = _parse_ontology()
    _extract_named_graphs(classes)
    all_nodes = classes + properties
    print(f"  {len(classes)} classes  |  {len(properties)} properties  |  {len(all_nodes)} total")

    texts = [n["embed_text"] for n in all_nodes]

    print("\nEmbedding via Bedrock Cohere ...")
    bedrock = _bedrock_client()
    embeddings = _embed_all(bedrock, texts)
    dim = len(embeddings[0])
    print(f"  dimension={dim}")

    print("\nConnecting to Postgres ...")
    conn = _pg_conn()
    print("  connected")

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        if args.wipe:
            cur.execute("DROP TABLE IF EXISTS ontology_nodes")
            print("  dropped existing table")
        cur.execute(CREATE_TABLE_SQL.format(dim=dim))
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
    conn.commit()
    print(f"  table ready (dim={dim})")

    print("\nUpserting nodes ...")
    _upsert_nodes(conn, all_nodes, embeddings)
    conn.close()

    print(f"\nDone — {len(all_nodes)} nodes indexed ({len(classes)} classes, {len(properties)} properties)")


if __name__ == "__main__":
    main()
