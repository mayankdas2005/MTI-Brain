"""Phantom baseline — recovers a DB stamped at this revision.

Revision ID: b3c4d5e6f7a8
Revises:
Create Date: 2026-05-02

The deployed `alembic_version` table reports revision ``b3c4d5e6f7a8`` but
no migration file with that ID was ever committed to the repo. Without
this stub, ``alembic upgrade head`` fails with::

    Can't locate revision identified by 'b3c4d5e6f7a8'

This file exists *purely to satisfy alembic's revision graph* so that
later migrations can chain from it via ``down_revision``. The actual
schema state at this revision is whatever was in the DB at the time
alembic was abandoned/forgotten — not reproducible from this file.

Both ``upgrade()`` and ``downgrade()`` are intentionally no-ops. Do not
add work here. Future migrations should depend on a *named* baseline,
not this phantom — once we want fresh-from-scratch DB reproducibility
we should write a proper ``0000_baseline.py`` that captures the existing
``quest_thread`` / ``quest_message`` triggers and tables.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: this revision represents the pre-recovery DB state."""
    pass


def downgrade() -> None:
    """No-op: nothing to undo."""
    pass
