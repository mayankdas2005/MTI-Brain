"""Apache Jena Fuseki SPARQL client for the MTI Brain pipeline.

Wraps aiohttp to execute SELECT, ASK, and CONSTRUCT queries against a
Fuseki ARQ endpoint. Returns parsed bindings as plain Python dicts so
nodes never have to touch HTTP internals.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from app.core.config import settings
from app.core.logger import logger


class FusekiClient:
    """Async SPARQL client backed by a persistent aiohttp session."""

    def __init__(self, base_url: str, dataset: str, timeout: int = 30) -> None:
        self._query_url = f"{base_url.rstrip('/')}/{dataset}/sparql"
        self._update_url = f"{base_url.rstrip('/')}/{dataset}/update"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            headers={"Accept": "application/sparql-results+json"},
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    def _session_or_raise(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("FusekiClient not opened — call open() first.")
        return self._session

    async def execute_select(self, query: str) -> tuple[list[str], list[list], list[dict]]:
        """Execute a SPARQL SELECT and return (columns, rows, raw_bindings).

        columns    : list of variable names from head.vars
        rows       : list of scalar value lists (same order as columns)
        raw_bindings: original [{var: {value, type, …}}, …] dicts from Fuseki
        """
        session = self._session_or_raise()
        try:
            async with session.post(
                self._query_url,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            ) as resp:
                resp.raise_for_status()
                payload: dict = await resp.json(content_type=None)
        except aiohttp.ClientResponseError as e:
            raise RuntimeError(f"Fuseki HTTP error {e.status}: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Fuseki request failed: {e}") from e

        columns: list[str] = payload.get("head", {}).get("vars", [])
        raw_bindings: list[dict] = payload.get("results", {}).get("bindings", [])

        rows: list[list] = []
        for binding in raw_bindings:
            row: list[Any] = []
            for col in columns:
                cell = binding.get(col)
                if cell is None:
                    row.append(None)
                else:
                    val = cell.get("value")
                    dtype = cell.get("datatype", "")
                    if "decimal" in dtype or "integer" in dtype or "double" in dtype or "float" in dtype:
                        try:
                            row.append(float(val))
                        except (TypeError, ValueError):
                            row.append(val)
                    elif "date" in dtype:
                        row.append(val)
                    else:
                        row.append(val)
            rows.append(row)

        return columns, rows, raw_bindings

    async def execute_ask(self, query: str) -> bool:
        """Execute a SPARQL ASK and return the boolean result."""
        session = self._session_or_raise()
        try:
            async with session.post(
                self._query_url,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            ) as resp:
                resp.raise_for_status()
                payload: dict = await resp.json(content_type=None)
                return bool(payload.get("boolean", False))
        except Exception as e:
            logger.warning(f"Fuseki ASK error: {e}")
            return False

    async def check_predicate_exists(self, predicate_uri: str) -> bool:
        """Return True if the predicate appears as a property in the graph."""
        query = f"ASK {{ ?s <{predicate_uri}> ?o }}"
        return await self.execute_ask(query)

    async def check_class_exists(self, class_uri: str) -> bool:
        """Return True if at least one instance of the class exists."""
        query = f"ASK {{ ?s a <{class_uri}> }}"
        return await self.execute_ask(query)

    async def health_check(self) -> bool:
        """Lightweight ping — true if Fuseki is reachable."""
        try:
            return await self.execute_ask("ASK { ?s ?p ?o }")
        except Exception:
            return False
