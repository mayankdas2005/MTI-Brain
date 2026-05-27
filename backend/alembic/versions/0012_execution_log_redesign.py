"""Execution log audit/lineage redesign: drop vestigial cols, add langfuse_trace_id.

Revision ID: 0012_execution_log_redesign
Revises: 0011_graph_context_table
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_execution_log_redesign"
down_revision: Union[str, Sequence[str], None] = "0011_graph_context_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("mti_brain_execution_log", "implicit_positive")
    op.drop_column("mti_brain_execution_log", "implicit_negative")
    op.drop_column("mti_brain_execution_log", "liked")
    op.drop_column("mti_brain_execution_log", "valid")
    op.add_column(
        "mti_brain_execution_log",
        sa.Column("langfuse_trace_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mti_brain_execution_log", "langfuse_trace_id")
    op.add_column(
        "mti_brain_execution_log",
        sa.Column("valid", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "mti_brain_execution_log",
        sa.Column("liked", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "mti_brain_execution_log",
        sa.Column("implicit_negative", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "mti_brain_execution_log",
        sa.Column("implicit_positive", sa.Boolean(), nullable=True),
    )
