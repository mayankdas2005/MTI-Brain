"""Pydantic schemas for chat API requests and responses."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ResponseTone = Literal["consultant", "operator", "brief"]


# ─── Requests ───


class _StrictRequest(BaseModel):
    """Base for all request models - rejects unknown fields, strips strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NewChatRequest(_StrictRequest):
    """Request to create a new chat thread.

    Attributes:
        thread_id: Optional client-supplied thread ID.
        project_id: Optional project to associate the thread with.
        title: Optional initial title for the thread.
    """

    thread_id: uuid.UUID | None = Field(default=None)
    project_id: uuid.UUID | None = Field(default=None)
    title: str | None = Field(default=None)


class AskRequest(_StrictRequest):
    """Request to ask a question in a thread.

    Attributes:
        question: The user's question text.
        conversation_id: Optional conversation ID for follow-up questions.
        source_conversation_id: Optional conversation ID of the version
            that spawned this follow-up (for version-branch visibility).
        response_tone: Response style - 'consultant' (default), 'operator',
            or 'brief'.
        max_rows: Maximum result rows to return (default 100).
    """

    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = Field(default=None)
    source_conversation_id: uuid.UUID | None = Field(default=None)
    response_tone: ResponseTone = Field(default="consultant")
    max_rows: int = Field(default=100, ge=10, le=500)
    prior_sql: str | None = Field(
        default=None,
        max_length=10000,
        description="SQL from a specific prior answer the user wants to refine. "
        "Passed by the 'Refine this query' UI - lets generate_sql adapt a "
        "specific answer's SQL rather than regenerating from scratch.",
    )


class RetryRequest(_StrictRequest):
    """Retry: re-generate response for an existing conversation.

    Attributes:
        conversation_id: The conversation to retry.
        response_tone: Response style carried from user settings.
        max_rows: Maximum result rows carried from user settings.
    """

    conversation_id: uuid.UUID = Field(..., description="The conversation to retry")
    source_conversation_id: uuid.UUID | None = Field(default=None)
    response_tone: ResponseTone = Field(default="consultant")
    max_rows: int = Field(default=100, ge=10, le=500)


class EditRequest(_StrictRequest):
    """Edit: modify the question and generate a new version.

    Attributes:
        conversation_id: The conversation to edit.
        question: The edited question text.
        response_tone: Response style carried from user settings.
        max_rows: Maximum result rows carried from user settings.
    """

    conversation_id: uuid.UUID = Field(..., description="The conversation to edit")
    question: str = Field(
        ..., min_length=1, max_length=2000, description="The edited question"
    )
    source_conversation_id: uuid.UUID | None = Field(default=None)
    response_tone: ResponseTone = Field(default="consultant")
    max_rows: int = Field(default=100, ge=10, le=500)


class FeedbackRequest(_StrictRequest):
    """Request to submit feedback on a conversation.

    Attributes:
        liked: Whether the user liked the response (true=like, false=dislike).
        comment: Optional free-text comment.
    """

    liked: bool = Field(..., description="true=like, false=dislike")
    comment: str | None = Field(default=None, max_length=2000)


class RenameRequest(_StrictRequest):
    """Request to rename a thread.

    Attributes:
        title: The new title for the thread.
    """

    title: str = Field(..., min_length=1, max_length=500)


class MoveRequest(_StrictRequest):
    """Request to move a thread to a project.

    Attributes:
        project_id: Target project ID, or None to remove from project.
    """

    project_id: uuid.UUID | None = Field(
        ..., description="Target project ID, or null to remove from project"
    )


class BulkDeleteRequest(_StrictRequest):
    """Request to delete multiple threads.

    Attributes:
        thread_ids: List of thread IDs to delete.
    """

    thread_ids: list[uuid.UUID] = Field(..., min_length=1)


class BulkMoveRequest(_StrictRequest):
    """Request to move multiple threads to a project.

    Attributes:
        thread_ids: List of thread IDs to move.
        project_id: Target project ID, or None to remove from project.
    """

    thread_ids: list[uuid.UUID] = Field(..., min_length=1)
    project_id: uuid.UUID | None = Field(
        ..., description="Target project ID, or null to remove from project"
    )


# ─── Responses ───


class ThreadSummary(BaseModel):
    """Summary of a thread for listing.

    Attributes:
        id: Unique thread identifier.
        project_id: Associated project ID, if any.
        title: Thread title.
        starred: Whether the thread is starred.
        last_message: Preview of the most recent message.
        created_at: When the thread was created.
        updated_at: When the thread was last updated.
    """

    id: uuid.UUID
    project_id: uuid.UUID | None
    title: str | None
    starred: bool = False
    last_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SearchResult(BaseModel):
    """A search result matching a thread or message.

    Attributes:
        thread_id: ID of the matching thread.
        project_id: Associated project ID, if any.
        title: Thread title.
        match_type: Type of match (e.g. title, message).
        preview: Short preview of the matching content.
        headline: Highlighted headline snippet.
        rank: Relevance ranking score.
        created_at: When the thread was created.
        updated_at: When the thread was last updated.
    """

    thread_id: uuid.UUID
    project_id: uuid.UUID | None = None
    title: str | None
    match_type: str
    preview: str | None = None
    headline: str | None = None
    rank: float = 0.0
    created_at: datetime
    updated_at: datetime


class MessageFeedback(BaseModel):
    """Inline feedback attached to a message."""

    liked: bool
    comment: str | None = None


class MessageOut(BaseModel):
    """A single message in a thread.

    Attributes:
        id: Unique message identifier.
        thread_id: ID of the parent thread.
        conversation_id: ID of the conversation turn.
        parent_conversation_id: ID of the parent conversation turn, if any.
        role: Message role (e.g. user, assistant).
        content: Message body text.
        reasoning: Optional reasoning trace from the model.
        metadata_: Optional extra metadata dictionary.
        feedback: Optional feedback for this message.
        created_at: When the message was created.
    """

    id: uuid.UUID
    thread_id: uuid.UUID
    conversation_id: uuid.UUID
    parent_conversation_id: uuid.UUID | None = None
    role: str
    content: str
    reasoning: str | None = None
    metadata_: dict | None = Field(default=None, alias="metadata_")
    feedback: MessageFeedback | None = None
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class ThreadDetail(BaseModel):
    """Full thread with all messages.

    Attributes:
        id: Unique thread identifier.
        project_id: Associated project ID, if any.
        title: Thread title.
        starred: Whether the thread is starred.
        messages: Ordered list of all messages in the thread.
        created_at: When the thread was created.
        updated_at: When the thread was last updated.
    """

    id: uuid.UUID
    project_id: uuid.UUID | None
    title: str | None
    starred: bool = False
    messages: list[MessageOut]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NewChatResponse(BaseModel):
    """Response after creating a new chat thread.

    Attributes:
        thread_id: ID of the newly created thread.
        title: Title assigned to the thread, if any.
    """

    thread_id: uuid.UUID
    title: str | None


class FeedbackOut(BaseModel):
    """Response after submitting feedback.

    Attributes:
        id: Unique feedback identifier.
        conversation_id: ID of the conversation the feedback applies to.
        liked: Whether the user liked the response.
        comment: Optional free-text comment.
        created_at: When the feedback was submitted.
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    liked: bool
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeleteResponse(BaseModel):
    """Response after deleting a thread.

    Attributes:
        deleted: Whether the deletion succeeded.
        thread_id: ID of the deleted thread.
    """

    deleted: bool
    thread_id: uuid.UUID


class BulkDeleteResponse(BaseModel):
    """Response after bulk deleting threads.

    Attributes:
        deleted_count: Number of threads successfully deleted.
    """

    deleted_count: int


class BulkMoveResponse(BaseModel):
    """Response after bulk moving threads.

    Attributes:
        moved_count: Number of threads successfully moved.
        project_id: Target project ID, or None if removed from project.
    """

    moved_count: int
    project_id: uuid.UUID | None
