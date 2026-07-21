"""Mock Neo4j graph responses for integration tests."""

from __future__ import annotations


class MockNeo4jFixture:
    """Configurable Neo4j mock — set graph data before querying.

    Usage:
        neo4j = MockNeo4jFixture()
        neo4j.set_tables(["lpp.orders", "lpp.items"])
        result = await neo4j.run_query("MATCH (t:Table) RETURN t.fqn")
    """

    def __init__(self):
        self._tables: list[str] = []
        self._nodes: list[dict] = []
        self._edges: list[dict] = []
        self._custom_results: dict[str, list] = {}
        self._call_log: list[str] = []

    def set_tables(self, tables: list[str]):
        self._tables = tables
        self._nodes = [{"_label": "Table", "fqn": t} for t in tables]

    def set_graph(self, nodes: list[dict], edges: list[dict]):
        self._nodes = nodes
        self._edges = edges

    def set_custom_result(self, query_fragment: str, result: list):
        self._custom_results[query_fragment.lower()] = result

    async def run_query(self, query: str, *args, **kwargs) -> list:
        self._call_log.append(query)
        for fragment, result in self._custom_results.items():
            if fragment in query.lower():
                return result
        if "table" in query.lower():
            return [{"fqn": t} for t in self._tables]
        return self._nodes

    @property
    def call_count(self) -> int:
        return len(self._call_log)

    def reset(self):
        self._tables = []
        self._nodes = []
        self._edges = []
        self._custom_results = {}
        self._call_log = []
