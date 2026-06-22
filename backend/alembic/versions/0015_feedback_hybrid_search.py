"""Add question_text and search_vector to mti_brain_feedback for hybrid retrieval.

Adds:
  - question_text TEXT  : denormalised copy of the user question (avoids join at query time)
  - search_vector TSVECTOR : GIN-indexed tsvector over question_text + comment
  - DB trigger          : auto-recomputes search_vector on INSERT / UPDATE
  - Backfill            : populates question_text for existing rows from mti_brain_message

Revision ID: 0015_feedback_hybrid_search
Revises: 0014_user_instructions
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0015_feedback_hybrid_search"
down_revision: Union[str, Sequence[str], None] = "0014_user_instructions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mti_brain_feedback ADD COLUMN IF NOT EXISTS question_text TEXT")
    op.execute("ALTER TABLE mti_brain_feedback ADD COLUMN IF NOT EXISTS search_vector TSVECTOR")

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mti_brain_feedback_fts
        ON mti_brain_feedback USING gin(search_vector)
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION mti_brain_feedback_sv_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector(
                'english',
                coalesce(NEW.question_text, '') || ' ' || coalesce(NEW.comment, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_mti_brain_feedback_sv
        BEFORE INSERT OR UPDATE OF question_text, comment
        ON mti_brain_feedback
        FOR EACH ROW
        EXECUTE FUNCTION mti_brain_feedback_sv_update();
    """)

    op.execute("""
        UPDATE mti_brain_feedback f
        SET question_text = q.content
        FROM mti_brain_message m
        JOIN mti_brain_message q
          ON q.conversation_id = m.conversation_id AND q.role = 'user'
        WHERE f.message_id = m.id
          AND f.question_text IS NULL
    """)

    op.execute("""
        UPDATE mti_brain_feedback
        SET search_vector = to_tsvector(
            'english',
            coalesce(question_text, '') || ' ' || coalesce(comment, '')
        )
        WHERE search_vector IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_mti_brain_feedback_sv ON mti_brain_feedback")
    op.execute("DROP FUNCTION IF EXISTS mti_brain_feedback_sv_update()")
    op.execute("DROP INDEX IF EXISTS idx_mti_brain_feedback_fts")
    op.execute("ALTER TABLE mti_brain_feedback DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE mti_brain_feedback DROP COLUMN IF EXISTS question_text")
