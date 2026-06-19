"""Create mti_brain_tribal_knowledge table for pgvector semantic retrieval.

Stores embedded .md tribal knowledge files (policy docs, meeting notes, etc.)
with both a vector(1536) column for semantic search and a tsvector column for
full-text search. Hybrid retrieval uses both paths with RRF merge.

Revision ID: 0013_tribal_knowledge_table
Revises: 0012_execution_log_redesign
Create Date: 2026-06-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0013_tribal_knowledge_table"
down_revision: Union[str, Sequence[str], None] = "0012_execution_log_redesign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mti_brain_tribal_knowledge (
            id           UUID        NOT NULL DEFAULT gen_random_uuid(),
            source_file  TEXT        NOT NULL,
            file_name    TEXT        NOT NULL,
            folder       TEXT        NOT NULL,
            content      TEXT        NOT NULL,
            embedding    vector(1536),
            search_vector TSVECTOR,
            metadata     JSONB       NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_tribal_knowledge PRIMARY KEY (id),
            CONSTRAINT uq_tribal_knowledge_source UNIQUE (source_file)
        )
    """)
    op.execute("""
        CREATE INDEX idx_tribal_knowledge_embedding
        ON mti_brain_tribal_knowledge
        USING hnsw (embedding vector_cosine_ops)
    """)
    op.execute("""
        CREATE INDEX idx_tribal_knowledge_fts
        ON mti_brain_tribal_knowledge
        USING GIN (search_vector)
    """)
    op.execute("""
        CREATE INDEX ix_tribal_knowledge_folder
        ON mti_brain_tribal_knowledge (folder)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tribal_knowledge_folder")
    op.execute("DROP INDEX IF EXISTS idx_tribal_knowledge_fts")
    op.execute("DROP INDEX IF EXISTS idx_tribal_knowledge_embedding")
    op.execute("DROP TABLE IF EXISTS mti_brain_tribal_knowledge")
