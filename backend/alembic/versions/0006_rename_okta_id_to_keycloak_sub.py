"""Rename okta_id column to keycloak_sub in mti_brain_user.

Revision ID: 0006_rename_okta_id_to_keycloak_sub
Revises: 0005_full_index_audit
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006_keycloak_sub"
down_revision: Union[str, Sequence[str], None] = "0005_full_index_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("mti_brain_user", "okta_id", new_column_name="keycloak_sub")
    op.execute(
        "ALTER INDEX IF EXISTS ix_mti_brain_user_okta_id "
        "RENAME TO ix_mti_brain_user_keycloak_sub"
    )


def downgrade() -> None:
    op.alter_column("mti_brain_user", "keycloak_sub", new_column_name="okta_id")
    op.execute(
        "ALTER INDEX IF EXISTS ix_mti_brain_user_keycloak_sub "
        "RENAME TO ix_mti_brain_user_okta_id"
    )
