"""Feedback overhaul: intent_text, feedback_type, trigger tracking on feedback; distilled_preferences on user.

Adds to mti_brain_feedback:
  - intent_text TEXT            : FTS-searchable intent fingerprint from directive_writer
  - feedback_type VARCHAR(16)   : 'answer' | 'sql' | 'chart' | 'general'
  - last_triggered_at TIMESTAMPTZ : updated when feedback is retrieved and applied
  - trigger_count INTEGER       : how many times this feedback has been retrieved

Adds to mti_brain_user:
  - distilled_preferences TEXT  : Haiku-synthesised 5-8 bullet behavioural profile
  - distilled_at TIMESTAMPTZ    : when distillation last ran
  - feedback_count_at_distill INT : feedback count at last distillation

Updates DB trigger trg_mti_brain_feedback_sv to include intent_text in tsvector.

Revision ID: 0016_feedback_overhaul
Revises: 0015_feedback_hybrid_search
Create Date: 2026-06-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0016_feedback_overhaul"
down_revision: Union[str, Sequence[str], None] = "0015_feedback_hybrid_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mti_brain_feedback new columns ───────────────────────────────────────
    op.execute("ALTER TABLE mti_brain_feedback ADD COLUMN IF NOT EXISTS intent_text TEXT")
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ADD COLUMN IF NOT EXISTS feedback_type VARCHAR(16) NOT NULL DEFAULT 'general'"
    )
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ADD COLUMN IF NOT EXISTS last_triggered_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE mti_brain_feedback "
        "ADD COLUMN IF NOT EXISTS trigger_count INTEGER NOT NULL DEFAULT 0"
    )

    # ── Rebuild trigger to include intent_text ────────────────────────────────
    # Drop old trigger first (function stays, we replace it)
    op.execute("DROP TRIGGER IF EXISTS trg_mti_brain_feedback_sv ON mti_brain_feedback")

    op.execute("""
        CREATE OR REPLACE FUNCTION mti_brain_feedback_sv_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector(
                'english',
                coalesce(NEW.question_text, '') || ' ' ||
                coalesce(NEW.comment,        '') || ' ' ||
                coalesce(NEW.intent_text,    '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_mti_brain_feedback_sv
        BEFORE INSERT OR UPDATE OF question_text, comment, intent_text
        ON mti_brain_feedback
        FOR EACH ROW
        EXECUTE FUNCTION mti_brain_feedback_sv_update();
    """)

    # ── mti_brain_user new columns ────────────────────────────────────────────
    op.execute(
        "ALTER TABLE mti_brain_user ADD COLUMN IF NOT EXISTS distilled_preferences TEXT"
    )
    op.execute(
        "ALTER TABLE mti_brain_user ADD COLUMN IF NOT EXISTS distilled_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE mti_brain_user "
        "ADD COLUMN IF NOT EXISTS feedback_count_at_distill INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    # Restore trigger to pre-0016 state (matches 0015 definition)
    op.execute("DROP TRIGGER IF EXISTS trg_mti_brain_feedback_sv ON mti_brain_feedback")

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

    op.execute("ALTER TABLE mti_brain_feedback DROP COLUMN IF EXISTS intent_text")
    op.execute("ALTER TABLE mti_brain_feedback DROP COLUMN IF EXISTS feedback_type")
    op.execute("ALTER TABLE mti_brain_feedback DROP COLUMN IF EXISTS last_triggered_at")
    op.execute("ALTER TABLE mti_brain_feedback DROP COLUMN IF EXISTS trigger_count")

    op.execute("ALTER TABLE mti_brain_user DROP COLUMN IF EXISTS distilled_preferences")
    op.execute("ALTER TABLE mti_brain_user DROP COLUMN IF EXISTS distilled_at")
    op.execute("ALTER TABLE mti_brain_user DROP COLUMN IF EXISTS feedback_count_at_distill")
