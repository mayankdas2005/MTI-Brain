"""Mock Redis for cache and rate-limit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock


class MockRedisFixture:
    """In-memory Redis mock for tests where fakeredis isn't available.

    Supports basic get/set/delete/expire/exists operations.
    For full fidelity, prefer `fakeredis.aioredis.FakeRedis()`.
    """

    def __init__(self):
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, **kwargs):
        self._store[key] = value
        if ex:
            self._ttls[key] = ex

    async def delete(self, *keys: str):
        for k in keys:
            self._store.pop(k, None)
            self._ttls.pop(k, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def expire(self, key: str, seconds: int):
        if key in self._store:
            self._ttls[key] = seconds

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    async def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)

    def reset(self):
        self._store.clear()
        self._ttls.clear()
