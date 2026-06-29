"""Create mti_brain_user_instructions table and seed default ACRONYM instruction for existing users.

Revision ID: 0014_user_instructions
Revises: 0013_tribal_knowledge_table
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014_user_instructions"
down_revision: Union[str, Sequence[str], None] = "0013_tribal_knowledge_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACRONYM_TITLE = "Acronym Glossary"
_ACRONYM_CONTENT = (
    "At the end of every response, append a glossary table for any domain-specific or "
    "non-obvious acronyms used in the body. Omit universally known terms (USD, EUR, KPI, SQL, API). "
    "Include treasury, finance, banking, and system acronyms (e.g. ACH, FX, SLA, GL, AP, AR, "
    "KRW, MTM, LGD, PD, EAD, WCF, SCC, RCF, LOC, TMS, ERP, SWIFT, SEPA, RTGS). "
    "Format as a markdown table with columns Acronym and Full Form. "
    "Only include acronyms that actually appear in this response. "
    "If no domain-specific acronyms were used, omit the table entirely."
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mti_brain_user_instructions (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            user_id     UUID        NOT NULL REFERENCES mti_brain_user(id) ON DELETE CASCADE,
            title       VARCHAR(255) NOT NULL,
            content     TEXT        NOT NULL,
            enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
            scope       VARCHAR(32) NOT NULL DEFAULT 'all',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_mti_brain_user_instructions PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_mti_brain_user_instructions_user_id ON mti_brain_user_instructions (user_id)
    """)
    op.execute(f"""
        INSERT INTO mti_brain_user_instructions (user_id, title, content, enabled, scope)
        SELECT id, '{_ACRONYM_TITLE}', $BODY${_ACRONYM_CONTENT}$BODY$, TRUE, 'all'
        FROM mti_brain_user
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mti_brain_user_instructions_user_id")
    op.execute("DROP TABLE IF EXISTS mti_brain_user_instructions")
