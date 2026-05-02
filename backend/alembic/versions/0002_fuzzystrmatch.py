"""Ensure fuzzystrmatch extension is enabled.

Revision ID: 0002_fuzzystrmatch
Revises: 0001_search_perf
Create Date: 2026-05-02

Adds typo tolerance to chat and project search by enabling
``fuzzystrmatch`` for ``levenshtein_less_equal()``. Almost certainly a
no-op in production — earlier code (since removed in Tier 1) used
``dmetaphone()`` from the same extension, so it has been installed all
along. The ``IF NOT EXISTS`` guard makes this safe regardless.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002_fuzzystrmatch"
down_revision: Union[str, Sequence[str], None] = "0001_search_perf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")


def downgrade() -> None:
    """Downgrade schema."""
    # Intentionally not dropped — other features (and possibly future
    # migrations) may depend on it. Extensions are cheap to leave installed.
    pass
