"""SQLAlchemy ORM model for the execution log.

Captures every pipeline run outcome - question, SQL, schema, timing,
retry count, and whether a saved pattern influenced the query. This
table is the foundation for the anti-degradation monitor (Phase 2)
and implicit feedback detection (Phase 3).
"""

import uuid
from datetime import datetime, timezone

from app.db.base import Base
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column


class MTIBrainExecutionLog(Base):
    """One row per pipeline run (data_query type only).

    Attributes:
        id: Primary key UUID.
        thread_id: Conversation thread (matches LangGraph thread_id).
        question: The user's natural-language question.
        question_type: Classifier output (data_query, general_chat, rejected).
        schema_fqn: Chosen schema (e.g. ``datawarehouse.dw_ecard_app``).
        tables_used: Tables referenced in the final SQL.
        sql: The final SQL that was executed.
        row_count: Number of rows returned.
        retry_count: How many fix_query retries were needed (0 = first-try).
        fix_query_count: Number of fix_query node invocations.
        valid: Whether validate_results said the output was correct.
        exec_error: Execution error message, if any.
        pattern_matched: Whether a QueryPattern influenced this query.
        pattern_name: Which pattern was matched, if any.
        duration_ms: Total pipeline duration in milliseconds.
        implicit_positive: Set true when the next message is a follow-up.
        implicit_negative: Set true when the next message is a rephrase.
        liked: Explicit feedback (true=thumbs up, false=thumbs down, null=none).
        created_at: When this log entry was created.
    """

    __tablename__ = "mti_brain_execution_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="data_query"
    )
    schema_fqn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tables_used: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fix_query_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    exec_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_matched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pattern_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Phase 3: implicit feedback (updated async after next user message)
    implicit_positive: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    implicit_negative: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    # Explicit feedback (updated async when user clicks thumbs up/down)
    liked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # User context
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_tone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    max_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether this was an edit or retry (not a fresh question)
    is_retry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_exec_log_thread", "thread_id"),
        Index("ix_exec_log_schema", "schema_fqn"),
        Index("ix_exec_log_created", "created_at"),
        Index("ix_exec_log_pattern", "pattern_matched"),
    )
