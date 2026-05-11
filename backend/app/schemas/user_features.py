"""Pydantic schemas for Playbook, Pinned Metrics, and Thread Labels."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ─── Saved Queries (Playbook) ───

class SavedQueryCreate(_StrictRequest):
    name: str = Field(..., min_length=1, max_length=255)
    query_text: str = Field(..., min_length=1, max_length=10000)


class SavedQueryUpdate(_StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    query_text: str | None = Field(default=None, min_length=1, max_length=10000)


class SavedQueryOut(BaseModel):
    id: uuid.UUID
    name: str
    query_text: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Pinned Metrics ───

class PinnedMetricCreate(_StrictRequest):
    label: str = Field(..., min_length=1, max_length=255)
    source_query: str = Field(..., min_length=1, max_length=10000)
    position: int = Field(default=0, ge=0)


class PinnedMetricUpdate(_StrictRequest):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    position: int | None = Field(default=None, ge=0)


class PinnedMetricOut(BaseModel):
    id: uuid.UUID
    label: str
    source_query: str
    position: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Thread Labels ───

class ThreadLabelCreate(_StrictRequest):
    label: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="blue", max_length=20)


class ThreadLabelOut(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    label: str
    color: str
    created_at: datetime

    model_config = {"from_attributes": True}
