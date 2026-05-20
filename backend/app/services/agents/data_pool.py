"""Connection pool management for Fuseki KG and Tribal graph.

Call ``init_data_pool()`` at startup and ``close_data_pool()`` at shutdown.
Nodes access the clients via ``get_kg_client()`` and ``get_tribal_client()``.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logger import logger
from app.services.agents.fuseki_client import FusekiClient

_kg_client: FusekiClient | None = None


async def init_data_pool() -> None:
    """Open aiohttp session for the Fuseki KG endpoint."""
    global _kg_client

    _kg_client = FusekiClient(
        base_url=settings.FUSEKI_URL,
        dataset=settings.FUSEKI_DATASET,
        timeout=settings.FUSEKI_TIMEOUT,
        username=settings.FUSEKI_USER,
        password=settings.FUSEKI_PASSWORD,
    )
    await _kg_client.open()

    kg_ok = await _kg_client.health_check()
    logger.info(
        f"Data pool ready: KG={_kg_client.query_url} "
        f"({'ok' if kg_ok else 'UNREACHABLE'})"
    )


async def close_data_pool() -> None:
    """Close the Fuseki HTTP session."""
    global _kg_client
    if _kg_client:
        await _kg_client.close()
        _kg_client = None
    logger.info("Data pool closed")


def get_kg_client() -> FusekiClient:
    if not _kg_client:
        raise RuntimeError("Data pool not initialized — call init_data_pool() first.")
    return _kg_client


def get_tribal_client() -> FusekiClient:
    return get_kg_client()
