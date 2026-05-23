"""
GDS algorithm wrappers.

Execution order:
  1. WCC       → wcc_component_id on Table
  1a. Bridge   → add JOINS_TO edges for isolated tables
  1b. WCC re-run
  2. PageRank  → pagerank_score on Table
  3. Betweenness → betweenness_score on Table
  4. Leiden    → community_id on Table + Community nodes
  5. FastRP    → used internally by Node Similarity (not stored)
  6. Node Similarity → STRUCTURALLY_SIMILAR edges
  9. KNN       → SEMANTICALLY_SIMILAR edges (after embeddings)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from neo4j import GraphDatabase
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_LEIDEN_GAMMA_DEFAULT = 1.2
_NOW = lambda: datetime.now(timezone.utc).isoformat()


class GDSPipeline:
    def __init__(self, uri: str, user: str, password: str, db: str):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = db

    def close(self):
        self._driver.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _run(self, cypher: str, **params) -> list[dict]:
        with self._driver.session(database=self._db) as s:
            return s.run(cypher, **params).data()

    def _drop_graph(self, name: str):
        exists = self._run("CALL gds.graph.exists($name) YIELD exists RETURN exists AS e", name=name)
        if exists and exists[0].get("e"):
            self._run("CALL gds.graph.drop($name) YIELD graphName", name=name)

    # ── Graph projections ──────────────────────────────────────────────────

    def project_join_graph(self):
        self._drop_graph("join_graph")
        self._run("""
            CALL gds.graph.project(
                'join_graph',
                'Table',
                {
                    JOINS_TO: {
                        orientation: 'UNDIRECTED',
                        properties: {
                            join_cost: { defaultValue: 2.0 }
                        }
                    }
                }
            )
        """)
        log.info("Projected join_graph.")

    def project_leiden_graph(self):
        self._drop_graph("leiden_graph")
        self._run("""
            CALL gds.graph.project(
                'leiden_graph',
                'Table',
                {
                    JOINS_TO: {
                        orientation: 'UNDIRECTED',
                        properties: { leiden_weight: { defaultValue: 1.0 } }
                    }
                }
            )
        """)
        log.info("Projected leiden_graph.")

    def project_column_knn(self):
        self._drop_graph("column_knn")
        self._run("""
            CALL gds.graph.project(
                'column_knn',
                { Column: { properties: ['cohere_embedding'] } },
                '*'
            )
        """)
        log.info("Projected column_knn.")

    # ── WCC ────────────────────────────────────────────────────────────────

    def run_wcc(self) -> dict:
        result = self._run("""
            CALL gds.wcc.write('join_graph', {
                writeProperty: 'wcc_component_id'
            })
            YIELD componentCount, componentDistribution
            RETURN componentCount, componentDistribution
        """)
        r = result[0] if result else {}
        log.info("WCC: %d components.", r.get("componentCount", 0))
        return r

    def get_wcc_report(self) -> dict:
        main_cid_rows = self._run("""
            MATCH (t:Table) WHERE t.wcc_component_id IS NOT NULL
            WITH t.wcc_component_id AS cid, count(*) AS n
            ORDER BY n DESC LIMIT 1
            RETURN cid
        """)
        if not main_cid_rows:
            return {"main_component_id": -1, "isolated": [], "small_clusters": [], "pendants": []}
        main_cid = main_cid_rows[0]["cid"]

        isolated = self._run("""
            MATCH (t:Table) WHERE t.wcc_component_id IS NOT NULL
            WITH t.wcc_component_id AS cid, collect(t) AS members
            WHERE size(members) = 1
            UNWIND members AS t
            RETURN t.fqn AS fqn, t.table_type AS table_type,
                   t.row_count AS row_count, t.wcc_component_id AS cid
            ORDER BY t.row_count DESC
        """)

        small_clusters = self._run("""
            MATCH (t:Table) WHERE t.wcc_component_id IS NOT NULL
              AND t.wcc_component_id <> $main_cid
            WITH t.wcc_component_id AS cid, collect(t) AS members
            WHERE size(members) >= 2 AND size(members) <= 5
            RETURN cid, size(members) AS cluster_size,
                   [m IN members | m.fqn] AS tables,
                   [m IN members | m.table_type] AS types
            ORDER BY cluster_size
        """, main_cid=main_cid)

        pendants = self._run("""
            MATCH (t:Table) WHERE t.wcc_component_id = $main_cid
            WITH t,
                 size([(t)-[:JOINS_TO]-()  | 1]) +
                 size([(t)<-[:JOINS_TO]-() | 1]) AS degree
            WHERE degree = 1
            RETURN t.fqn AS fqn, t.table_type AS table_type, degree
            ORDER BY t.row_count DESC
        """, main_cid=main_cid)

        return {
            "main_component_id": main_cid,
            "isolated": isolated,
            "small_clusters": small_clusters,
            "pendants": pendants,
        }

    def flag_isolated_tables(self):
        self._run("""
            MATCH (t:Table)
            WHERE NOT EXISTS { (t)-[:JOINS_TO]-() }
              AND NOT EXISTS { ()-[:JOINS_TO]->(t) }
            SET t.is_isolated = true,
                t.isolation_reason = 'no_fk_no_view_no_semantic_bridge'
        """)

    # ── PageRank ───────────────────────────────────────────────────────────

    def run_pagerank(self) -> dict:
        result = self._run("""
            CALL gds.pageRank.write('join_graph', {
                writeProperty:              'pagerank_score',
                maxIterations:              20,
                dampingFactor:              0.85,
                relationshipWeightProperty: 'join_cost'
            })
            YIELD nodePropertiesWritten, ranIterations
            RETURN nodePropertiesWritten, ranIterations
        """)
        r = result[0] if result else {}
        log.info("PageRank: wrote to %d nodes.", r.get("nodePropertiesWritten", 0))
        return r

    # ── Betweenness ────────────────────────────────────────────────────────

    def run_betweenness(self) -> dict:
        result = self._run("""
            CALL gds.betweenness.write('join_graph', {
                writeProperty: 'betweenness_score'
            })
            YIELD nodePropertiesWritten
            RETURN nodePropertiesWritten
        """)
        r = result[0] if result else {}
        log.info("Betweenness: wrote to %d nodes.", r.get("nodePropertiesWritten", 0))
        return r

    # ── Leiden ─────────────────────────────────────────────────────────────

    def run_leiden(self, gamma: float = _LEIDEN_GAMMA_DEFAULT) -> dict:
        result = self._run("""
            CALL gds.leiden.write('leiden_graph', {
                writeProperty:              'community_id',
                gamma:                      $gamma,
                maxLevels:                  10,
                randomSeed:                 42,
                relationshipWeightProperty: 'leiden_weight'
            })
            YIELD communityCount, modularity, ranLevels
            RETURN communityCount, modularity, ranLevels
        """, gamma=gamma)
        r = result[0] if result else {}
        log.info(
            "Leiden: %d communities, modularity=%.4f, gamma=%.2f",
            r.get("communityCount", 0), r.get("modularity", 0), gamma,
        )
        return {**r, "gamma": gamma}

    def validate_leiden(self) -> dict:
        total = self._run("MATCH (t:Table) RETURN count(t) AS n")[0]["n"] or 1

        community_sizes = self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
            WITH t.community_id AS cid, count(*) AS n
            ORDER BY n DESC
            RETURN cid, n
        """)

        cross_edge_rows = self._run("""
            MATCH (a:Table)-[r:JOINS_TO]->(b:Table)
            WHERE a.community_id <> b.community_id
            RETURN count(r) AS cross_edges
        """)
        total_edges = self._run("MATCH ()-[r:JOINS_TO]->() RETURN count(r) AS n")[0]["n"] or 1
        cross_edges = cross_edge_rows[0]["cross_edges"] if cross_edge_rows else 0
        cross_pct = round(cross_edges / total_edges * 100, 1)

        max_community_pct = 0.0
        if community_sizes:
            max_community_pct = round(community_sizes[0]["n"] / total * 100, 1)

        ok = max_community_pct <= 30 and 15 <= cross_pct <= 50
        log.info(
            "Leiden validation: max_community=%.1f%% cross_edges=%.1f%% ok=%s",
            max_community_pct, cross_pct, ok,
        )
        return {
            "ok": ok,
            "max_community_pct": max_community_pct,
            "cross_edge_pct": cross_pct,
            "community_sizes": community_sizes[:20],
        }

    def build_community_nodes(self, leiden_meta: dict):
        """Create Community nodes and link tables to them."""
        now   = _NOW()
        gamma = leiden_meta.get("gamma", _LEIDEN_GAMMA_DEFAULT)

        # Step 1 — MERGE Community nodes (must exist before domain voting can SET on them)
        communities_raw = self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
            WITH t.community_id AS cid, count(t) AS n
            RETURN cid, n
        """)
        for row in communities_raw:
            cid, n = row["cid"], row["n"]
            self._run("""
                MERGE (c:Community {id: $cid})
                ON CREATE SET c.dominant_domain = 'unknown'
                SET c.table_count  = $n,
                    c.leiden_gamma = $gamma,
                    c.run_date     = $now
            """, cid=cid, n=n, gamma=gamma, now=now)

        # Step 2 — Link tables to communities
        self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
            MATCH (c:Community {id: t.community_id})
            MERGE (c)-[:CONTAINS_TABLE]->(t)
        """)

        # Step 3 — PageRank-weighted domain voting (Community nodes now exist)
        self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
              AND t.business_domain IS NOT NULL
            WITH t.community_id AS cid,
                 t.business_domain AS dom,
                 sum(coalesce(t.pagerank_score, 0.001)) AS w
            ORDER BY w DESC
            WITH cid, collect({domain: dom, weight: w}) AS ranked
            WITH cid, ranked,
                 reduce(s = 0.0, r IN ranked | s + r.weight) AS total_w
            MATCH (c:Community {id: cid})
            SET c.dominant_domain            = ranked[0].domain,
                c.domain_distribution        = [r IN ranked |
                    r.domain + ':' + toString(round(r.weight * 1000) / 1000.0)],
                c.dominant_domain_confidence = CASE WHEN total_w = 0 THEN 0.0
                    ELSE round(ranked[0].weight / total_w * 100) / 100.0 END
        """)

        log.info("Created/updated %d Community nodes.", len(communities_raw))

    def build_bridges_to_edges(self):
        """Create BRIDGES_TO edges between Community nodes that share cross-community JOINS_TO.

        bridge_table_fqn is the highest-PageRank table in cid_a that actually has a
        JOINS_TO edge to cid_b — not just any high-PageRank table in the source community.
        """
        now = _NOW()
        self._run("""
            MATCH (a:Table)-[j:JOINS_TO]->(b:Table)
            WHERE a.community_id IS NOT NULL
              AND b.community_id IS NOT NULL
              AND a.community_id <> b.community_id
            WITH a.community_id AS cid_a, b.community_id AS cid_b, count(j) AS cross_edges

            MATCH (bridge:Table)-[:JOINS_TO]->(target:Table)
            WHERE bridge.community_id = cid_a AND target.community_id = cid_b
            WITH cid_a, cid_b, cross_edges, bridge
            ORDER BY coalesce(bridge.pagerank_score, 0) DESC
            WITH cid_a, cid_b, cross_edges, head(collect(bridge.fqn)) AS bridge_fqn

            MATCH (ca:Community {id: cid_a})
            MATCH (cb:Community {id: cid_b})
            MERGE (ca)-[br:BRIDGES_TO]->(cb)
            SET br.bridge_table_fqn = bridge_fqn,
                br.cross_edge_count = cross_edges,
                br.computed_at      = $now
        """, now=now)
        result = self._run("MATCH ()-[r:BRIDGES_TO]->() RETURN count(r) AS n")
        log.info("BRIDGES_TO edges: %d", result[0]["n"] if result else 0)

    # ── FastRP ─────────────────────────────────────────────────────────────

    def run_fastrp_for_node_similarity(self):
        """
        Run FastRP to produce structural embeddings in-memory.
        These are NOT stored on nodes — used directly by Node Similarity.
        """
        self._drop_graph("fastrp_graph")
        self._run("""
            CALL gds.graph.project(
                'fastrp_graph',
                'Table',
                { JOINS_TO: { orientation: 'UNDIRECTED' } }
            )
        """)
        self._run("""
            CALL gds.fastRP.mutate('fastrp_graph', {
                embeddingDimension:   128,
                iterationWeights:     [0.8, 1.0, 1.0],
                normalizationStrength: -0.5,
                mutateProperty:       'fastrp_embedding',
                randomSeed:           42
            })
            YIELD nodePropertiesWritten
        """)
        log.info("FastRP mutation done — ready for Node Similarity.")

    # ── Node Similarity ────────────────────────────────────────────────────

    def run_node_similarity(self, cutoff: float = 0.5, top_k: int = 10) -> dict:
        """
        Runs on fastrp_graph (after FastRP mutate).
        Writes STRUCTURALLY_SIMILAR edges between Table nodes.
        """
        try:
            result = self._run("""
                CALL gds.nodeSimilarity.write('fastrp_graph', {
                    writeRelationshipType: 'STRUCTURALLY_SIMILAR',
                    writeProperty:         'similarity',
                    similarityCutoff:      $cutoff,
                    topK:                  $top_k
                })
                YIELD relationshipsWritten, nodesCompared
                RETURN relationshipsWritten, nodesCompared
            """, cutoff=cutoff, top_k=top_k)
            r = result[0] if result else {}
            log.info(
                "Node Similarity: %d STRUCTURALLY_SIMILAR edges written.",
                r.get("relationshipsWritten", 0),
            )
            return r
        finally:
            self._drop_graph("fastrp_graph")

    # ── KNN on column embeddings ───────────────────────────────────────────

    def run_knn(self, cutoff: float = 0.88) -> dict:
        self.project_column_knn()
        result = self._run("""
            CALL gds.knn.write('column_knn', {
                nodeProperties:         ['cohere_embedding'],
                topK:                   5,
                writeRelationshipType:  'SEMANTICALLY_SIMILAR',
                writeProperty:          'similarity',
                similarityCutoff:       $cutoff,
                randomSeed:             42,
                concurrency:            1
            })
            YIELD relationshipsWritten, nodesCompared
            RETURN relationshipsWritten, nodesCompared
        """, cutoff=cutoff)
        self._drop_graph("column_knn")
        r = result[0] if result else {}
        log.info(
            "KNN: %d SEMANTICALLY_SIMILAR edges written.",
            r.get("relationshipsWritten", 0),
        )
        return r

    # ── WCC bridging helpers ───────────────────────────────────────────────

    def get_isolated_fqns(self) -> list[str]:
        rows = self._run("""
            MATCH (t:Table)
            WHERE NOT EXISTS { (t)-[:JOINS_TO]-() }
              AND NOT EXISTS { ()-[:JOINS_TO]->(t) }
            RETURN t.fqn AS fqn
        """)
        return [r["fqn"] for r in rows]

    def get_small_cluster_fqns(self, main_cid: int) -> list[str]:
        rows = self._run("""
            MATCH (t:Table)
            WHERE t.wcc_component_id IS NOT NULL
              AND t.wcc_component_id <> $main_cid
            WITH t.wcc_component_id AS cid, collect(t.fqn) AS fqns
            WHERE size(fqns) >= 2 AND size(fqns) <= 5
            UNWIND fqns AS fqn
            RETURN fqn
        """, main_cid=main_cid)
        return [r["fqn"] for r in rows]

    def get_main_wcc_id(self) -> int:
        rows = self._run("""
            MATCH (t:Table) WHERE t.wcc_component_id IS NOT NULL
            WITH t.wcc_component_id AS cid, count(*) AS n
            ORDER BY n DESC LIMIT 1
            RETURN cid
        """)
        return rows[0]["cid"] if rows else -1

    def load_wcc_bridge_edges(self, bridges: list[dict]):
        """bridges: list of dicts with from_fqn, from_col, to_fqn, to_col, confidence, source"""
        if not bridges:
            return
        now = _NOW()
        rows = [{**b, "created_at": now, "updated_at": now} for b in bridges]
        with self._driver.session(database=self._db) as s:
            s.run("""
                UNWIND $rows AS r
                MATCH (a:Table {fqn: r.from_fqn})
                MATCH (b:Table {fqn: r.to_fqn})
                MERGE (a)-[j:JOINS_TO {from_col: r.from_col, to_col: r.to_col}]->(b)
                ON CREATE SET
                    j.confidence    = r.confidence,
                    j.join_cost     = 1.0 / r.confidence,
                    j.leiden_weight = 0.6,
                    j.is_declared   = false,
                    j.is_ontology   = false,
                    j.is_wcc_bridge = true,
                    j.source        = r.source,
                    j.frequency     = 1,
                    j.created_at    = r.created_at,
                    j.updated_at    = r.updated_at
                ON MATCH SET
                    j.is_wcc_bridge = true,
                    j.updated_at    = r.updated_at
            """, rows=rows)
        log.info("Loaded %d WCC bridge edges.", len(bridges))
