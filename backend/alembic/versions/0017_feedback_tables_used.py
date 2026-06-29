"""Add tables_used array column to mti_brain_feedback for table-based cross-thread retrieval.

Enables Path B of the hybrid feedback retrieval strategy: after anchor_resolver resolves
tables, sql_generator does a late-pass PostgreSQL lookup for feedback from other threads
that used the same anchor tables. This catches structurally-identical queries with
completely different question wording that the early vector+FTS pass misses.

Adds:
  - mti_brain_feedback.tables_used TEXT[]  : anchor table FQNs from the pipeline run
  - GIN index idx_mti_brain_feedback_tables_used  : efficient && (array overlap) queries

Revision ID: 0017_feedback_tables_used
Revises: 0016_feedback_overhaul
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0017_feedback_tables_used"
down_revision: Union[str, Sequence[str], None] = "0016_feedback_overhaul"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ADD COLUMN IF NOT EXISTS tables_used TEXT[]"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mti_brain_feedback_tables_used "
        "ON mti_brain_feedback USING GIN (tables_used)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mti_brain_feedback_tables_used")
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "DROP COLUMN IF EXISTS tables_used"
    )
