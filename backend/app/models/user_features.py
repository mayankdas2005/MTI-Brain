"""User-specific feature models: saved queries, pinned metrics, thread labels."""

import uuid
from datetime import datetime, timezone

from app.db.base import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class UserSavedQuery(Base):
    """A saved query template (Playbook entry) belonging to a user.

    Attributes:
        id: Primary key UUID.
        user_id: Owning user (FK → mti_brain_user).
        name: Display name shown in Playbook (max 255 chars).
        query_text: The full query text to execute when run.
        created_at: UTC creation timestamp.
        updated_at: UTC last-modified timestamp (auto-updated).
    """

    __tablename__ = "mti_brain_saved_query"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["MTIBrainUser | None"] = relationship()  # noqa: F821


class UserPinnedMetric(Base):
    """A metric pinned to the home page dashboard by a user.

    Attributes:
        id: Primary key UUID.
        user_id: Owning user (FK → mti_brain_user).
        label: Human-readable metric name shown on the card.
        source_query: The query text to execute when refreshing the card.
        position: Display order (ascending = left-to-right, top-to-bottom).
        created_at: UTC creation timestamp.
        updated_at: UTC last-modified timestamp.
    """

    __tablename__ = "mti_brain_pinned_metric"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_query: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["MTIBrainUser | None"] = relationship()  # noqa: F821


class ThreadLabel(Base):
    """A colored label applied to a thread by a user.

    Attributes:
        id: Primary key UUID.
        thread_id: Tagged thread (FK → mti_brain_thread).
        user_id: User who applied the label (FK → mti_brain_user).
        label: Label name (max 100 chars).
        color: CSS color identifier from a fixed palette (e.g. 'red', 'blue').
        created_at: UTC creation timestamp.
    """

    __tablename__ = "mti_brain_thread_label"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_thread.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mti_brain_user.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="blue")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
