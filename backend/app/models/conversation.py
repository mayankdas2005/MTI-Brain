"""SQLAlchemy ORM models for conversations, threads, projects, and feedback.

Defines the persistence layer for the MTI Brain conversational interface,
including project grouping, threaded conversations, individual messages,
and user feedback with optional vector embeddings.
"""

import uuid
from datetime import datetime
from datetime import timezone

from app.db.base import Base
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship


class MTIBrainProject(Base):
    """A top-level project that groups related conversation threads.

    Attributes:
        id: Primary key UUID, auto-generated.
        user_id: Owner user UUID (FK to mti_brain_user).
        name: Human-readable project name (max 255 chars).
        description: Optional long-form project description.
        starred: Whether the user has starred/favourited the project.
        created_at: Timestamp of project creation (UTC).
        updated_at: Timestamp of last modification (UTC, auto-updated).
        user: The owning ``MTIBrainUser`` relationship.
        threads: Child ``MTIBrainThread`` instances belonging to this project.
    """

    __tablename__ = "mti_brain_project"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Full-text search vector - auto-populated by DB trigger on (name, description)
    search_vector = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["MTIBrainUser | None"] = relationship(back_populates="projects")  # noqa: F821
    threads: Mapped[list["MTIBrainThread"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_mti_brain_project_user", "user_id"),
        Index("ix_mti_brain_project_search", "search_vector", postgresql_using="gin"),
        Index(
            "ix_mti_brain_project_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_mti_brain_project_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )


class MTIBrainThread(Base):
    """A conversation thread within an optional project.

    Each thread corresponds to a single LangGraph checkpointer thread and
    contains an ordered sequence of messages.

    Attributes:
        id: Primary key UUID, also used as the LangGraph thread_id.
        user_id: Owner user UUID (FK to mti_brain_user).
        project_id: Optional FK linking the thread to a ``MTIBrainProject``.
        title: Optional short title (max 500 chars).
        starred: Whether the user has starred/favourited the thread.
        search_vector: TSVECTOR column auto-populated by a DB trigger on title.
        created_at: Timestamp of thread creation (UTC).
        updated_at: Timestamp of last modification (UTC, auto-updated).
        user: The owning ``MTIBrainUser`` relationship.
        project: Parent ``MTIBrainProject`` relationship (nullable).
        messages: Ordered child ``MTIBrainMessage`` instances.
    """

    __tablename__ = "mti_brain_thread"

    # This ID is also the thread_id used in LangGraph's checkpointer
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_project.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Full-text search vector - auto-populated by DB trigger on title
    search_vector = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["MTIBrainUser | None"] = relationship(back_populates="threads")  # noqa: F821
    project: Mapped[MTIBrainProject | None] = relationship(back_populates="threads")
    messages: Mapped[list["MTIBrainMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="MTIBrainMessage.created_at",
    )

    __table_args__ = (
        Index("ix_mti_brain_thread_updated", "updated_at"),
        Index("ix_mti_brain_thread_project", "project_id"),
        Index("ix_mti_brain_thread_user", "user_id"),
        Index("ix_mti_brain_thread_search", "search_vector", postgresql_using="gin"),
        Index("ix_mti_brain_thread_user_updated", "user_id", "updated_at"),
        Index("ix_mti_brain_thread_starred", "starred"),
        Index(
            "ix_mti_brain_thread_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )


class MTIBrainMessage(Base):
    """A single user or assistant message within a thread.

    Messages are grouped into question/response pairs via
    ``conversation_id`` and support retry/edit chains through
    ``parent_conversation_id``.

    Attributes:
        id: Primary key UUID, auto-generated.
        thread_id: FK to the owning ``MTIBrainThread``.
        conversation_id: Groups a question and its response together.
        parent_conversation_id: Links retries/edits to the original
            conversation; ``None`` for the first version.
        role: Message author role (``'user'`` or ``'assistant'``).
        content: Full text content of the message.
        reasoning: Optional model reasoning/chain-of-thought text.
        metadata_: JSONB column storing sql, chart_spec, intent,
            resolved_filters, columns, rows, row_count, and follow_ups.
        search_vector: TSVECTOR column auto-populated by a DB trigger on
            content.
        created_at: Timestamp of message creation (UTC).
        thread: Parent ``MTIBrainThread`` relationship.
        feedback: Child ``MTIBrainFeedback`` instances for this message.
    """

    __tablename__ = "mti_brain_message"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    # conversation_id groups a question + response pair together
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    # Links retries/edits to the original conversation (null for first version)
    parent_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stores: sql, chart_spec, intent, resolved_filters, columns, rows, row_count, follow_ups
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # Full-text search vector - auto-populated by DB trigger on content
    search_vector = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    thread: Mapped[MTIBrainThread] = relationship(back_populates="messages")
    feedback: Mapped[list["MTIBrainFeedback"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_mti_brain_message_thread", "thread_id"),
        Index("ix_mti_brain_message_conversation", "conversation_id"),
        Index("ix_mti_brain_message_created", "created_at"),
        Index("ix_mti_brain_message_search", "search_vector", postgresql_using="gin"),
        Index("ix_mti_brain_message_thread_created", "thread_id", "created_at"),
        Index("ix_mti_brain_message_role", "role"),
        Index("ix_mti_brain_message_thread_role", "thread_id", "role"),
        Index(
            "ix_mti_brain_message_parent_conversation",
            "parent_conversation_id",
            postgresql_where="parent_conversation_id IS NOT NULL",
        ),
        # Added in migration 0004
        Index("ix_mti_brain_message_thread_conversation", "thread_id", "conversation_id"),
        # Added in migration 0005
        Index("ix_mti_brain_message_conversation_role", "conversation_id", "role"),
        Index("ix_mti_brain_message_thread_role_created", "thread_id", "role", "created_at"),
        Index(
            "ix_mti_brain_message_stopped",
            sa_text("((metadata->>'stopped')::boolean)"),
            postgresql_where="role = 'assistant'",
        ),
    )


class MTIBrainFeedback(Base):
    """User feedback (like/dislike and optional comment) on a message.

    Stores an optional pgvector embedding of the question plus feedback
    text to enable similarity-based retrieval of past feedback.

    Attributes:
        id: Primary key UUID, auto-generated.
        message_id: Optional FK to the ``MTIBrainMessage`` being rated.
        thread_id: FK to the owning ``MTIBrainThread``.
        liked: ``True`` for like, ``False`` for dislike, ``None`` if unset.
        comment: Optional free-text feedback comment.
        embedding: 1536-dimensional pgvector embedding for similarity search.
        created_at: Timestamp of feedback creation (UTC).
        message: Parent ``MTIBrainMessage`` relationship (nullable).
    """

    __tablename__ = "mti_brain_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_message.id", ondelete="CASCADE"),
        nullable=True,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    liked: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )  # true=like, false=dislike
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalised question text — avoids JOIN at retrieval time; populated on save
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FTS-searchable intent fingerprint built from directive_writer intent_fingerprint dict
    intent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'answer' | 'sql' | 'chart' | 'general' — captured from frontend widget
    feedback_type: Mapped[str] = mapped_column(String(16), nullable=False, default="general")
    # Tracking: when was this feedback last retrieved and applied to a query
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pgvector embedding of question for vector similarity search
    embedding = mapped_column(Vector(1536), nullable=True)
    # tsvector over question_text + comment + intent_text for FTS; maintained by DB trigger
    search_vector = mapped_column(TSVECTOR, nullable=True)
    # anchor tables from the pipeline run — enables table-based cross-thread retrieval (late pass)
    tables_used: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    message: Mapped[MTIBrainMessage | None] = relationship(back_populates="feedback")

    __table_args__ = (
        Index("ix_mti_brain_feedback_thread", "thread_id"),
        Index("ix_mti_brain_feedback_message", "message_id"),
        Index("ix_mti_brain_feedback_created", "created_at"),
        Index("ix_mti_brain_feedback_message_created", "message_id", "created_at"),
        Index("idx_mti_brain_feedback_fts", "search_vector", postgresql_using="gin"),
        Index("idx_mti_brain_feedback_tables_used", "tables_used", postgresql_using="gin"),
    )


class MTIBrainDashboard(Base):
    """Tracks generated HTML dashboards stored in S3.

    One row per conversation_id. Status progresses: pending → ready | failed.
    Deleted automatically when the parent thread is deleted (CASCADE).
    """

    __tablename__ = "mti_brain_dashboard"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    s3_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_mti_brain_dashboard_thread",       "thread_id"),
        Index("ix_mti_brain_dashboard_conversation", "conversation_id"),
        Index("ix_mti_brain_dashboard_user",         "user_id"),
    )


class MTIBrainGraphContext(Base):
    """Tracks generated knowledge-graph HTML visualizations stored in S3.

    One row per conversation_id. Status progresses: pending → ready | failed.
    Deleted automatically when the parent thread is deleted (CASCADE).
    """

    __tablename__ = "mti_brain_graph_context"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    s3_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_mti_brain_graph_context_thread",       "thread_id"),
        Index("ix_mti_brain_graph_context_conversation", "conversation_id"),
        Index("ix_mti_brain_graph_context_user",         "user_id"),
    )
