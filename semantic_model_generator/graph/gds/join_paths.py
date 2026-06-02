"""
Pre-compute join paths between table pairs.

Dijkstra  — single shortest path (join_cost weight), stored as JoinPath nodes
Yen's     — K=3 alternative paths per pair, stored with k_rank
Steiner   — on-demand minimal subgraph for 3-5 anchor tables (not precomputed)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import combinations

from neo4j import GraphDatabase
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)
_NOW = lambda: datetime.now(timezone.utc).isoformat()


class JoinPathBuilder:
    def __init__(self, uri: str, user: str, password: str, db: str):
        self._driver = GraphDatabase.driver(
            uri, auth=(user, password),
            keep_alive=True,
            connection_timeout=120,
            max_transaction_retry_time=300,
        )
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

    def _get_community_pairs(self) -> list[tuple[str, str]]:
        """All within-community table pairs (same community_id)."""
        rows = self._run("""
            MATCH (t:Table) WHERE t.community_id IS NOT NULL
            WITH t.community_id AS cid, collect(t.fqn) AS fqns
            WHERE size(fqns) >= 2
            RETURN fqns
        """)
        pairs = []
        for row in rows:
            fqns = row["fqns"]
            for a, b in combinations(fqns, 2):
                pairs.append((a, b))
        return pairs

    # ── Join clause lookup ─────────────────────────────────────────────────

    def _get_join_clauses_for_path(self, path_tables: list[str]) -> list[str]:
        """
        Look up from_col/to_col for each consecutive pair in a path.
        Returns SQL JOIN predicates ordered by traversal direction:
        each clause is always '{left_table}.{col} = {right_table}.{col}'
        where left_table = path_tables[i] and right_table = path_tables[i+1].
        GDS virtual path relationships only carry projected numeric properties
        (join_cost), not string properties — so we query the real edges here.
        """
        clauses: list[str] = []
        for i in range(len(path_tables) - 1):
            a, b = path_tables[i], path_tables[i + 1]
            rows = self._run("""
                MATCH (ta:Table {fqn: $a})-[r:JOINS_TO]-(tb:Table {fqn: $b})
                WHERE r.from_col IS NOT NULL AND r.to_col IS NOT NULL
                RETURN r.from_col AS fc, r.to_col AS tc,
                       startNode(r).fqn AS from_fqn, endNode(r).fqn AS to_fqn
                LIMIT 1
            """, a=a, b=b)
            if rows:
                row = rows[0]
                # Always emit clause in traversal order: a.col = b.col
                if row["from_fqn"] == a:
                    clause = f"{a}.{row['fc']} = {b}.{row['tc']}"
                else:
                    clause = f"{a}.{row['tc']} = {b}.{row['fc']}"
                clauses.append(clause)
            else:
                clauses.append(f"/* no edge found between {a} and {b} */")
        return clauses

    # ── Dijkstra ───────────────────────────────────────────────────────────

    def _project_dijkstra_graph(self):
        self._drop_graph("dijkstra_graph")
        self._run("""
            CALL gds.graph.project(
                'dijkstra_graph',
                { Table: {} },
                {
                    JOINS_TO: {
                        orientation: 'UNDIRECTED',
                        properties: { join_cost: { defaultValue: 2.0 } }
                    }
                }
            )
        """)

    def run_dijkstra_all_pairs(self, max_hops: int = 6):
        """Precompute Dijkstra paths for all within-community table pairs."""
        self._project_dijkstra_graph()
        pairs = self._get_community_pairs()
        log.info("Precomputing Dijkstra paths for %d pairs …", len(pairs))

        saved = 0
        now = _NOW()
        for src, dst in pairs:
            result = self._run("""
                MATCH (src:Table {fqn: $src})
                MATCH (dst:Table {fqn: $dst})
                CALL gds.shortestPath.dijkstra.stream('dijkstra_graph', {
                    sourceNode:                src,
                    targetNode:                dst,
                    relationshipWeightProperty:'join_cost'
                })
                YIELD path, totalCost
                RETURN
                    [n IN nodes(path) | n.fqn] AS path_tables,
                    totalCost,
                    size(nodes(path)) - 1      AS hop_count
                LIMIT 1
            """, src=src, dst=dst)

            if not result:
                continue
            r = result[0]
            if max_hops is not None and r["hop_count"] > max_hops:
                continue

            join_clauses = self._get_join_clauses_for_path(r["path_tables"])
            path_id = f"dijkstra::{src}::{dst}"
            self._run("""
                MERGE (jp:JoinPath {id: $id})
                SET jp.from_fqn     = $from_fqn,
                    jp.to_fqn       = $to_fqn,
                    jp.path_tables  = $path_tables,
                    jp.join_clauses = $join_clauses,
                    jp.total_cost   = $total_cost,
                    jp.hop_count    = $hop_count,
                    jp.algorithm    = 'dijkstra',
                    jp.k_rank       = 1,
                    jp.source_hash  = $id,
                    jp.created_at   = $now,
                    jp.updated_at   = $now
            """, id=path_id, from_fqn=src, to_fqn=dst,
                 path_tables=r["path_tables"],
                 join_clauses=join_clauses,
                 total_cost=r["totalCost"],
                 hop_count=r["hop_count"],
                 now=now)
            saved += 1

        self._drop_graph("dijkstra_graph")
        log.info("Dijkstra: saved %d JoinPath nodes.", saved)

    # ── Yen's K-Shortest ───────────────────────────────────────────────────

    def run_yens_all_pairs(self, k: int = 3, max_hops: int = 6):
        """Precompute Yen's K-shortest paths for all within-community pairs."""
        self._project_dijkstra_graph()  # reuse same projection
        pairs = self._get_community_pairs()
        log.info("Precomputing Yen's K=%d paths for %d pairs …", k, len(pairs))

        saved = 0
        now = _NOW()
        for src, dst in pairs:
            results = self._run("""
                MATCH (src:Table {fqn: $src})
                MATCH (dst:Table {fqn: $dst})
                CALL gds.shortestPath.yens.stream('dijkstra_graph', {
                    sourceNode:                src,
                    targetNode:                dst,
                    k:                         $k,
                    relationshipWeightProperty:'join_cost'
                })
                YIELD path, totalCost, index
                RETURN
                    [n IN nodes(path) | n.fqn] AS path_tables,
                    totalCost,
                    size(nodes(path)) - 1      AS hop_count,
                    index + 1                  AS k_rank
            """, src=src, dst=dst, k=k)

            for r in results:
                if max_hops is not None and r["hop_count"] > max_hops:
                    continue
                if r["k_rank"] == 1:
                    continue  # Dijkstra already saved k_rank=1

                join_clauses = self._get_join_clauses_for_path(r["path_tables"])
                path_id = f"yens::{src}::{dst}::k{r['k_rank']}"
                self._run("""
                    MERGE (jp:JoinPath {id: $id})
                    SET jp.from_fqn     = $from_fqn,
                        jp.to_fqn       = $to_fqn,
                        jp.path_tables  = $path_tables,
                        jp.join_clauses = $join_clauses,
                        jp.total_cost   = $total_cost,
                        jp.hop_count    = $hop_count,
                        jp.algorithm    = 'yens',
                        jp.k_rank       = $k_rank,
                        jp.source_hash  = $id,
                        jp.created_at   = $now,
                        jp.updated_at   = $now
                """, id=path_id, from_fqn=src, to_fqn=dst,
                     path_tables=r["path_tables"],
                     join_clauses=join_clauses,
                     total_cost=r["totalCost"],
                     hop_count=r["hop_count"],
                     k_rank=r["k_rank"],
                     now=now)
                saved += 1

        self._drop_graph("dijkstra_graph")
        log.info("Yen's: saved %d additional JoinPath nodes.", saved)

    # ── Steiner Tree (on-demand) ───────────────────────────────────────────

    def steiner_tree(self, anchor_fqns: list[str]) -> dict:
        """
        On-demand minimal subgraph connecting all anchor tables.
        Uses GDS Steiner tree. Returns join plan dict.
        Used at query time for multi-domain queries.
        """
        if len(anchor_fqns) < 2:
            return {"error": "Need at least 2 anchor tables"}

        self._project_dijkstra_graph()

        # Build terminal node IDs from FQNs
        terminal_ids = self._run("""
            MATCH (t:Table) WHERE t.fqn IN $fqns
            RETURN id(t) AS node_id, t.fqn AS fqn
        """, fqns=anchor_fqns)

        if len(terminal_ids) < 2:
            self._drop_graph("dijkstra_graph")
            return {"error": "Some anchor tables not found in graph"}

        source_id = terminal_ids[0]["node_id"]
        target_ids = [r["node_id"] for r in terminal_ids[1:]]

        result = self._run("""
            MATCH (src) WHERE id(src) = $source_id
            CALL gds.steinerTree.stream('dijkstra_graph', {
                sourceNode:                src,
                targetNodes:               $target_ids,
                relationshipWeightProperty:'join_cost'
            })
            YIELD relationshipIndex, sourceNode, targetNode, cost
            MATCH (a) WHERE id(a) = sourceNode
            MATCH (b) WHERE id(b) = targetNode
            RETURN a.fqn AS from_fqn, b.fqn AS to_fqn, cost
        """, source_id=source_id, target_ids=target_ids)

        self._drop_graph("dijkstra_graph")

        edges = [{"from_fqn": r["from_fqn"], "to_fqn": r["to_fqn"], "cost": r["cost"]}
                 for r in result]
        total_cost = sum(e["cost"] for e in edges)
        involved_tables = list({t for e in edges for t in [e["from_fqn"], e["to_fqn"]]})

        return {
            "anchor_tables": anchor_fqns,
            "steiner_edges": edges,
            "total_cost": round(total_cost, 3),
            "involved_tables": involved_tables,
        }

    # ── Cross-community paths ──────────────────────────────────────────────

    def run_cross_community_paths(self, max_hops: int = 6):
        """
        D1 — Precompute Dijkstra paths for cross-community anchor/fact table pairs
        connected via BRIDGES_TO. Stores JoinPath nodes with is_cross_community=true.
        """
        candidate_pairs = self._run("""
            MATCH (ca:Community)-[:BRIDGES_TO]->(cb:Community)
            MATCH (a:Table) WHERE a.community_id = ca.id
              AND a.typical_join_role IN ['anchor','fact']
            MATCH (b:Table) WHERE b.community_id = cb.id
              AND b.typical_join_role IN ['anchor','fact']
            RETURN a.fqn AS src, b.fqn AS dst, ca.id AS cid_a, cb.id AS cid_b
            ORDER BY coalesce(a.pagerank_score, 0) DESC
            LIMIT 500
        """)
        if not candidate_pairs:
            log.info("No cross-community candidate pairs found — skipping.")
            return

        self._project_dijkstra_graph()
        log.info("Cross-community paths: %d candidate pairs.", len(candidate_pairs))

        saved = 0
        now = _NOW()
        for row in candidate_pairs:
            src, dst = row["src"], row["dst"]
            result = self._run("""
                MATCH (src:Table {fqn: $src})
                MATCH (dst:Table {fqn: $dst})
                CALL gds.shortestPath.dijkstra.stream('dijkstra_graph', {
                    sourceNode:                src,
                    targetNode:                dst,
                    relationshipWeightProperty:'join_cost'
                })
                YIELD path, totalCost
                RETURN
                    [n IN nodes(path) | n.fqn] AS path_tables,
                    totalCost,
                    size(nodes(path)) - 1      AS hop_count
                LIMIT 1
            """, src=src, dst=dst)

            if not result:
                continue
            r = result[0]
            if max_hops is not None and r["hop_count"] > max_hops:
                continue

            bridge_cids = list({row["cid_a"], row["cid_b"]})
            join_clauses = self._get_join_clauses_for_path(r["path_tables"])
            path_id = f"cross_community::{src}::{dst}"
            self._run("""
                MERGE (jp:JoinPath {id: $id})
                SET jp.from_fqn              = $from_fqn,
                    jp.to_fqn                = $to_fqn,
                    jp.path_tables           = $path_tables,
                    jp.join_clauses          = $join_clauses,
                    jp.total_cost            = $total_cost,
                    jp.hop_count             = $hop_count,
                    jp.algorithm             = 'dijkstra',
                    jp.k_rank                = 1,
                    jp.is_cross_community    = true,
                    jp.bridge_community_ids  = $bridge_cids,
                    jp.source_hash           = $id,
                    jp.created_at            = $now,
                    jp.updated_at            = $now
            """, id=path_id, from_fqn=src, to_fqn=dst,
                 path_tables=r["path_tables"],
                 join_clauses=join_clauses,
                 total_cost=r["totalCost"],
                 hop_count=r["hop_count"],
                 bridge_cids=bridge_cids,
                 now=now)
            saved += 1

        self._drop_graph("dijkstra_graph")
        log.info("Cross-community paths: saved %d JoinPath nodes.", saved)

    def run_quality_scores(self):
        """
        D2 — Compute quality_score on all JoinPath nodes in a single Cypher pass.
        quality_score = mean(edge_confidence) × (1/hop_count) × declared_bonus
        """
        now = _NOW()
        result = self._run("""
            MATCH (jp:JoinPath)
            WHERE jp.path_tables IS NOT NULL AND size(jp.path_tables) >= 2
            CALL (jp) {
                WITH jp.path_tables AS tables,
                     CASE WHEN coalesce(jp.hop_count, 0) < 1 THEN 1 ELSE jp.hop_count END AS hop_count
                UNWIND range(0, size(tables) - 2) AS i
                MATCH (a:Table {fqn: tables[i]})-[r:JOINS_TO]-(b:Table {fqn: tables[i+1]})
                WITH jp, hop_count,
                     avg(coalesce(r.confidence, 0.5))                        AS avg_conf,
                     sum(CASE WHEN r.is_declared = true THEN 1 ELSE 0 END)   AS declared_count,
                     count(r)                                                 AS edge_count
                WITH jp, hop_count, avg_conf, declared_count, edge_count,
                     CASE WHEN declared_count = edge_count THEN 1.3 ELSE 1.0 END AS bonus
                SET jp.quality_score = round(avg_conf * (1.0 / hop_count) * bonus * 10000) / 10000.0,
                    jp.updated_at    = $now
            }
            RETURN count(jp) AS updated
        """, now=now)
        updated = result[0]["updated"] if result else 0
        log.info("quality_score set on %d JoinPath nodes.", updated)

    # ── Lookup helpers ─────────────────────────────────────────────────────

    def get_join_path(self, from_fqn: str, to_fqn: str, k: int = 1) -> list[dict]:
        """Retrieve precomputed join path(s) between two tables."""
        return self._run("""
            MATCH (jp:JoinPath)
            WHERE jp.from_fqn = $from_fqn AND jp.to_fqn = $to_fqn
               OR jp.from_fqn = $to_fqn   AND jp.to_fqn = $from_fqn
            RETURN jp.path_tables AS path_tables,
                   jp.join_clauses AS join_clauses,
                   jp.total_cost AS total_cost,
                   jp.hop_count AS hop_count,
                   jp.algorithm AS algorithm,
                   jp.k_rank AS k_rank
            ORDER BY jp.k_rank ASC
            LIMIT $k
        """, from_fqn=from_fqn, to_fqn=to_fqn, k=k)
