"""Search perf: trigram indexes + project search_vector + trigger.

Revision ID: 0001_search_perf
Revises:
Create Date: 2026-05-02

Adds, in this order:

* ``pg_trgm`` extension (idempotent — likely already installed since
  ``ix_quest_thread_title_trgm`` exists).
* Trigram GIN index on ``quest_message.content`` (Layer 2 ILIKE and
  Layer 3 trigram pre-filter both use it).
* Trigram GIN indexes on ``quest_project.name`` and ``quest_project.description``.
* ``quest_project.search_vector`` TSVECTOR column, plus a BEFORE
  INSERT/UPDATE trigger that maintains it from ``name`` (weight A) and
  ``description`` (weight B), and a GIN index on the column.
* Backfill of existing ``quest_project`` rows so the index has data
  immediately.

This is the **first** migration in the project. Existing production DBs
already have ``search_vector`` triggers on ``quest_thread`` and
``quest_message`` (created out-of-band before alembic was set up); this
migration intentionally does NOT recreate them. A future ``0000_baseline``
migration can capture those if/when we want to make a fresh DB
reproducible from scratch.

For very large tables in production, consider running each
``CREATE INDEX`` as ``CREATE INDEX CONCURRENTLY`` out-of-band instead of
letting alembic run them inside this transaction.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001_search_perf"
# Chains from the phantom baseline (b3c4d5e6f7a8) so upgrades succeed
# against DBs that were stamped at that revision before alembic was
# re-established in the repo. See b3c4d5e6f7a8_phantom_baseline.py.
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PROJECT_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION quest_project_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Trigram indexes for fast ILIKE / word_similarity / `%` operator.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quest_message_content_trgm "
        "ON quest_message USING gin (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quest_project_name_trgm "
        "ON quest_project USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quest_project_description_trgm "
        "ON quest_project USING gin (description gin_trgm_ops)"
    )

    # Project FTS column + maintenance trigger + GIN index.
    op.execute(
        "ALTER TABLE quest_project ADD COLUMN IF NOT EXISTS search_vector tsvector"
    )
    op.execute(_PROJECT_TRIGGER_FN)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_quest_project_search_vector ON quest_project"
    )
    op.execute(
        "CREATE TRIGGER trg_quest_project_search_vector "
        "BEFORE INSERT OR UPDATE OF name, description ON quest_project "
        "FOR EACH ROW EXECUTE FUNCTION quest_project_search_vector_update()"
    )

    # Backfill so existing rows match what the trigger would produce.
    op.execute(
        "UPDATE quest_project SET search_vector = "
        "setweight(to_tsvector('english', coalesce(name, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(description, '')), 'B')"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quest_project_search "
        "ON quest_project USING gin (search_vector)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_quest_project_search")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_quest_project_search_vector ON quest_project"
    )
    op.execute("DROP FUNCTION IF EXISTS quest_project_search_vector_update()")
    op.execute("ALTER TABLE quest_project DROP COLUMN IF EXISTS search_vector")
    op.execute("DROP INDEX IF EXISTS ix_quest_project_description_trgm")
    op.execute("DROP INDEX IF EXISTS ix_quest_project_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_quest_message_content_trgm")
    # pg_trgm is intentionally not dropped — other indexes / extensions may rely on it.
