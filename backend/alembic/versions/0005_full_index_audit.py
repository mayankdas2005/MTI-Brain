"""Full index audit — add all indexes identified as missing across every API.

Each index is documented with the query and endpoint that motivated it.

Revision ID: 0005_full_index_audit
Revises: 0004_message_conversation_index
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_full_index_audit"
down_revision: Union[str, Sequence[str], None] = "0004_message_conversation_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mti_brain_message ────────────────────────────────────────────────────

    # M8: Expression index on the JSON "stopped" flag.
    # Used in get_thread() valid_convos CTE:
    #   WHERE role='assistant' AND (... OR (metadata->>'stopped')::boolean = true)
    # Without this, Postgres evaluates the JSON cast for every assistant message
    # in the thread on every GET /chat/{thread_id} call.
    op.create_index(
        "ix_mti_brain_message_stopped",
        "mti_brain_message",
        [sa.text("((metadata->>'stopped')::boolean)")],
        postgresql_where="role = 'assistant'",
    )

    # M9: (conversation_id, role) composite.
    # Used in POST /conversations/{id}/feedback → save_feedback():
    #   WHERE conversation_id = :cid AND role = 'assistant'
    # Only conversation_id was indexed; role required a post-index filter.
    op.create_index(
        "ix_mti_brain_message_conversation_role",
        "mti_brain_message",
        ["conversation_id", "role"],
    )

    # M10: (thread_id, role, created_at) composite.
    # Used in POST /chat/{id}/edit → is_first_conversation():
    #   WHERE thread_id=:tid AND role='user' ORDER BY created_at ASC LIMIT 1
    # thread_role index covers the WHERE but not the ORDER BY, forcing a sort.
    op.create_index(
        "ix_mti_brain_message_thread_role_created",
        "mti_brain_message",
        ["thread_id", "role", "created_at"],
    )

    # ── mti_brain_saved_query (Playbook) ─────────────────────────────────────

    # M4: (user_id, created_at) — covers GET /playbook:
    #   WHERE user_id=:uid ORDER BY created_at ASC
    # user_id was indexed but created_at was not; sort done in memory.
    op.create_index(
        "ix_mti_brain_saved_query_user_created",
        "mti_brain_saved_query",
        ["user_id", "created_at"],
    )

    # ── mti_brain_pinned_metric ───────────────────────────────────────────────

    # H3: (user_id, position, created_at) — covers GET /pinned-metrics:
    #   WHERE user_id=:uid ORDER BY position ASC, created_at ASC
    # Both sort columns were unindexed; in-memory sort on every listing.
    op.create_index(
        "ix_mti_brain_pinned_metric_user_position",
        "mti_brain_pinned_metric",
        ["user_id", "position", "created_at"],
    )

    # ── mti_brain_thread_label ────────────────────────────────────────────────

    # M5: (user_id, created_at) — covers GET /labels:
    #   WHERE user_id=:uid ORDER BY created_at DESC LIMIT 200
    op.create_index(
        "ix_mti_brain_thread_label_user_created",
        "mti_brain_thread_label",
        ["user_id", "created_at"],
    )

    # M6: (thread_id, user_id) — covers GET /labels/thread/{id}:
    #   WHERE thread_id=:tid AND user_id=:uid
    # Both columns were indexed separately; a composite removes the dual-index scan.
    op.create_index(
        "ix_mti_brain_thread_label_thread_user",
        "mti_brain_thread_label",
        ["thread_id", "user_id"],
    )

    # M7: (label) — covers GET /chat/recents?label=X:
    #   JOIN ... ON tl.label = :label_filter
    # No index on the label column; scanned all label rows for the user.
    op.create_index(
        "ix_mti_brain_thread_label_label",
        "mti_brain_thread_label",
        ["label"],
    )


def downgrade() -> None:
    op.drop_index("ix_mti_brain_thread_label_label", table_name="mti_brain_thread_label")
    op.drop_index("ix_mti_brain_thread_label_thread_user", table_name="mti_brain_thread_label")
    op.drop_index("ix_mti_brain_thread_label_user_created", table_name="mti_brain_thread_label")
    op.drop_index("ix_mti_brain_pinned_metric_user_position", table_name="mti_brain_pinned_metric")
    op.drop_index("ix_mti_brain_saved_query_user_created", table_name="mti_brain_saved_query")
    op.drop_index("ix_mti_brain_message_thread_role_created", table_name="mti_brain_message")
    op.drop_index("ix_mti_brain_message_conversation_role", table_name="mti_brain_message")
    op.drop_index("ix_mti_brain_message_stopped", table_name="mti_brain_message")
