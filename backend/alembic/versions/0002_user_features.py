"""Add user features: saved queries (playbook), pinned metrics, thread labels.

Revision ID: 0002_user_features
Revises: 0001_baseline
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.models import conversation as _conversation  # noqa: F401
from app.models import execution_log as _execution_log  # noqa: F401
from app.models import user as _user  # noqa: F401
from app.models import user_features as _user_features  # noqa: F401

revision: str = "0002_user_features"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mti_brain_saved_query ──────────────────────────────────────────────
    op.create_table(
        "mti_brain_saved_query",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_mti_brain_saved_query_user_id", "mti_brain_saved_query", ["user_id"])

    # ── mti_brain_pinned_metric ────────────────────────────────────────────
    op.create_table(
        "mti_brain_pinned_metric",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("source_query", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_mti_brain_pinned_metric_user_id", "mti_brain_pinned_metric", ["user_id"])

    # ── mti_brain_thread_label ─────────────────────────────────────────────
    op.create_table(
        "mti_brain_thread_label",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("mti_brain_thread.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default="blue"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_mti_brain_thread_label_thread_id", "mti_brain_thread_label", ["thread_id"])
    op.create_index("ix_mti_brain_thread_label_user_id", "mti_brain_thread_label", ["user_id"])


def downgrade() -> None:
    op.drop_table("mti_brain_thread_label")
    op.drop_table("mti_brain_pinned_metric")
    op.drop_table("mti_brain_saved_query")
