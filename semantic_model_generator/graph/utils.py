"""Shared utilities for the graph ingestion pipeline."""

from __future__ import annotations


def is_uuid_col(name: str) -> bool:
    """Return True for surrogate UUID columns that carry no join semantics.

    In the lpp schema 62/77 tables have a column literally named 'uuid' as
    their surrogate PK, plus a handful of columns ending '_uuid' (e.g.
    file_uuid).  All real FK joins go through 'code' or '*_ref' columns.
    These UUID columns are excluded from Column nodes and JOINS_TO edges.
    """
    return name == "uuid" or name.endswith("_uuid")
