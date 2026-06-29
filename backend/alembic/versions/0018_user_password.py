"""Add password hash column and bootstrap role users.

Revision ID: 0018_user_password
Revises: 0017_feedback_tables_used
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018_user_password"
down_revision: Union[str, Sequence[str], None] = "0017_feedback_tables_used"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN_HASH = "$2b$12$cIf.CmlZ0pO2sAWQy4Yzr.TRNpeL/Tx9r8omOPdzbpgQiKKIsXGgq"
_USER_HASH = "$2b$12$s.DOjMeoCePS33QVqJ3sOuaro/fP8FHeXwtWYfsrhbWR8fkcNN6xO"


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TABLE mti_brain_user ADD COLUMN IF NOT EXISTS password TEXT")
    bind.execute(
        sa.text("""
            INSERT INTO mti_brain_user (
                id, keycloak_sub, email, name, password, groups, last_login, created_at
            )
            VALUES (
                gen_random_uuid(),
                'admin@milestone.tech',
                'admin@milestone.tech',
                'Admin User',
                :password,
                '["admin"]'::jsonb,
                now(),
                now()
            )
            ON CONFLICT (keycloak_sub) DO UPDATE
            SET password = COALESCE(mti_brain_user.password, EXCLUDED.password),
                groups = '["admin"]'::jsonb
        """),
        {"password": _ADMIN_HASH},
    )
    bind.execute(
        sa.text("""
            INSERT INTO mti_brain_user (
                id, keycloak_sub, email, name, password, groups, last_login, created_at
            )
            VALUES (
                gen_random_uuid(),
                'user@milestone.tech',
                'user@milestone.tech',
                'Standard User',
                :password,
                '["user"]'::jsonb,
                now(),
                now()
            )
            ON CONFLICT (keycloak_sub) DO UPDATE
            SET password = COALESCE(mti_brain_user.password, EXCLUDED.password),
                groups = '["user"]'::jsonb
        """),
        {"password": _USER_HASH},
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mti_brain_user DROP COLUMN IF EXISTS password")