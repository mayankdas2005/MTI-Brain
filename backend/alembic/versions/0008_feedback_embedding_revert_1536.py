"""Revert mti_brain_feedback.embedding back to Vector(1536).

Cohere Embed v4 (embed-v4:0) returns 1536-dimensional vectors.
Migration 0007 incorrectly changed the column to Vector(1024).

Revision ID: 0008_feedback_embedding_revert_1536
Revises: 0007_feedback_embedding_dim
Create Date: 2026-05-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008_embed_dim_1536"
down_revision: Union[str, Sequence[str], None] = "0007_feedback_embedding_dim"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE mti_brain_feedback SET embedding = NULL")
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ALTER COLUMN embedding TYPE vector(1536)"
    )


def downgrade() -> None:
    op.execute("UPDATE mti_brain_feedback SET embedding = NULL")
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ALTER COLUMN embedding TYPE vector(1024)"
    )
