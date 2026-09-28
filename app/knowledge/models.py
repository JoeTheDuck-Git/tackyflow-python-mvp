from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CrawledDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    source_type: str = "web"
    source_url: str
    title: str = ""
    content: str
    content_hash: str
    fetched_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    id: str
    document_id: str
    workspace_id: str
    chunk_index: int
    content: str
    embedding: list[float]
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    id: str
    document_id: str
    content: str
    score: float = Field(ge=0, le=1)
    source_url: str | None = None
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCaptureRecord(BaseModel):
    """Auditable provenance for every external source acquisition attempt."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    generation_id: str | None = None
    document_id: str | None = None
    source_url: str
    acquisition_method: str
    status: str = Field(pattern="^(success|failed)$")
    completeness: float = Field(default=0, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    excerpt: str = Field(default="", max_length=1200)
    failure_reason: str = Field(default="", max_length=1000)
    requires_human_review: bool = False
    fetched_at: datetime = Field(default_factory=utc_now)
