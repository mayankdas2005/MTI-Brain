"""SQLAlchemy ORM model for authenticated users (Keycloak OIDC)."""

import uuid
from datetime import datetime, timezone

from app.db.base import Base
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class MTIBrainUser(Base):
    """An authenticated user synced from Keycloak.

    Attributes:
        id: Internal primary key UUID.
        keycloak_sub: Keycloak subject identifier (``sub`` claim) - unique.
        email: User's primary email from Keycloak profile.
        name: Display name from Keycloak profile.
        groups: Keycloak group names the user belongs to (JSON array).
        organization: Organization name from Keycloak profile (may be empty).
        last_login: Timestamp of the user's most recent login.
        created_at: Timestamp of first login / user creation.
        threads: Threads owned by this user.
        projects: Projects owned by this user.
    """

    __tablename__ = "mti_brain_user"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    keycloak_sub: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stores a bcrypt hash in the DB column named "password".
    password_hash: Mapped[str | None] = mapped_column("password", Text, nullable=True)
    groups: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    organization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Haiku-synthesised 5-8 bullet behavioural profile; injected instead of raw feedback when fresh
    distilled_preferences: Mapped[str | None] = mapped_column(Text, nullable=True)
    distilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_count_at_distill: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    threads: Mapped[list["MTIBrainThread"]] = relationship(  # noqa: F821
        back_populates="user", lazy="noload"
    )
    projects: Mapped[list["MTIBrainProject"]] = relationship(  # noqa: F821
        back_populates="user", lazy="noload"
    )

    __table_args__ = (
        Index("ix_mti_brain_user_email", "email"),
    )
