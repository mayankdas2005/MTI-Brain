"""Disable seeded default login hashes for bootstrap users.

Revision ID: 0019_disable_seeded_passwords
Revises: 0018_user_password
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019_disable_seeded_passwords"
down_revision: Union[str, Sequence[str], None] = "0018_user_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN_HASH = "$2b$12$cIf.CmlZ0pO2sAWQy4Yzr.TRNpeL/Tx9r8omOPdzbpgQiKKIsXGgq"
_USER_HASH = "$2b$12$s.DOjMeoCePS33QVqJ3sOuaro/fP8FHeXwtWYfsrhbWR8fkcNN6xO"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE mti_brain_user
            SET password = NULL
            WHERE (
                lower(email) IN ('admin@milestone.tech', 'user@milestone.tech')
                OR lower(keycloak_sub) IN ('admin@milestone.tech', 'user@milestone.tech')
            )
            AND password IN (:admin_hash, :user_hash)
            """
        ),
        {"admin_hash": _ADMIN_HASH, "user_hash": _USER_HASH},
    )


def downgrade() -> None:
    # Irreversible: original plaintext passwords are intentionally not retained.
    pass
