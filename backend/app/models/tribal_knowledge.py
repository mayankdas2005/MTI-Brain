"""SQLAlchemy ORM model for tribal knowledge pgvector store.

Each row represents one .md file from the tribal knowledge corpus,
with a 1536-dim Cohere Embed v4 vector for semantic search and a
tsvector column for full-text search (GIN-indexed).
"""

import uuid
from datetime import datetime
from datetime import timezone

from app.db.base import Base
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime
from sqlalchemy import Index
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class MTIBrainTribalKnowledge(Base):
    """A single .md tribal knowledge file embedded and stored for hybrid retrieval.

    Attributes:
        id: Primary key UUID, auto-generated.
        source_file: Relative path from repo root (unique — used for upserts).
        file_name: Basename of the file (e.g. "01_cfo_meeting.md").
        folder: Subfolder / category name.
        content: Full markdown text body (frontmatter stripped).
        embedding: 1536-dim Cohere Embed v4 vector (search_document input type).
        search_vector: PostgreSQL tsvector for full-text search (GIN-indexed).
        metadata_: JSONB dict of parsed YAML frontmatter fields.
        created_at: Ingestion timestamp (UTC).
    """

    __tablename__ = "mti_brain_tribal_knowledge"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    folder: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=True)
    search_vector = mapped_column(TSVECTOR, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("source_file", name="uq_tribal_knowledge_source"),
        Index("ix_tribal_knowledge_folder", "folder"),
        Index("idx_tribal_knowledge_fts", "search_vector", postgresql_using="gin"),
    )
