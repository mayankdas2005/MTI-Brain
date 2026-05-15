"""Add index on mti_brain_message.conversation_id.

Used by the IN (SELECT conversation_id FROM valid_convos) join condition in
get_thread. Without this index PostgreSQL must seq-scan all message rows for
the thread and apply the IN filter row-by-row, causing significant slowdowns
on threads with many messages.

Also adds a composite (thread_id, conversation_id) index for covering lookups
that filter by both columns simultaneously.

Revision ID: 0004_message_conversation_index
Revises: 0003_perf_indexes
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_message_conversation_index"
down_revision: Union[str, Sequence[str], None] = "0003_perf_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both indexes may already exist if the model's __table_args__ was applied
    # by a previous migration — if_not_exists prevents DuplicateTable errors.
    op.create_index(
        "ix_mti_brain_message_conversation",
        "mti_brain_message",
        ["conversation_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_mti_brain_message_thread_conversation",
        "mti_brain_message",
        ["thread_id", "conversation_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_mti_brain_message_thread_conversation", table_name="mti_brain_message")
    op.drop_index("ix_mti_brain_message_conversation", table_name="mti_brain_message")
