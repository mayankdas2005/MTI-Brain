"""Mock Redshift query execution for integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock


class MockRedshiftFixture:
    """Configurable Redshift mock — set results or errors before calling.

    Usage:
        rs = MockRedshiftFixture()
        rs.set_result(["id", "total"], [[1, 100], [2, 200]])
        rows = await rs.execute_query("SELECT ...")
    """

    def __init__(self):
        self._results: list[dict] = []
        self._error: Exception | None = None
        self._call_log: list[str] = []

    def set_result(self, columns: list[str], rows: list[list]):
        self._results = [dict(zip(columns, row)) for row in rows]
        self._error = None

    def set_empty(self):
        self._results = []
        self._error = None

    def set_error(self, exc: Exception):
        self._error = exc

    async def execute_query(self, sql: str, *args, **kwargs) -> list[dict]:
        self._call_log.append(sql)
        if self._error:
            raise self._error
        return list(self._results)

    @property
    def call_count(self) -> int:
        return len(self._call_log)

    @property
    def last_sql(self) -> str | None:
        return self._call_log[-1] if self._call_log else None

    def reset(self):
        self._results = []
        self._error = None
        self._call_log = []
