"""Add missing performance indexes on message role, parent_conversation_id,
thread starred, and feedback created_at.

Revision ID: 0003_perf_indexes
Revises: 0002_user_features
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_perf_indexes"
down_revision: Union[str, Sequence[str], None] = "0002_user_features"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Single-column index on role — used in every ask/retry/edit/search/feedback
    op.create_index(
        "ix_mti_brain_message_role",
        "mti_brain_message",
        ["role"],
    )

    # Composite index for the most common filter: thread_id + role
    op.create_index(
        "ix_mti_brain_message_thread_role",
        "mti_brain_message",
        ["thread_id", "role"],
    )

    # parent_conversation_id — retry/edit conversation chain traversal
    op.create_index(
        "ix_mti_brain_message_parent_conversation",
        "mti_brain_message",
        ["parent_conversation_id"],
        postgresql_where="parent_conversation_id IS NOT NULL",
    )

    # starred on threads — used in list_recent_chats starred filter
    op.create_index(
        "ix_mti_brain_thread_starred",
        "mti_brain_thread",
        ["starred"],
    )

    # created_at on feedback — used in ORDER BY within the get_thread LATERAL join
    op.create_index(
        "ix_mti_brain_feedback_created",
        "mti_brain_feedback",
        ["created_at"],
    )

    # Composite for feedback lookup by message_id + created_at (DISTINCT ON pattern)
    op.create_index(
        "ix_mti_brain_feedback_message_created",
        "mti_brain_feedback",
        ["message_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mti_brain_feedback_message_created", table_name="mti_brain_feedback")
    op.drop_index("ix_mti_brain_feedback_created", table_name="mti_brain_feedback")
    op.drop_index("ix_mti_brain_thread_starred", table_name="mti_brain_thread")
    op.drop_index("ix_mti_brain_message_parent_conversation", table_name="mti_brain_message")
    op.drop_index("ix_mti_brain_message_thread_role", table_name="mti_brain_message")
    op.drop_index("ix_mti_brain_message_role", table_name="mti_brain_message")
