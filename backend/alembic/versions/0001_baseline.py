"""Baseline schema for MTI Brain.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-04

Bootstraps a fresh database with everything the app needs:

* Required Postgres extensions (``pg_trgm``, ``fuzzystrmatch``, ``vector``).
* All ``mti_brain_*`` tables, columns, FKs, and indexes (created from
  :data:`Base.metadata` so trigram GIN indexes defined on the models come
  along for the ride).
* ``search_vector`` trigger functions and triggers for ``mti_brain_thread``
  (from ``title``), ``mti_brain_message`` (from ``content``), and
  ``mti_brain_project`` (weighted ``name`` + ``description``).

Idempotent guards (``IF NOT EXISTS`` / ``IF EXISTS``) are used on
extensions and trigger DDL so the migration can be re-run safely against
a partially-initialised DB.
"""

from typing import Sequence, Union

from alembic import op

from app.db.base import Base
# Importing the model modules registers their tables on Base.metadata.
from app.models import conversation as _conversation  # noqa: F401
from app.models import execution_log as _execution_log  # noqa: F401
from app.models import user as _user  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_THREAD_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION mti_brain_thread_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', coalesce(NEW.title, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

_MESSAGE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION mti_brain_message_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', coalesce(NEW.content, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

_PROJECT_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION mti_brain_project_search_vector_update() RETURNS trigger AS $$
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
    # Extensions must come first: pgvector for the feedback embedding
    # column, pg_trgm for the trigram GIN indexes that ship with the
    # models, fuzzystrmatch for levenshtein_less_equal() in search SQL.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create every table, column, FK, and index defined on the models.
    Base.metadata.create_all(bind=op.get_bind())

    # search_vector triggers (BEFORE INSERT/UPDATE) keep the tsvector
    # columns in sync without app code having to remember.
    op.execute(_THREAD_TRIGGER_FN)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mti_brain_thread_search_vector ON mti_brain_thread"
    )
    op.execute(
        "CREATE TRIGGER trg_mti_brain_thread_search_vector "
        "BEFORE INSERT OR UPDATE OF title ON mti_brain_thread "
        "FOR EACH ROW EXECUTE FUNCTION mti_brain_thread_search_vector_update()"
    )

    op.execute(_MESSAGE_TRIGGER_FN)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mti_brain_message_search_vector ON mti_brain_message"
    )
    op.execute(
        "CREATE TRIGGER trg_mti_brain_message_search_vector "
        "BEFORE INSERT OR UPDATE OF content ON mti_brain_message "
        "FOR EACH ROW EXECUTE FUNCTION mti_brain_message_search_vector_update()"
    )

    op.execute(_PROJECT_TRIGGER_FN)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mti_brain_project_search_vector ON mti_brain_project"
    )
    op.execute(
        "CREATE TRIGGER trg_mti_brain_project_search_vector "
        "BEFORE INSERT OR UPDATE OF name, description ON mti_brain_project "
        "FOR EACH ROW EXECUTE FUNCTION mti_brain_project_search_vector_update()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mti_brain_project_search_vector ON mti_brain_project"
    )
    op.execute("DROP FUNCTION IF EXISTS mti_brain_project_search_vector_update()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mti_brain_message_search_vector ON mti_brain_message"
    )
    op.execute("DROP FUNCTION IF EXISTS mti_brain_message_search_vector_update()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mti_brain_thread_search_vector ON mti_brain_thread"
    )
    op.execute("DROP FUNCTION IF EXISTS mti_brain_thread_search_vector_update()")

    Base.metadata.drop_all(bind=op.get_bind())

    # Extensions intentionally not dropped — leaving them installed is
    # cheap and avoids breaking unrelated databases that share the cluster.
