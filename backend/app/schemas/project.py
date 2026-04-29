"""Pydantic schemas for project API requests and responses."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    """Base for all request models - rejects unknown fields, strips strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateProjectRequest(_StrictRequest):
    """Request to create a new project.

    Attributes:
        name: Name of the project.
        description: Optional project description.
    """

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class UpdateProjectRequest(_StrictRequest):
    """Request to update a project.

    Attributes:
        name: New project name, or None to leave unchanged.
        description: New project description, or None to leave unchanged.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class ProjectOut(BaseModel):
    """Project summary for listing.

    Attributes:
        id: Unique project identifier.
        name: Project name.
        description: Project description, if any.
        starred: Whether the project is starred.
        thread_count: Number of threads in the project.
        created_at: When the project was created.
        updated_at: When the project was last updated.
    """

    id: uuid.UUID
    name: str
    description: str | None
    starred: bool = False
    thread_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetail(BaseModel):
    """Full project with thread list.

    Attributes:
        id: Unique project identifier.
        name: Project name.
        description: Project description, if any.
        starred: Whether the project is starred.
        threads: List of threads belonging to the project.
        created_at: When the project was created.
        updated_at: When the project was last updated.
    """

    id: uuid.UUID
    name: str
    description: str | None
    starred: bool = False
    threads: list["ThreadBrief"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ThreadBrief(BaseModel):
    """Minimal thread info within a project.

    Attributes:
        id: Unique thread identifier.
        title: Thread title, if any.
        starred: Whether the thread is starred.
        created_at: When the thread was created.
        updated_at: When the thread was last updated.
    """

    id: uuid.UUID
    title: str | None
    starred: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeleteProjectResponse(BaseModel):
    """Response after deleting a project.

    Attributes:
        deleted: Whether the deletion succeeded.
        project_id: ID of the deleted project.
    """

    deleted: bool
    project_id: uuid.UUID
