"""Change mti_brain_feedback.embedding from Vector(1536) to Vector(1024).

Cohere Embed v4 on Bedrock produces 1024-dim vectors. The column was
initialised at 1536 (OpenAI legacy default), causing all embedding inserts
to fail silently on dimension mismatch.

Revision ID: 0007_feedback_embedding_dim
Revises: 0006_keycloak_sub
Create Date: 2026-05-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_feedback_embedding_dim"
down_revision: Union[str, Sequence[str], None] = "0006_keycloak_sub"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop existing values first — dimension change requires a full column rewrite
    op.execute("UPDATE mti_brain_feedback SET embedding = NULL")
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ALTER COLUMN embedding TYPE vector(1024)"
    )


def downgrade() -> None:
    op.execute("UPDATE mti_brain_feedback SET embedding = NULL")
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ALTER COLUMN embedding TYPE vector(1536)"
    )
