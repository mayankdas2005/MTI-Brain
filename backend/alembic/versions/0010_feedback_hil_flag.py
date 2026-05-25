"""Add verified_for_hil flag to mti_brain_feedback.

Revision ID: 0010_feedback_hil_flag
Revises: 0009_dashboard_table
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_feedback_hil_flag"
down_revision: Union[str, Sequence[str], None] = "0009_dashboard_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mti_brain_feedback",
        sa.Column(
            "verified_for_hil",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("mti_brain_feedback", "verified_for_hil")
