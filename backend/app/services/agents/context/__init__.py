"""Context fetcher subpackage — pure Neo4j retrieval, no Redshift at retrieval time."""

from .fetcher import context_fetcher

__all__ = ["context_fetcher"]
