"""Node 1a: context_fetcher — delegates to context/ subpackage.

All logic has moved to:
  context/fetcher.py        — main entry point
  context/table_discovery.py — 8-path table search
  context/column_loader.py  — join-critical detection + column prioritization
  context/cross_domain.py   — 4-method hub cascade
  context/helpers.py        — embedding, tokenization, trim
"""

from app.services.agents.context.fetcher import context_fetcher
from app.services.agents.context.helpers import get_embedding as _get_embedding

__all__ = ["context_fetcher", "_get_embedding"]
