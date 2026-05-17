"""Add mti_brain_dashboard table for S3-backed dashboard persistence.

Revision ID: 0009_dashboard_table
Revises: 0008_embed_dim_1536
Create Date: 2026-05-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_dashboard_table"
down_revision: Union[str, Sequence[str], None] = "0008_embed_dim_1536"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mti_brain_dashboard",
        sa.Column("id",              sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("thread_id",       sa.UUID(as_uuid=True), sa.ForeignKey("mti_brain_thread.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_id",         sa.UUID(as_uuid=True), sa.ForeignKey("mti_brain_user.id",   ondelete="SET NULL"), nullable=True),
        sa.Column("s3_key",          sa.String(500), nullable=False, server_default=""),
        sa.Column("s3_url",          sa.Text,        nullable=False, server_default=""),
        sa.Column("status",          sa.String(20),  nullable=False, server_default="pending"),
        sa.Column("error_msg",       sa.Text,        nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mti_brain_dashboard_thread",       "mti_brain_dashboard", ["thread_id"])
    op.create_index("ix_mti_brain_dashboard_conversation", "mti_brain_dashboard", ["conversation_id"], unique=True)
    op.create_index("ix_mti_brain_dashboard_user",         "mti_brain_dashboard", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_mti_brain_dashboard_user",         table_name="mti_brain_dashboard")
    op.drop_index("ix_mti_brain_dashboard_conversation", table_name="mti_brain_dashboard")
    op.drop_index("ix_mti_brain_dashboard_thread",       table_name="mti_brain_dashboard")
    op.drop_table("mti_brain_dashboard")
